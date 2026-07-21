"""SlotSaturation pass-through at the phase_judge/solver module boundaries
(queue-correctness-1, gate correction 1 — marker fallback).

Both `judge()` and `solve()` normally degrade ANY exception from the
underlying `agent.run_phase` call to 'unavailable'/'unsolved' so validation
never blocks generation — EXCEPT an api auth error (re-raised loud) and now
a fleet slot-saturation marker (re-raised as SlotSaturation so the pipeline
parks the job instead of shipping ungraded/unsolved content and burning a
requeue-worthy signal on a soft degrade).

Pattern mirrors tests/services/test_solver.py's COMMON-kwargs + monkeypatched
`agent.run_phase` harness.

RED: today both modules swallow the marker error into a plain degrade
(available=False, judge-unavailable:/solver-unavailable: RuntimeError) — no
SlotSaturation escapes.
"""
import pytest

from app.services import phase_judge, solver
from app.services.errors import SlotSaturation

_MARKER = "429 fleet credential slot wait exhausted (credential=gemini:p, budget=120s)"

_JUDGE_COMMON = dict(
    subject="matematika", phase_name="preview", output_md="x",
    lesson_context=None, prior_outputs={},
    gen_provider="claude", gen_model="claude-sonnet-4-6",
    judge_provider="claude", judge_model="claude-sonnet-4-6",
    transport="cli",
)

_SOLVER_COMMON = dict(
    subject="matematika", phase_name="memory-check", phase_output_md="...",
    lesson_context="ctx", prior_outputs={}, output_language="uz",
    solver_provider="claude", solver_model="claude-opus-4-7", transport="cli",
    homework_job_id=None, phase_output_id=None,
)


async def test_judge_parks_on_slot_saturation_marker(monkeypatch):
    async def _saturated(**kwargs):
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(phase_judge, "get_prompt", lambda s, p, **kw: "CONTRACT")
    monkeypatch.setattr("app.services.agent.run_phase", _saturated)

    with pytest.raises(SlotSaturation):
        await phase_judge.judge(**_JUDGE_COMMON)


async def test_solver_parks_on_slot_saturation_marker(monkeypatch):
    async def _saturated(**kwargs):
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(solver, "get_prompt", lambda s, p, **kw: "CONTRACT")
    monkeypatch.setattr("app.services.agent.run_phase", _saturated)

    with pytest.raises(SlotSaturation):
        await solver.solve(**_SOLVER_COMMON)
