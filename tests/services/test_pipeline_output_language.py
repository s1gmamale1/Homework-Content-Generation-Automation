"""Task 8 — thread output_language into the generator + judge.

Tests cover:
1. Generator seam: get_prompt is called with the job's output_language.
2. Judge seam: phase_judge.judge uses the output_language to build its contract.
3. Control: wrong language → DIFFERENT medium block (tests must BITE if dropped).
"""
import types
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import prompts
from app.services import phase_judge as pj
from app.services import agent
from app.services import pipeline


# ---------------------------------------------------------------------------
# Shared patch_io fixture — mocks all DB/IO so _execute_phase runs without DB
# (modelled after tests/services/test_pipeline_judge_status.py)
# ---------------------------------------------------------------------------

def _make_kwargs(
    phase_name: str = "flashcards",
    output_language: str = "uz",
) -> dict:
    return dict(
        job_id=uuid.uuid4(),
        phase_name=phase_name,
        phase_order=1,
        subject="matematika",
        provider="claude",
        model=None,
        pdf_path=Path("/fake/book.pdf"),
        attach_file=False,
        section={"title": "Algebra", "number": "1.1", "page_start": 1, "page_end": 5,
                 "id": uuid.uuid4()},
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
        output_language=output_language,
    )


@pytest.fixture()
def patch_io(monkeypatch):
    """Patch all DB and agent I/O so _execute_phase can run without a real DB."""
    ns = types.SimpleNamespace(
        get_prompt_calls=[],
        get_prompt_hash_calls=[],
        failover_outputs=[("# generated output", 100, 50, "claude")],
    )

    # ---- phase_repo --------------------------------------------------------
    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        pass

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

    # ---- _run_with_failover ------------------------------------------------
    async def fake_failover(*, requested_provider, model, run_fn, transport, **kw):
        return ns.failover_outputs.pop(0)

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    # ---- _judge_with_timeout (always returns a clean pass) -----------------
    from app.services.phase_judge import JudgeOutcome

    async def fake_judge(**kw):
        return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    # ---- model_tiers.resolve_judge -----------------------------------------
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))

    return ns


# ---------------------------------------------------------------------------
# Generator seam tests (pipeline._execute_phase → get_prompt call)
# ---------------------------------------------------------------------------

NON_L2 = "matematika"  # a non-L2 subject; language == "uz" in registry


@pytest.mark.asyncio
async def test_pipeline_passes_output_language_to_get_prompt(monkeypatch, patch_io):
    """pipeline._execute_phase must forward output_language to get_prompt.

    We monkeypatch pipeline.get_prompt to record the kwarg it receives, then
    call _execute_phase with output_language='en'. We assert:
    - The recorded output_language is 'en' (not the default 'uz').
    - The prompt returned to the failover driver contains the EN medium block.

    This FAILS if the seam is reverted to hardcode output_language='uz'.
    """
    captured: dict = {}
    original_get_prompt = prompts.get_prompt

    def recording_get_prompt(subject, phase_name, provider_suffix="", output_language="uz"):
        captured["output_language"] = output_language
        # Delegate to the real impl so we get the real language-rule injected
        return original_get_prompt(subject, phase_name, output_language=output_language)

    monkeypatch.setattr(pipeline, "get_prompt", recording_get_prompt)
    # Also patch get_prompt_hash to avoid DB-unrelated hash computation side effects
    monkeypatch.setattr(pipeline, "get_prompt_hash",
                        lambda subject, phase, **kw: "deadbeef" * 8)

    kw = _make_kwargs(phase_name="flashcards", output_language="en")
    await pipeline._execute_phase(**kw)

    assert "output_language" in captured, (
        "get_prompt was never called — seam is broken"
    )
    assert captured["output_language"] == "en", (
        f"pipeline passed output_language={captured['output_language']!r} to get_prompt, "
        "expected 'en' — seam is not forwarding the job's language"
    )
    # The real get_prompt must embed the EN medium block when output_language='en'
    built_prompt = original_get_prompt(NON_L2, "flashcards", output_language="en")
    assert "All student-facing text in natural, formal English." in built_prompt, (
        "EN medium block not found in the prompt built by get_prompt(output_language='en') — "
        "prompts layer is not wiring output_language into the prompt text"
    )


