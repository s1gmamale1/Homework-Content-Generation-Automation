"""SlotSaturation pass-through at the two REGEN broad catches inside
_execute_phase (queue-correctness-1, gate correction 1):
  - judge-regen (pipeline.py ~1354): `except Exception as exc` after the
    regen's `_run_with_failover` call, guarding the post-regen judge re-check.
  - solver-regen (pipeline.py ~1441): the analogous catch in the solver's
    capped regen loop.

The judge catch soft-degrades non-auth failures to ``major_regen_failed``.
The solver catch is fail-closed after a proven mismatch, but fleet saturation
is still a control signal rather than a content failure: it must escape as
SlotSaturation so the worker parks the job instead of persisting
``mismatch_blocked``.

Choreography (two-phase stub) copied from
tests/services/test_pipeline_solver.py:96-138's patch_io fixture: patch every
DB/agent I/O boundary so _execute_phase runs without a real DB, then make the
SECOND `_run_with_failover` call (the regen leg) raise the marker error.

RED history: both regen catches once swallowed the marker. The regression pins
the distinct current outcomes: judge soft-degrade excludes it; solver
fail-closed excludes it.
"""
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings as _settings
from app.services import pipeline
from app.services.errors import SlotSaturation
from app.services.phase_judge import JudgeOutcome
from app.services.solver import SolveOutcome

_MARKER = "429 fleet credential slot wait exhausted (credential=gemini:p, budget=120s)"


def _judge_major() -> JudgeOutcome:
    return JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: content issue"],
        feedback="fix this", has_major=True,
    )


def _judge_ok() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)


def _mismatch() -> SolveOutcome:
    return SolveOutcome(
        available=True, agrees=False,
        warnings=["[high] Q3: key says 'B', solved answer is 'C' — arithmetic error"],
        feedback="\n\n## Fix these answer-key errors\n- [high] Q3: ...",
        has_mismatch=True,
    )


def _make_kwargs(phase_name: str, provider: str = "claude", model: Optional[str] = None) -> dict:
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
        solver_transport="cli",
        judge_provider_ov=None,
        judge_model_ov=None,
        solver_provider_ov=None,
        solver_model_ov=None,
        extract_provider="gemini",
        extract_model=None,
        solver_boss_arena_enabled=True,
    )


@pytest.fixture()
def patch_io(monkeypatch):
    """Patch all DB and agent I/O so _execute_phase can run without a real DB.
    Mirrors test_pipeline_solver.py's patch_io fixture."""
    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        pass

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)

    async def fake_jobs_set_status(session, job_id, status, **kw):
        pass
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", fake_jobs_set_status)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase, **kw: "base prompt text")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase, **kw: "deadbeef" * 8)

    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))
    monkeypatch.setattr(pipeline.model_tiers, "resolve_solver", lambda *a, **kw: ("claude", None))

    monkeypatch.setattr(_settings, "solver_enabled", True)
    monkeypatch.setattr(_settings, "max_solve_regens", 1)
    monkeypatch.setattr(_settings, "max_judge_regens", 1)


async def test_judge_regen_slot_saturation_escapes_as_park(monkeypatch, patch_io):
    """First _run_with_failover call = initial generation; second = the
    judge-triggered regen. The regen call raises the saturation marker —
    SlotSaturation must escape _execute_phase, not soft-degrade to
    'major_regen_failed'."""
    calls = {"n": 0}

    async def fake_failover(*, requested_provider, model, run_fn, transport, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("# initial output", 100, 50, "claude")
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    judge_responses = [_judge_major(), _judge_ok()]

    async def fake_judge(**kw):
        return judge_responses.pop(0)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs(phase_name="preview")  # not in _SOLVER_PHASES — isolates the judge leg
    with pytest.raises(SlotSaturation):
        await pipeline._execute_phase(**kw)

    assert calls["n"] == 2, f"expected exactly 2 failover calls (gen + regen), got {calls['n']}"


async def test_solver_regen_slot_saturation_escapes_as_park(monkeypatch, patch_io):
    """First _run_with_failover call = initial generation; second = the
    solver-triggered regen. The regen call raises the saturation marker —
    SlotSaturation must escape _execute_phase, not become the hard content
    outcome 'mismatch_blocked'."""
    calls = {"n": 0}

    async def fake_failover(*, requested_provider, model, run_fn, transport, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("# initial output", 100, 50, "claude")
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    async def fake_judge(**kw):
        return _judge_ok()
    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    async def fake_solve(**kw):
        return _mismatch()
    monkeypatch.setattr(pipeline.solver, "solve", fake_solve)

    kw = _make_kwargs(phase_name="memory-check")
    with pytest.raises(SlotSaturation):
        await pipeline._execute_phase(**kw)

    assert calls["n"] == 2, f"expected exactly 2 failover calls (gen + regen), got {calls['n']}"
