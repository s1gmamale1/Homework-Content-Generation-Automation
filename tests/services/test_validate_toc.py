"""Unit tests for ``agent.validate_toc``.

Mocking strategy (mirrors ``tests/services/test_agent.py``):
- ``app.services.agent._spawn`` — patched to return canned (rc, text, usage, stderr).
- ``app.services.agent._record_usage`` — patched to capture kwargs without a DB.
- ``app.services.agent._toc_source_pdf`` — patched to return a real temp file
  (spawn-path tests) or None (no-window test).

All tests are async (``pytest.mark.asyncio``), no DB required.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.services import agent as agent_module
from app.services.agent import validate_toc
from app.schemas.toc import TOCEntryExtracted

BOOK_ID = UUID("00000000-0000-0000-0000-000000000001")

_ENTRIES: list[TOCEntryExtracted] = [
    TOCEntryExtracted(
        section_number="1.1",
        section_title="Introduction",
        page_start=5,
        page_end=10,
    ),
    TOCEntryExtracted(
        section_number="1.2",
        section_title="History",
        page_start=11,
        page_end=20,
    ),
]


def _make_usage(*, prompt: int = 100, output: int = 20) -> dict[str, Any]:
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cached_tokens": 0,
        "total_tokens": prompt + output,
        "raw": {},
    }


def _verified_json() -> str:
    return json.dumps({"verdict": "verified", "confidence": "high", "issues": []})


def _mismatch_json(issues: list[str] | None = None) -> str:
    return json.dumps(
        {
            "verdict": "mismatch",
            "confidence": "medium",
            "issues": issues or ["Section 3 missing", "Page numbers off"],
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _make_fake_window(tmp_path: Path) -> Path:
    """Return a real temp file so window.unlink() succeeds in the function."""
    p = tmp_path / "toc_window.pdf"
    p.write_bytes(b"%PDF-stub")
    return p


# ─────────────────────────────────────────────────────────────────────
# (a) verified verdict → status "verified"
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A clean gemini response with verdict='verified' maps to status='verified'."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    async def fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
        return (0, _verified_json(), _make_usage(), "")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model="gemini-2.5-flash",
        transport="cli",
    )

    assert result.status == "verified"
    assert result.confidence == "high"
    assert result.issues == []
    assert len(record_calls) == 1
    assert record_calls[0]["success"] is True
    assert record_calls[0]["operation"] == "toc.validate"


# ─────────────────────────────────────────────────────────────────────
# (b) mismatch verdict with issues → status "mismatch", issues surfaced
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A mismatch verdict maps to status='mismatch' and issues are returned."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    issues = ["Section 3 missing", "Page numbers off"]

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    async def fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
        return (0, _mismatch_json(issues), _make_usage(), "")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model="gemini-2.5-flash",
        transport="cli",
    )

    assert result.status == "mismatch"
    assert result.confidence == "medium"
    assert result.issues == issues
    assert "Section 3 missing" in result.detail
    assert len(record_calls) == 1
    assert record_calls[0]["success"] is True
    assert record_calls[0]["operation"] == "toc.validate"


# ─────────────────────────────────────────────────────────────────────
# (c) _spawn raises → "skipped"
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_spawn_raises_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """If _spawn raises, validate_toc must return skipped (never raise)."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    async def fake_spawn(**_kwargs: Any) -> None:
        raise RuntimeError("connection refused")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model=None,
        transport="cli",
    )

    assert result.status == "skipped"
    assert result.confidence is None
    assert result.issues == []
    # usage must be recorded as failure
    assert len(record_calls) == 1
    assert record_calls[0]["success"] is False
    assert record_calls[0]["operation"] == "toc.validate"


# ─────────────────────────────────────────────────────────────────────
# (d) _spawn returns rc != 0 → "skipped"
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_nonzero_rc_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A non-zero exit code from the CLI is caught and returns skipped."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    async def fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
        return (1, "", _make_usage(prompt=0, output=0), "CLI crashed")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model=None,
        transport="cli",
    )

    assert result.status == "skipped"
    assert len(record_calls) == 1
    assert record_calls[0]["success"] is False


# ─────────────────────────────────────────────────────────────────────
# (e) bad/non-JSON text → "skipped"
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_bad_json_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Invalid JSON output from the model must degrade to skipped, not raise."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    async def fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
        return (0, "Sorry, I cannot assess that.", _make_usage(), "")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model=None,
        transport="cli",
    )

    assert result.status == "skipped"
    assert result.confidence is None
    # A usage record should still be written as failure
    assert len(record_calls) == 1
    assert record_calls[0]["success"] is False


# ─────────────────────────────────────────────────────────────────────
# (f) _toc_source_pdf returns None → skipped AND _spawn NOT called
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_no_window_skipped_no_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """When _toc_source_pdf returns None the function must return skipped
    immediately — _spawn must NOT be called."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: None)

    spawn_called = False

    async def fake_spawn(**_kwargs: Any) -> tuple:
        nonlocal spawn_called
        spawn_called = True
        return (0, _verified_json(), _make_usage(), "")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model=None,
        transport="cli",
    )

    assert result.status == "skipped"
    assert result.detail == "no contents-page window"
    assert not spawn_called, "_spawn must NOT be called when window is None"
    # No usage record should be written (early return before spawn)
    assert len(record_calls) == 0


# ─────────────────────────────────────────────────────────────────────
# (g) pre-spawn failure (prompt build raises) → skipped, no leak, no spawn
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_toc_prebuild_raises_skipped_and_unlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A failure BEFORE the spawn (here: _build_master_prompt raises) must still
    degrade to 'skipped', never raise into the caller, NOT call _spawn, and
    unlink the temp window (no leak). Guards the never-raise/always-unlink
    invariant for the prompt-building region."""
    window = _make_fake_window(tmp_path)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(agent_module, "_toc_source_pdf", lambda *_: window)

    def boom_build(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("prompt build blew up")

    spawn_called = False

    async def fake_spawn(**kwargs: Any):
        nonlocal spawn_called
        spawn_called = True
        return (0, "{}", _make_usage(), "")

    monkeypatch.setattr(agent_module, "_build_master_prompt", boom_build)
    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)

    result = await validate_toc(
        entries=_ENTRIES,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
        provider="gemini",
        model="gemini-2.5-flash",
        transport="cli",
    )

    assert result.status == "skipped"
    assert "validate_toc error" in result.detail
    assert not spawn_called, "_spawn must NOT run after a prompt-build failure"
    assert not window.exists(), "temp window must be unlinked even on pre-spawn failure"