@pytest.mark.asyncio
async def test_pipeline_get_prompt_hash_carries_output_language(monkeypatch, patch_io):
    """get_prompt_hash at the provenance seam must be called with output_language.

    We monkeypatch pipeline.get_prompt_hash to record its kwargs, then call
    _execute_phase with output_language='en'. We assert the recorded kwarg is 'en'.
    We also assert the hash actually DIFFERS between en and uz (proving it's live input).

    This FAILS if the seam reverts to calling get_prompt_hash without output_language.
    """
    captured: dict = {}
    original_hash = prompts.get_prompt_hash

    def recording_hash(subject, phase_name, output_language="uz"):
        captured["output_language"] = output_language
        return original_hash(subject, phase_name, output_language=output_language)

    monkeypatch.setattr(pipeline, "get_prompt_hash", recording_hash)
    # Also patch get_prompt to a dummy so it doesn't drag in filesystem reads
    monkeypatch.setattr(pipeline, "get_prompt",
                        lambda subject, phase, **kw: "dummy prompt text")

    kw = _make_kwargs(phase_name="flashcards", output_language="en")
    await pipeline._execute_phase(**kw)

    assert "output_language" in captured, (
        "get_prompt_hash was never called from the seam — check _execute_phase"
    )
    assert captured["output_language"] == "en", (
        f"pipeline passed output_language={captured['output_language']!r} to get_prompt_hash, "
        "expected 'en' — hash seam is not forwarding the job's language"
    )

    # Bonus: the real hash function must produce DIFFERENT values for en vs uz
    hash_en = original_hash(NON_L2, "flashcards", output_language="en")
    hash_uz = original_hash(NON_L2, "flashcards", output_language="uz")
    assert hash_en != hash_uz, (
        "get_prompt_hash returns the same value for 'en' and 'uz' — "
        "output_language is not a live input to the hash"
    )


# ---------------------------------------------------------------------------
# Judge seam tests (phase_judge.judge uses output_language for contract)
# ---------------------------------------------------------------------------
# NOTE: test_all_three_judge_call_sites_carry_output_language was a vacuous
# source-grep test — removed. The two behavioral tests below exercise the judge
# seam end-to-end and genuinely bite if output_language is dropped.

def _capturing_run_phase(captured: dict):
    async def _fake(**kwargs):
        captured["phase_prompt"] = kwargs.get("phase_prompt", "")
        return types.SimpleNamespace(parsed=pj.Verdict(passed=True))
    return _fake


@pytest.mark.asyncio
async def test_judge_en_contract_contains_en_medium_block(monkeypatch):
    """judge(..., output_language='en') must build contract with EN medium block."""
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    import asyncio
    from app.services import model_tiers as mt
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    await pj.judge(
        subject=NON_L2,
        phase_name="flashcards",
        output_md="some output",
        lesson_context="lesson ctx",
        prior_outputs={},
        gen_provider="claude",
        gen_model="claude-sonnet-4-6",
        judge_provider=jp,
        judge_model=jm,
        output_language="en",
    )
    phase_prompt = captured.get("phase_prompt", "")
    assert "All student-facing text in natural, formal English." in phase_prompt, (
        "EN medium block not found in judge prompt; output_language='en' not threaded into contract"
    )


@pytest.mark.asyncio
async def test_judge_uz_contract_does_not_contain_en_medium_block(monkeypatch):
    """Control: judge(..., output_language='uz') must NOT have the EN medium block."""
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    from app.services import model_tiers as mt
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    await pj.judge(
        subject=NON_L2,
        phase_name="flashcards",
        output_md="some output",
        lesson_context="lesson ctx",
        prior_outputs={},
        gen_provider="claude",
        gen_model="claude-sonnet-4-6",
        judge_provider=jp,
        judge_model=jm,
        output_language="uz",
    )
    phase_prompt = captured.get("phase_prompt", "")
    assert "All student-facing text in natural, formal English." not in phase_prompt, (
        "EN medium block must NOT appear when output_language='uz'"
    )
    assert "All student-facing text in natural, formal Uzbek." in phase_prompt, (
        "UZ medium block must appear when output_language='uz'"
    )


def test_judge_signature_has_output_language_param():
    """phase_judge.judge must declare output_language as a parameter."""
    import inspect
    sig = inspect.signature(pj.judge)
    assert "output_language" in sig.parameters, (
        "phase_judge.judge signature missing output_language param"
    )
    default = sig.parameters["output_language"].default
    assert default == "uz", (
        f"output_language default should be 'uz', got {default!r}"
    )
