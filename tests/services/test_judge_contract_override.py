import inspect

from app.services import phase_judge


def test_judge_accepts_contract_override():
    sig = inspect.signature(phase_judge.judge)
    assert "contract_override" in sig.parameters


def test_judge_uses_override_in_source():
    src = inspect.getsource(phase_judge.judge)
    # the contract must come from the override when present, else get_prompt
    assert "contract_override or get_prompt(" in src
