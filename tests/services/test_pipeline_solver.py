"""Tests for the CQ-C solver block in _execute_phase:
  - runs only for _SOLVER_PHASES, only when settings.solver_enabled
  - a HIGH-confidence key mismatch triggers a capped regen loop (bounded by
    settings.max_solve_regens)
  - agreement (no mismatch) short-circuits with solver_status='ok', no regen
  - a non-auth regen exception soft-degrades to 'mismatch_regen_failed'
    without failing the job
  - solver_status is threaded into phase_repo.set_status's final 'done' call

Mirrors the harness in tests/services/test_pipeline_judge_status.py: stubs
_judge_with_timeout, _run_with_failover, and app.services.solver.solve;
captures solver_status passed to phase_repo.set_status via a spy.
asyncio_mode=auto (pyproject.toml).
"""
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import pipeline
from app.services.phase_judge import JudgeOutcome
from app.services.solver import SolveOutcome
from app.config import settings as _settings


# ---------------------------------------------------------------------------
# Helpers to build lightweight fake outcomes
# ---------------------------------------------------------------------------

def _judge_ok() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)


def _agree() -> SolveOutcome:
    return SolveOutcome(available=True, agrees=True, warnings=[], feedback="", has_mismatch=False)


def _mismatch() -> SolveOutcome:
    return SolveOutcome(
        available=True, agrees=False,
        warnings=["[high] Q3: key says 'B', solved answer is 'C' — arithmetic error"],
        feedback="\n\n## Fix these answer-key errors\n- [high] Q3: ...",
        has_mismatch=True,
    )


# ---------------------------------------------------------------------------
# Shared kwargs for _execute_phase (non-extract phase)
# ---------------------------------------------------------------------------

def _make_kwargs(
    phase_name: str = "memory-check",
    provider: str = "claude",
    model: Optional[str] = None,
) -> dict:
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


# ---------------------------------------------------------------------------
# Fixture: patch away all I/O boundaries
# ---------------------------------------------------------------------------

@pytest.fixture()
def patch_io(monkeypatch):
    """Patch all DB and agent I/O so _execute_phase can run without a real DB.

    Captures the solver_status value passed to phase_repo.set_status's final
    'done' call and exposes it on the returned namespace, plus call counters
    for solver.solve and _run_with_failover (to prove the regen gate bites).
    """
    import types
    ns = types.SimpleNamespace(
        solver_status="__unset__", set_status_calls=[], solve_calls=[], failover_calls=[],
    )

    # ---- phase_repo --------------------------------------------------------
    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        ns.set_status_calls.append((status, kw))
        if status == "done":
            ns.solver_status = kw.get("solver_status")

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

    # ---- _run_with_failover (generation + solver-regen) --------------------
    # Returns (output_md, tin, tout, produced_by); tests can override per-call.
    ns.failover_outputs = [("# generated output", 100, 50, "claude")]

    async def fake_failover(*, requested_provider, model, run_fn, transport, **kw):
        ns.failover_calls.append((requested_provider, model, transport))
        return ns.failover_outputs.pop(0)

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    # ---- get_prompt / get_prompt_hash -------------------------------------
    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase, **kw: "base prompt text")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase, **kw: "deadbeef" * 8)

    # ---- model_tiers.resolve_judge / resolve_solver ------------------------
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))
    monkeypatch.setattr(pipeline.model_tiers, "resolve_solver", lambda *a, **kw: ("claude", None))

    # ---- _judge_with_timeout: always a clean pass (solver runs on the SAME
    # output the judge shipped; judge behavior is covered by its own tests) --
    async def fake_judge(**kw):
        return _judge_ok()
    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    # ---- solver.solve: default agrees; tests override via ns.solve_outputs -
    ns.solve_outputs = [_agree()]

    async def fake_solve(**kw):
        ns.solve_calls.append(kw)
        return ns.solve_outputs.pop(0)

    monkeypatch.setattr(pipeline.solver, "solve", fake_solve)

    # ---- solver settings: enabled, 1 regen (mirrors max_judge_regens=1) ----
    monkeypatch.setattr(_settings, "solver_enabled", True)
    monkeypatch.setattr(_settings, "max_solve_regens", 1)

    return ns


# ===========================================================================
# Test 1: target phase + HIGH-confidence mismatch → regen runner CALLED,
# solver_status in {"mismatch_regen", "mismatch_shipped"}
# ===========================================================================

async def test_target_phase_mismatch_triggers_regen(patch_io):
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),   # initial generation
        ("# regenned output", 110, 55, "claude"),  # solver-triggered regen
    ]
    patch_io.solve_outputs = [_mismatch(), _mismatch()]  # still mismatched after regen

    kw = _make_kwargs(phase_name="memory-check")
    await pipeline._execute_phase(**kw)

    # Bite: the regen runner (_run_with_failover) was invoked a SECOND time
    # (once for content generation, once for the solver-triggered regen).
    assert len(patch_io.failover_calls) == 2, (
        f"expected 2 failover calls (gen + solver-regen), got {len(patch_io.failover_calls)}"
    )
    assert patch_io.solver_status in ("mismatch_regen", "mismatch_shipped"), (
        f"got {patch_io.solver_status!r}"
    )
    assert len(patch_io.solve_calls) == 2, f"expected 2 solve() calls, got {len(patch_io.solve_calls)}"


