"""Tests for TOC validation soft-gate wired into toc_extractor.run.

Cases:
  (a) validate_toc returns mismatch → set_status("toc_review") + entries persisted + toc_review SSE
  (b) validate_toc returns verified → set_status("toc_ready")
  (c) validate_toc returns skipped  → set_status("toc_ready") + set_toc_validation still called
  (d) toc_validation_enabled=False  → validate_toc NOT called, toc_ready, set_toc_validation NOT called
"""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import toc_extractor


# ── Shared fake infrastructure (mirrors test_toc_extractor.py) ───────────────

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        pass


_DEFAULT_LAUNCH_DEFAULTS = SimpleNamespace(
    extract_provider="gemini",
    extract_model="gemini-2.5-flash",
    toc_transport="cli",
)

_FAKE_ENTRY = SimpleNamespace(section_title="Lesson 1", page_start=None, page_end=None)


def _fake_toc_entry_out():
    return SimpleNamespace(model_dump=lambda mode=None: {"section_title": "Lesson 1"})


def _patch_common(monkeypatch):
    """Patch repos, bus, SessionLocal — same style as test_toc_extractor.py.

    Returns (statuses, toc_validations, bulk_calls, events).
    """
    statuses: list[tuple[str, str | None]] = []
    toc_validations: list[tuple] = []  # (verdict, detail)
    bulk_calls: list = []
    events: list[tuple[str, dict]] = []  # (event_name, data)

    async def fake_set_status(session, book_id, status, error_message=None):
        statuses.append((status, error_message))

    async def fake_set_toc_validation(session, book_id, verdict, detail):
        toc_validations.append((verdict, detail))

    async def fake_bulk_create(session, book_id, entries):
        bulk_calls.append(list(entries))
        return list(entries)

    async def fake_delete_for_book(session, book_id):
        return 0

    async def fake_publish(rid, ev, data):
        events.append((ev, data))

    async def fake_close(rid):
        pass

    async def fake_get_launch_defaults(session):
        return _DEFAULT_LAUNCH_DEFAULTS

    monkeypatch.setattr(toc_extractor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(toc_extractor.books_repo, "set_status", fake_set_status)
    monkeypatch.setattr(toc_extractor.books_repo, "set_toc_validation", fake_set_toc_validation)
    monkeypatch.setattr(toc_extractor.toc_repo, "bulk_create", fake_bulk_create)
    monkeypatch.setattr(toc_extractor.toc_repo, "delete_for_book", fake_delete_for_book)
    monkeypatch.setattr(toc_extractor.events_bus, "publish", fake_publish)
    monkeypatch.setattr(toc_extractor.events_bus, "close", fake_close)
    monkeypatch.setattr(toc_extractor.launch_defaults_repo, "get", fake_get_launch_defaults)
    # TOCEntryOut.model_validate — return a simple namespace with model_dump
    monkeypatch.setattr(
        toc_extractor.TOCEntryOut,
        "model_validate",
        classmethod(lambda cls, r: _fake_toc_entry_out()),
    )
    return statuses, toc_validations, bulk_calls, events


def _fake_extract_toc_one_entry():
    """Returns an extractor result with one entry (non-empty → passes 0-entry guard)."""
    async def _inner(**kw):
        return SimpleNamespace(entries=[_FAKE_ENTRY])
    return _inner


# ── (a) mismatch → toc_review ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mismatch_sets_toc_review_status(monkeypatch):
    """mismatch verdict must flip book to toc_review, not toc_ready."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(
            status="mismatch",
            confidence="high",
            issues=["Lesson 5 not found on page 42"],
            detail="Lesson 5 not found on page 42",
        )

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    # Final status must be exactly "toc_review" — not "toc_ready"
    final_status = statuses[-1][0]
    assert final_status == "toc_review", (
        f"mismatch verdict must produce status='toc_review', got {final_status!r}"
    )
    assert "toc_ready" not in [s for s, _ in statuses], (
        "toc_ready must NOT appear in statuses when verdict=mismatch"
    )


@pytest.mark.asyncio
async def test_mismatch_entries_still_persisted(monkeypatch):
    """Even on mismatch the TOC entries must be written to DB."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="mismatch", confidence="low", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert len(bulk_calls) == 1, "bulk_create must be called exactly once even on mismatch"
    assert bulk_calls[0] == [_FAKE_ENTRY]


@pytest.mark.asyncio
async def test_mismatch_publishes_toc_review_sse(monkeypatch):
    """mismatch must emit a 'toc_review' SSE event with entries + validation payload."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    issues = ["page 10 missing"]

    async def fake_validate_toc(**kw):
        return SimpleNamespace(
            status="mismatch",
            confidence="medium",
            issues=issues,
            detail="page 10 missing",
        )

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    event_names = [ev for ev, _ in events]
    assert "toc_review" in event_names, f"Expected 'toc_review' SSE event, got {event_names}"
    assert "toc_ready" not in event_names, "toc_ready SSE must NOT be emitted on mismatch"

    toc_review_data = next(data for ev, data in events if ev == "toc_review")
    assert "entries" in toc_review_data
    assert "validation" in toc_review_data
    assert toc_review_data["validation"]["verdict"] == "mismatch"
    assert toc_review_data["validation"]["issues"] == issues


@pytest.mark.asyncio
async def test_mismatch_writes_toc_validation_row(monkeypatch):
    """mismatch must call set_toc_validation with ('mismatch', detail)."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(
            status="mismatch", confidence="high", issues=["x"], detail="x"
        )

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert len(toc_validations) == 1, "set_toc_validation must be called once"
    verdict, detail = toc_validations[0]
    assert verdict == "mismatch"
    assert detail == "x"


