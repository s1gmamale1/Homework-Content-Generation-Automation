import inspect

from app.services import phase_judge as pj


def test_verdict_models_shape():
    v = pj.Verdict(passed=False, failures=[pj.Failure(requirement="r", evidence="e")])
    assert v.passed is False
    assert v.failures[0].requirement == "r" and v.failures[0].evidence == "e"
    assert pj.Verdict(passed=True).failures == []


def test_serialize_failures_to_strings():
    out = pj._serialize_failures(
        [pj.Failure(requirement="Exactly 3 checkpoints", evidence="found 4")]
    )
    assert out == ["Exactly 3 checkpoints — found 4"]


def test_build_judge_prompt_contains_contract_output_and_protocol():
    p = pj._build_judge_prompt(contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT")
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    low = p.lower()
    assert "cite" in low or "quote" in low
    assert "refute" in low or "substantiate" in low or "cannot substantiate" in low
    assert "placeholder" in low
    assert "VISUAL / SVG RULES" not in p


def test_build_feedback_lists_failures():
    fb = pj._build_feedback(["A — x", "B — y"])
    assert "A — x" in fb and "B — y" in fb


def test_judge_uses_run_phase_with_judge_operation_and_neutral_phase():
    src = inspect.getsource(pj.judge)
    assert "judge_model_for" in src
    assert "schema=Verdict" in src
    assert 'operation=f"judge:' in src or "operation=f'judge:" in src
    assert '"__judge__"' in src or "'__judge__'" in src
    assert "get_prompt(" in src


def test_judge_is_async_and_returns_outcome_type():
    assert inspect.iscoroutinefunction(pj.judge)
    src = inspect.getsource(pj.judge)
    assert "judge-unavailable" in src