async def test_target_phase_mismatch_regen_agrees(patch_io):
    """A regen that resolves the mismatch is adopted: solver_status='mismatch_regen'."""
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# regenned output", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _agree()]

    kw = _make_kwargs(phase_name="memory-check")
    await pipeline._execute_phase(**kw)

    assert patch_io.solver_status == "mismatch_regen", f"got {patch_io.solver_status!r}"
    done_call = next(c for c in patch_io.set_status_calls if c[0] == "done")
    assert done_call[1]["output_md"] == "# regenned output", "regenerated output must be adopted"


# ===========================================================================
# Test 2: target phase + agreement → regen runner NOT called, status='ok'
# ===========================================================================

async def test_target_phase_agrees_no_regen(patch_io):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_agree()]

    kw = _make_kwargs(phase_name="memory-check")
    await pipeline._execute_phase(**kw)

    assert patch_io.solver_status == "ok", f"got {patch_io.solver_status!r}"
    # Bite: only the initial generation call — no solver-regen call.
    assert len(patch_io.failover_calls) == 1, (
        f"expected 1 failover call (no regen), got {len(patch_io.failover_calls)}"
    )
    assert len(patch_io.solve_calls) == 1, f"expected 1 solve() call, got {len(patch_io.solve_calls)}"


# ===========================================================================
# Test 3: non-target phase → solver.solve NOT called, solver_status is None
# ===========================================================================

async def test_non_target_phase_skips_solver(patch_io):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]

    kw = _make_kwargs(phase_name="flashcards")
    await pipeline._execute_phase(**kw)

    # Bite: solver.solve was never invoked for a non-key-bearing phase.
    assert len(patch_io.solve_calls) == 0, f"expected 0 solve() calls, got {len(patch_io.solve_calls)}"
    assert patch_io.solver_status is None, f"got {patch_io.solver_status!r}"


# ===========================================================================
# Test 4: settings.solver_enabled=False → solver.solve NOT called, status None
# ===========================================================================

async def test_solver_disabled_skips_solver(monkeypatch, patch_io):
    monkeypatch.setattr(_settings, "solver_enabled", False)
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]

    kw = _make_kwargs(phase_name="memory-check")
    await pipeline._execute_phase(**kw)

    # Bite: the settings gate, not just the phase-name gate, controls this.
    assert len(patch_io.solve_calls) == 0, f"expected 0 solve() calls, got {len(patch_io.solve_calls)}"
    assert patch_io.solver_status is None, f"got {patch_io.solver_status!r}"


# ===========================================================================
# Test 5: regen raises a non-auth Exception → job NOT failed,
# solver_status == "mismatch_regen_failed"
# ===========================================================================

async def test_solver_regen_raises_non_auth_soft_degrades(monkeypatch, patch_io):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_mismatch()]

    async def failing_failover(*, requested_provider, model, run_fn, transport, **kw):
        patch_io.failover_calls.append((requested_provider, model, transport))
        if len(patch_io.failover_outputs) == 0:
            raise RuntimeError("network error during solver regen")
        return patch_io.failover_outputs.pop(0)

    monkeypatch.setattr(pipeline, "_run_with_failover", failing_failover)

    kw = _make_kwargs(phase_name="memory-check")
    # Should NOT raise; soft-degrade keeps original output.
    result = await pipeline._execute_phase(**kw)
    assert result is not None

    assert patch_io.solver_status == "mismatch_regen_failed", f"got {patch_io.solver_status!r}"
    done_call = next(c for c in patch_io.set_status_calls if c[0] == "done")
    assert done_call[1]["output_md"] == "# initial output", "original output must be kept on regen failure"


# ===========================================================================
# Test 6: boss-arena live-read toggle (operator-editable at /settings) —
# solver runs on boss-arena only when solver_boss_arena_enabled is True; the
# toggle must NOT leak into other _SOLVER_PHASES members.
# ===========================================================================

async def test_boss_arena_solved_when_toggle_on(patch_io):
    patch_io.failover_outputs = [
        ("# initial boss", 100, 50, "claude"),
        ("# regenned boss", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    kw = _make_kwargs(phase_name="boss-arena")
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) >= 1, "boss-arena must be solved when toggle on"
    assert patch_io.solver_status == "mismatch_regen", f"got {patch_io.solver_status!r}"


async def test_boss_arena_skipped_when_toggle_off(patch_io):
    patch_io.failover_outputs = [("# initial boss", 100, 50, "claude")]
    kw = _make_kwargs(phase_name="boss-arena")
    kw["solver_boss_arena_enabled"] = False
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) == 0, "boss-arena must NOT be solved when toggle off"
    assert patch_io.solver_status is None, f"got {patch_io.solver_status!r}"


async def test_non_boss_phase_ignores_boss_toggle(patch_io):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_agree()]
    kw = _make_kwargs(phase_name="memory-check")
    kw["solver_boss_arena_enabled"] = False   # off, but memory-check still solves
    await pipeline._execute_phase(**kw)
    assert len(patch_io.solve_calls) == 1
    assert patch_io.solver_status == "ok"