# ── (b) verified → toc_ready ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verified_sets_toc_ready_status(monkeypatch):
    """verified verdict must keep book at toc_ready."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="verified", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    final_status = statuses[-1][0]
    assert final_status == "toc_ready", (
        f"verified verdict must produce status='toc_ready', got {final_status!r}"
    )
    assert "toc_review" not in [s for s, _ in statuses]


@pytest.mark.asyncio
async def test_verified_publishes_toc_ready_sse(monkeypatch):
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="verified", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    event_names = [ev for ev, _ in events]
    assert "toc_ready" in event_names
    assert "toc_review" not in event_names


# ── (c) skipped → toc_ready, set_toc_validation still called ─────────────────

@pytest.mark.asyncio
async def test_skipped_sets_toc_ready_status(monkeypatch):
    """skipped verdict (validator couldn't run) → toc_ready, not toc_review."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="skipped", confidence=None, issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    final_status = statuses[-1][0]
    assert final_status == "toc_ready", (
        f"skipped verdict must produce status='toc_ready', got {final_status!r}"
    )


@pytest.mark.asyncio
async def test_skipped_still_writes_toc_validation_row(monkeypatch):
    """skipped must still call set_toc_validation — 'skipped' is distinct from NULL (disabled)."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="skipped", confidence=None, issues=[], detail="no window")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert len(toc_validations) == 1, "set_toc_validation must be called even when verdict=skipped"
    verdict, detail = toc_validations[0]
    assert verdict == "skipped"


# ── (d) disabled → validate_toc NOT called, toc_ready, set_toc_validation NOT called ──

@pytest.mark.asyncio
async def test_disabled_skips_validate_toc(monkeypatch):
    """When toc_validation_enabled=False, agent.validate_toc must NEVER be called."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    validate_called = []

    async def fake_validate_toc(**kw):
        validate_called.append(True)
        return SimpleNamespace(status="mismatch", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    # Disable the gate
    import app.config as _config
    monkeypatch.setattr(_config.settings, "toc_validation_enabled", False)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert validate_called == [], (
        "validate_toc must NOT be called when toc_validation_enabled=False"
    )


@pytest.mark.asyncio
async def test_disabled_sets_toc_ready(monkeypatch):
    """When disabled, the final status must still be toc_ready (no regression)."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="mismatch", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    import app.config as _config
    monkeypatch.setattr(_config.settings, "toc_validation_enabled", False)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    final_status = statuses[-1][0]
    assert final_status == "toc_ready", (
        f"disabled validation must not change status from toc_ready, got {final_status!r}"
    )


@pytest.mark.asyncio
async def test_disabled_does_not_write_toc_validation_row(monkeypatch):
    """When disabled, set_toc_validation must NOT be called (NULL = 'not validated')."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="mismatch", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    import app.config as _config
    monkeypatch.setattr(_config.settings, "toc_validation_enabled", False)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert toc_validations == [], (
        "set_toc_validation must NOT be called when toc_validation_enabled=False"
    )


@pytest.mark.asyncio
async def test_disabled_publishes_toc_ready_sse(monkeypatch):
    """When disabled, the SSE event must be toc_ready (existing behavior unchanged)."""
    statuses, toc_validations, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_validate_toc(**kw):
        return SimpleNamespace(status="mismatch", confidence="high", issues=[], detail="")

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", _fake_extract_toc_one_entry())
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)

    import app.config as _config
    monkeypatch.setattr(_config.settings, "toc_validation_enabled", False)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    event_names = [ev for ev, _ in events]
    assert "toc_ready" in event_names
    assert "toc_review" not in event_names
