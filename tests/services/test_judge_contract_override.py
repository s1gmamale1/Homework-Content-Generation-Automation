"""Behavioral test for the judge's per-phase contract_override (custom-prompt).

The old version was `inspect.getsource`/signature grep — it passed even if the
override were miswired. This lands on the REAL boundary: judge() builds the prompt
from `contract_override or get_prompt(...)` and hands it to `agent.run_phase` as
`phase_prompt`. We mock that boundary, capture the prompt, and assert the override
text actually reaches it (and that absence falls back to get_prompt)."""
import types

import pytest

from app.services import agent
from app.services import phase_judge as pj
from app.services.phase_judge import Verdict

_JUDGE_KW = dict(
    subject="math", phase_name="reading", output_md="OUT",
    lesson_context="SRC", prior_outputs={},
    gen_provider="claude", gen_model="claude-sonnet-4-6",
    judge_provider="claude", judge_model="claude-sonnet-4-6",
)


def _capturing_run_phase(captured):
    async def _fake(**kwargs):
        captured["phase_prompt"] = kwargs["phase_prompt"]
        return types.SimpleNamespace(parsed=Verdict(passed=True))
    return _fake


@pytest.mark.asyncio
async def test_override_is_the_contract_used(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    await pj.judge(contract_override="CUSTOM-CONTRACT-SENTINEL", **_JUDGE_KW)
    assert "CUSTOM-CONTRACT-SENTINEL" in captured["phase_prompt"]


@pytest.mark.asyncio
async def test_no_override_falls_back_to_get_prompt(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "BUILTIN-CONTRACT-SENTINEL")
    await pj.judge(contract_override=None, **_JUDGE_KW)
    assert "BUILTIN-CONTRACT-SENTINEL" in captured["phase_prompt"]
