"""Task 5 — subject-family gate for the extract-fidelity guard.

``pipeline._verify_and_maybe_regen_extract`` must resolve `strict` from the
subject's family (`app.services.subjects.REGISTRY`) and pass it through to
`agent.extract_fidelity_candidates`. Only `languages` and `humanities`
families are strict; everything else (including an unknown/absent subject
code) fails toward today's non-strict behavior.

These tests patch `agent.extract_fidelity_candidates` itself (not just its
inputs) so the passed `strict=` kwarg can be inspected directly — no real
model calls, no DB, no PDF reads.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import pipeline


_SECTION = {"title": "T", "number": "1", "page_start": 1, "page_end": 2}


async def _run(monkeypatch, subject):
    """Drive _verify_and_maybe_regen_extract with extract_fidelity_candidates
    mocked to return [] (no candidates -> no further paid calls), and return
    the mock so its call kwargs can be inspected."""
    spy = MagicMock(return_value=[])
    monkeypatch.setattr(pipeline.agent, "extract_fidelity_candidates", spy)
    verify_spy = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", verify_spy)
    await pipeline._verify_and_maybe_regen_extract(
        out="some extract text", book_text="some book text", pdf_path="x.pdf",
        prov="gemini", mdl=None, transport="api", section=_SECTION,
        job_id=None, po_id=None, subject=subject,
    )
    return spy


@pytest.mark.asyncio
async def test_english_resolves_strict_true(monkeypatch):
    spy = await _run(monkeypatch, "english")
    assert spy.call_args.kwargs["strict"] is True


@pytest.mark.asyncio
async def test_history_resolves_strict_true(monkeypatch):
    # history = family "humanities"
    spy = await _run(monkeypatch, "history")
    assert spy.call_args.kwargs["strict"] is True


@pytest.mark.asyncio
async def test_math_algebra_resolves_strict_false(monkeypatch):
    spy = await _run(monkeypatch, "math-algebra")
    assert spy.call_args.kwargs["strict"] is False


@pytest.mark.asyncio
async def test_physics_resolves_strict_false(monkeypatch):
    spy = await _run(monkeypatch, "physics")
    assert spy.call_args.kwargs["strict"] is False


@pytest.mark.asyncio
async def test_default_family_informatika_resolves_strict_false(monkeypatch):
    # informatika is family "default" — code tokens are genuine '/'+paren
    # content, so it deliberately stays non-strict.
    spy = await _run(monkeypatch, "informatika")
    assert spy.call_args.kwargs["strict"] is False


@pytest.mark.asyncio
async def test_unknown_subject_code_resolves_strict_false(monkeypatch):
    # Fail toward current (non-strict) behavior for a code not in the registry.
    spy = await _run(monkeypatch, "not-a-real-subject-code")
    assert spy.call_args.kwargs["strict"] is False


@pytest.mark.asyncio
async def test_absent_subject_resolves_strict_false(monkeypatch):
    # subject omitted entirely (default "") -> non-strict, same fail-toward-
    # current-behavior posture, for any caller that predates this gate.
    spy = MagicMock(return_value=[])
    monkeypatch.setattr(pipeline.agent, "extract_fidelity_candidates", spy)
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", AsyncMock(return_value=[]))
    await pipeline._verify_and_maybe_regen_extract(
        out="some extract text", book_text="some book text", pdf_path="x.pdf",
        prov="gemini", mdl=None, transport="api", section=_SECTION,
        job_id=None, po_id=None,
    )
    assert spy.call_args.kwargs["strict"] is False
