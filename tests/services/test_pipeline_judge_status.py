"""Tests for judge-block logic in _execute_phase:
  - retry-once on unavailable
  - capped regen loop (bounded by settings.max_judge_regens)
  - post-regen re-check
  - judge_status recorded in phase_repo.set_status

Stubs _judge_with_timeout and _run_with_failover; captures judge_status passed
to phase_repo.set_status via a spy.  asyncio_mode=auto (pyproject.toml).
"""
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.services import pipeline
from app.services.phase_judge import JudgeOutcome
from app.config import settings as _settings


# ---------------------------------------------------------------------------
# Helpers to build lightweight fake outcomes
# ---------------------------------------------------------------------------

def _ok() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)


def _major() -> JudgeOutcome:
    return JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: content issue"], feedback="fix this", has_major=True
    )


def _minor() -> JudgeOutcome:
    return JudgeOutcome(
        available=True, passed=False, warnings=["MINOR: style"], feedback="", has_major=False
    )


def _unavail() -> JudgeOutcome:
    return JudgeOutcome(
        available=False, passed=True, warnings=["judge-unavailable: TimeoutError"], feedback=""
    )


# ---------------------------------------------------------------------------
# Shared kwargs for _execute_phase (non-extract phase)
# ---------------------------------------------------------------------------

def _make_kwargs(
    phase_name: str = "preview",
    provider: str = "claude",
    model: Optional[str] = None,
) -> dict:
    return dict(
        job_id=uuid.uuid4(),
        phase_name=phase_name,
        phase_order=1,
        subject="math",
        provider=provider,
        model=model,
        pdf_path=Path("/fake/book.pdf"),
        attach_file=False,
        section={"title": "Algebra", "number": "1.1", "page_start": 1, "page_end": 5, "id": uuid.uuid4()},
        lesson_context="some context",
        prior_outputs={},
        difficulty=None,
        source_map_digest="abc123",
        transport="cli",
        extract_transport="cli",
        judge_transport="cli",
        judge_provider_ov=None,
        judge_model_ov=None,
        extract_provider="gemini",
        extract_model=None,
    )


# ---------------------------------------------------------------------------
# Fixture: patch away all I/O boundaries
# ---------------------------------------------------------------------------

@pytest.fixture()
def patch_io(monkeypatch):
    """Patch all DB and agent I/O so _execute_phase can run without a real DB.

    Captures the judge_status value passed to phase_repo.set_status's final
    'done' call and exposes it on the returned namespace.
    """
    import types
    ns = types.SimpleNamespace(judge_status=None, set_status_calls=[])

    # ---- phase_repo --------------------------------------------------------
    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        ns.set_status_calls.append((status, kw))
        if status == "done":
            ns.judge_status = kw.get("judge_status")

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)

    # ---- jobs_repo ---------------------------------------------------------
    async def fake_jobs_set_status(session, job_id, status, **kw):
        pass
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", fake_jobs_set_status)

    # ---- SessionLocal ------------------------------------------------------
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    # ---- _run_with_failover (generation) -----------------------------------
    # Returns (output_md, tin, tout, produced_by); tests can override per-call
    ns.failover_outputs = [("# generated output", 100, 50, "claude")]

    async def fake_failover(*, requested_provider, model, run_fn, transport):
        return ns.failover_outputs.pop(0)

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    # ---- get_prompt / get_prompt_hash -------------------------------------
    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase: "base prompt text")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase: "deadbeef" * 8)

    # ---- model_tiers.resolve_judge ----------------------------------------
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))

    return ns


# ===========================================================================
# Test (i): clean pass → judge_status = "ok"
# ===========================================================================

async def test_judge_status_clean_pass(monkeypatch, patch_io):
    """Phase passes the judge on the first call → judge_status='ok'."""
    calls = []

    async def fake_judge(**kw):
        calls.append("judge")
        return _ok()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "ok", f"got {patch_io.judge_status!r}"
    assert calls.count("judge") == 1, f"expected 1 judge call, got {len(calls)}"


# ===========================================================================
# Test (ii): MAJOR → regen → clean → judge_status = "ok"
# ===========================================================================

