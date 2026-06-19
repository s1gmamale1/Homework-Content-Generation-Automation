import inspect

from app.services import phase_judge as pj


def test_verdict_models_shape():
    v = pj.Verdict(
        passed=False,
        failures=[pj.Failure(requirement="r", evidence="e", severity="major")],
    )
    assert v.passed is False
    assert v.failures[0].requirement == "r" and v.failures[0].evidence == "e"
    assert v.failures[0].severity == "major"
    assert pj.Verdict(passed=True).failures == []


def test_serialize_failures_prefixes_severity():
    out = pj._serialize_failures(
        [pj.Failure(requirement="Exactly 3 checkpoints", evidence="found 4", severity="major")]
    )
    assert out == ["[major] Exactly 3 checkpoints — found 4"]


def test_build_judge_prompt_contains_contract_output_and_protocol():
    p = pj._build_judge_prompt(contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT")
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    low = p.lower()
    assert "cite" in low or "quote" in low
    assert "refute" in low or "substantiate" in low or "cannot substantiate" in low
    assert "placeholder" in low
    assert "major" in low and "minor" in low   # severity rubric present
    assert "VISUAL / SVG RULES" not in p


def test_build_judge_prompt_has_fidelity_rule_and_flags():
    p = pj._build_judge_prompt(
        contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT",
        fidelity_flags=["output states year 1991 as fact; not found in source"],
    )
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    # instruction points the judge at the (separately-injected) LESSON CONTEXT block as truth
    assert "lesson context" in p.lower() and ("ground truth" in p.lower() or "faithful" in p.lower())
    assert "1991" in p                      # deterministic hint surfaced
    assert "POSSIBLE SOURCE ISSUES" in p


def test_build_judge_prompt_omits_flags_section_when_empty():
    p = pj._build_judge_prompt(contract="C", output_md="O", fidelity_flags=[])
    assert "POSSIBLE SOURCE ISSUES" not in p


def test_build_feedback_lists_failures():
    fb = pj._build_feedback(["A — x", "B — y"])
    assert "A — x" in fb and "B — y" in fb


def test_judge_uses_run_phase_with_judge_operation_and_neutral_phase():
    src = inspect.getsource(pj.judge)
    # Judge selection now arrives as params (resolved by the caller via
    # model_tiers.resolve_judge); judge() consumes them, no longer self-selects.
    assert "judge_provider" in src and "judge_model" in src
    assert "schema=Verdict" in src
    assert 'operation=f"judge:' in src or "operation=f'judge:" in src
    assert '"__judge__"' in src or "'__judge__'" in src
    assert "get_prompt(" in src


def test_judge_is_async_and_returns_outcome_type():
    assert inspect.iscoroutinefunction(pj.judge)
    src = inspect.getsource(pj.judge)
    assert "judge-unavailable" in src


import asyncio
from types import SimpleNamespace

from app.services import agent
from app.services import model_tiers as mt


def _fake_run_phase(verdict):
    async def _run(**kwargs):
        return SimpleNamespace(parsed=verdict)
    return _run


def _call_judge():
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    return asyncio.run(pj.judge(
        subject="biology", phase_name="case-based-preview", output_md="x",
        lesson_context=None, prior_outputs={},
        gen_provider="claude", gen_model="claude-sonnet-4-6",
        judge_provider=jp, judge_model=jm,
    ))


def test_judge_pass_outcome(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")
    monkeypatch.setattr(agent, "run_phase", _fake_run_phase(pj.Verdict(passed=True)))
    out = _call_judge()
    assert out.available and out.passed
    assert out.warnings == [] and out.feedback == ""


def test_judge_major_failure_has_warnings_feedback_and_flags_major(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")
    v = pj.Verdict(passed=False, failures=[
        pj.Failure(requirement="Exactly 3", evidence="found 4", severity="major"),
    ])
    monkeypatch.setattr(agent, "run_phase", _fake_run_phase(v))
    out = _call_judge()
    assert out.available and not out.passed
    assert out.has_major is True
    assert out.warnings == ["[major] Exactly 3 — found 4"]
    assert "Exactly 3 — found 4" in out.feedback


def test_judge_minor_only_does_not_flag_major(monkeypatch):
    """A minor-only verdict records a warning but must NOT trigger a regen."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")
    v = pj.Verdict(passed=False, failures=[
        pj.Failure(requirement="Back is concise", evidence="padded filler", severity="minor"),
    ])
    monkeypatch.setattr(agent, "run_phase", _fake_run_phase(v))
    out = _call_judge()
    assert out.available and not out.passed
    assert out.has_major is False
    assert out.warnings == ["[minor] Back is concise — padded filler"]


def test_judge_degrades_when_run_phase_raises(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError("CLI exploded")

    monkeypatch.setattr(agent, "run_phase", _boom)
    out = _call_judge()
    assert out.available is False and out.passed is True
    assert out.warnings == ["judge-unavailable: RuntimeError"]


def test_judge_degrades_when_get_prompt_raises(monkeypatch):
    def _boom_prompt(s, p):
        raise KeyError("no such phase")

    monkeypatch.setattr(pj, "get_prompt", _boom_prompt)
    # run_phase should never be reached; if it is, this would error loudly
    out = _call_judge()
    assert out.available is False   # the Critical fix: get_prompt raise must degrade


def test_fidelity_flags_catches_world_claim_year_absent_from_source():
    out = "The treaty was signed in 1991, ending the union."
    src = "The republic became independent. (no dates given)"
    flags = pj._fidelity_flags(out, src)
    assert any("1991" in f for f in flags)


def test_fidelity_flags_ignores_math_worked_example_numbers():
    out = "Solve 3x + 7 = 22. Subtract 7: 3x = 15. Divide by 3: x = 5."
    src = "Linear equations: isolate the variable using inverse operations."
    assert pj._fidelity_flags(out, src) == []          # MUST be empty — no regen-tax on math


def test_fidelity_flags_passes_year_present_in_source():
    out = "Independence was declared in 1991."
    src = "In 1991 the republic declared independence."
    assert pj._fidelity_flags(out, src) == []


def test_sdk_auth_error_strings_trip_auth_signals():
    """Representative google-genai / anthropic / AI-Studio auth-error strings must
    be recognized as auth errors, so an api job fails loud instead of degrading."""
    from app.services import phase_judge

    samples = [
        "google.api_core.exceptions.PermissionDenied: 403 permission_denied: ...",
        "PERMISSION_DENIED: Vertex AI API has not been used in project ...",
        "anthropic.AuthenticationError: Error code: 401 - invalid x-api-key",
        "google.genai.errors.ClientError: 400 API key not valid. Please pass a valid API key.",
        "RefreshError: invalid_grant: Invalid JWT Signature.",
    ]
    for s in samples:
        assert phase_judge._is_auth_error(RuntimeError(s)), s
