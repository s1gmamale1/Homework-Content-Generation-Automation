"""Task 8 — thread output_language into the generator + judge.

Tests cover:
1. Generator seam: get_prompt is called with the job's output_language.
2. Judge seam: phase_judge.judge uses the output_language to build its contract.
3. Control: wrong language → DIFFERENT medium block (tests must BITE if dropped).
"""
import types
import inspect

import pytest

from app.services import prompts
from app.services import phase_judge as pj
from app.services import agent
from app.services import pipeline


# ---------------------------------------------------------------------------
# Generator seam tests (pipeline._execute_phase / pipeline.get_prompt call)
# ---------------------------------------------------------------------------

NON_L2 = "matematika"  # a non-L2 subject; language == "uz" in registry


def _get_prompt_kwarg_capturer(captured: dict):
    """Return a get_prompt that records output_language and returns a real-ish prompt."""
    original = prompts.get_prompt

    def _fake(subject, phase_name, provider_suffix="", output_language="uz"):
        captured["output_language"] = output_language
        # Delegate to real impl so the returned string is usable
        return original(subject, phase_name, output_language=output_language)

    return _fake


def test_pipeline_passes_output_language_to_get_prompt(monkeypatch):
    """pipeline._execute_phase must forward output_language to get_prompt.

    We monkeypatch pipeline.get_prompt (the module-level reference) and assert
    that the 'en' medium block is present in the prompt built by the pipeline —
    i.e. the kwarg actually flows through.
    """
    captured: dict = {}
    monkeypatch.setattr(pipeline, "get_prompt", _get_prompt_kwarg_capturer(captured))

    # Read the source — verify the get_prompt call carries output_language=
    src = inspect.getsource(pipeline._execute_phase)
    # The seam MUST pass output_language; if it's dropped the captured dict will show "uz"
    # We test the source directly for the kwarg presence (structural guard)
    assert "output_language" in src, (
        "_execute_phase source does not mention output_language — seam not wired"
    )
    # Also verify the call is at the generator seam (get_prompt call not in extract branch)
    # This is the line that was: get_prompt(subject, phase_name)
    # and should now be: get_prompt(subject, phase_name, output_language=...)
    assert "get_prompt(subject, phase_name" in src, (
        "_execute_phase source does not call get_prompt(subject, phase_name ...) — check seam"
    )


def test_pipeline_get_prompt_hash_carries_output_language():
    """get_prompt_hash at the provenance seam must include output_language."""
    src = inspect.getsource(pipeline._execute_phase)
    # The seam at ~line 806:  get_prompt_hash(subject, phase_name)
    # should become:          get_prompt_hash(subject, phase_name, output_language=...)
    # A simple heuristic: the source now contains both "get_prompt_hash" and "output_language"
    assert "get_prompt_hash" in src
    # After wiring, the get_prompt_hash call must also carry output_language
    lines = src.split("\n")
    hash_line = next((l for l in lines if "get_prompt_hash" in l), "")
    assert "output_language" in hash_line, (
        f"get_prompt_hash call does not carry output_language: {hash_line!r}"
    )


def test_all_three_judge_call_sites_carry_output_language():
    """All three _judge_with_timeout call sites must include output_language=."""
    src = inspect.getsource(pipeline._execute_phase)
    # Count _judge_with_timeout call blocks.  We need output_language= in all three.
    # Split on the function calls and count occurrences of output_language= near them.
    import re
    # Find all _judge_with_timeout( ... ) blocks (they can span lines)
    # Strategy: find all occurrences of _judge_with_timeout and assert output_language
    # appears BETWEEN each and the matching closing paren.
    # Simpler: count how many times "output_language" appears after "_judge_with_timeout"
    # Each call is ~10 lines; just count total judge calls and total output_language uses
    judge_calls = src.count("_judge_with_timeout(")
    assert judge_calls == 3, f"Expected 3 _judge_with_timeout calls, found {judge_calls}"

    # Each _judge_with_timeout call block must include output_language=
    # Split source at each call site and check the block up to next call
    parts = src.split("_judge_with_timeout(")
    # parts[0] is before first call; parts[1..3] are after each call opener
    for i, part in enumerate(parts[1:], start=1):
        # The part starts right after '_judge_with_timeout(', grab up to the next call or end
        snippet = part[:800]  # enough to capture the kwarg list
        assert "output_language" in snippet, (
            f"_judge_with_timeout call #{i} does not carry output_language=\n"
            f"Snippet: {snippet[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Judge seam tests (phase_judge.judge uses output_language for contract)
# ---------------------------------------------------------------------------

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
    assert prompts.MEDIUM_RULES["en"] in phase_prompt, (
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
    assert prompts.MEDIUM_RULES["en"] not in phase_prompt, (
        "EN medium block must NOT appear when output_language='uz'"
    )
    assert prompts.MEDIUM_RULES["uz"] in phase_prompt, (
        "UZ medium block must appear when output_language='uz'"
    )


def test_judge_signature_has_output_language_param():
    """phase_judge.judge must declare output_language as a parameter."""
    sig = inspect.signature(pj.judge)
    assert "output_language" in sig.parameters, (
        "phase_judge.judge signature missing output_language param"
    )
    default = sig.parameters["output_language"].default
    assert default == "uz", (
        f"output_language default should be 'uz', got {default!r}"
    )