async def test_judge_status_major_regen_then_ok(monkeypatch, patch_io):
    """First judge=MAJOR → regen → second judge=ok → judge_status='ok'."""
    # Regen generation output
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),    # initial generation
        ("# regenned output", 110, 55, "claude"),   # regen generation
    ]
    judge_responses = [_major(), _ok()]

    async def fake_judge(**kw):
        return judge_responses.pop(0)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)
    monkeypatch.setattr(_settings, "max_judge_regens", 1)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "ok", f"got {patch_io.judge_status!r}"
    assert judge_responses == [], "both judge calls should have been consumed"


# ===========================================================================
# Test (iii): MAJOR → regen → still MAJOR → judge_status = "major_shipped"
# ===========================================================================

async def test_judge_status_major_regen_still_major(monkeypatch, patch_io):
    """MAJOR → regen → still MAJOR → budget exhausted → judge_status='major_shipped'."""
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# regenned output", 110, 55, "claude"),
    ]
    judge_responses = [_major(), _major()]

    async def fake_judge(**kw):
        return judge_responses.pop(0)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)
    monkeypatch.setattr(_settings, "max_judge_regens", 1)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "major_shipped", f"got {patch_io.judge_status!r}"
    assert judge_responses == [], "both judge calls should have been consumed"


# ===========================================================================
# Test (iv): initial unavailable → retried once → still unavailable → "unavailable"
# ===========================================================================

async def test_judge_status_unavailable_retry_once(monkeypatch, patch_io):
    """If the initial judge call is unavailable, it is retried exactly once.
    If still unavailable, judge_status='unavailable'."""
    calls = []

    async def fake_judge(**kw):
        calls.append("judge")
        return _unavail()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "unavailable", f"got {patch_io.judge_status!r}"
    # Must be retried exactly once: 2 calls total
    assert len(calls) == 2, f"expected 2 judge calls (initial + 1 retry), got {len(calls)}"


# ===========================================================================
# Test (v): max_judge_regens=0 → MAJOR recorded "major_shipped", zero regen calls
# ===========================================================================

async def test_judge_status_no_regen_budget(monkeypatch, patch_io):
    """With max_judge_regens=0, a MAJOR outcome is immediately major_shipped
    with NO regen generation call."""
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        # If regen is called unexpectedly, popping an empty list will raise IndexError
    ]
    judge_calls = []

    async def fake_judge(**kw):
        judge_calls.append("judge")
        return _major()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)
    monkeypatch.setattr(_settings, "max_judge_regens", 0)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "major_shipped", f"got {patch_io.judge_status!r}"
    # Only the initial judge call; no regen judge call
    assert len(judge_calls) == 1, f"expected 1 judge call (no regen budget), got {len(judge_calls)}"
    # failover_outputs still has regen entry → confirms regen generation was NOT called
    assert len(patch_io.failover_outputs) == 0, "initial generation was consumed, no regen"


# ===========================================================================
# Test (vi): regen raises non-auth → soft-degrade → judge_status="major_regen_failed"
# ===========================================================================

async def test_judge_status_regen_raises_non_auth(monkeypatch, patch_io):
    """If regen generation raises a non-auth error, the original output is kept
    and judge_status='major_regen_failed'."""
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        # second call for regen will raise
    ]
    judge_calls = []

    async def fake_judge(**kw):
        judge_calls.append("judge")
        return _major()

    async def failing_failover(*, requested_provider, model, run_fn, transport):
        if len(patch_io.failover_outputs) == 0:
            raise RuntimeError("network error during regen")
        return patch_io.failover_outputs.pop(0)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)
    monkeypatch.setattr(pipeline, "_run_with_failover", failing_failover)
    monkeypatch.setattr(_settings, "max_judge_regens", 1)

    kw = _make_kwargs()
    # Should NOT raise; soft-degrade keeps original output
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "major_regen_failed", f"got {patch_io.judge_status!r}"
    # Only the initial judge call fired; no post-regen judge
    assert len(judge_calls) == 1, f"expected 1 judge call, got {len(judge_calls)}"


def _refused() -> JudgeOutcome:
    return JudgeOutcome(
        available=False, refused=True, passed=True,
        warnings=["judge-refused: content policy"], feedback="",
    )


async def test_judge_status_refused_skips_retry_once(monkeypatch, patch_io):
    """A refusal is recorded as judge_status='refused' and is NOT retried (unlike a
    transient unavailable, which is retried once)."""
    calls = []

    async def fake_judge(**kw):
        calls.append("judge")
        return _refused()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "refused", f"got {patch_io.judge_status!r}"
    assert len(calls) == 1, f"refusal must not be retried; got {len(calls)} judge calls"
