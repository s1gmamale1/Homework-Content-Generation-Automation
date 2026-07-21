"""Scheduler-side abandoned-sibling reset (queue-correctness-1, Task 5).

`_run_content_phases_parallel` cancels in-flight peers at three sites: peer
hard-failure, pause/park (SessionLimitPause/SlotSaturation/TransientPhaseError),
and external job-cancel. `asyncio.Task.cancel()` delivers `CancelledError` —
a `BaseException` — so the peer's own `except Exception` cleanup in
`_execute_one_phase` never runs and its phase_outputs row is orphaned at
'running'. `_abandon_inflight` sweeps those rows; these tests prove each
cancel-and-drain site calls it with the right (phase_names, status, reason).

RED: `pipeline._abandon_inflight` doesn't exist yet.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from loguru import logger

from app.services import pipeline
from app.services.errors import TransientPhaseError


def _sched_kwargs(**over) -> dict:
    """Full real signature of _run_content_phases_parallel (pipeline.py:632-663).
    content_phases=["a", "b"] are fictional phase names with no PHASE_DEPS
    entries, so resolve_phase_deps() returns an empty set for both — they are
    both root-level and launch in the same wave."""
    base = dict(
        job_id=uuid4(),
        resource_id="job-x",
        log=logger,
        content_phases=["a", "b"],
        phase_order_offset=0,
        subject="history",
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=Path("/nonexistent.pdf"),
        file_phases=set(),
        section_data={"title": "L1"},
        lesson_context=None,
        prior_outputs={},
        difficulty=None,
        source_map_digest="",
        source_map_ids=None,
        transport="api",
        extract_transport="api",
        judge_transport="api",
        solver_transport="api",
        custom_prompts=None,
        judge_provider_ov=None,
        judge_model_ov=None,
        solver_provider_ov=None,
        solver_model_ov=None,
        solver_boss_arena_enabled=False,
        extract_provider="gemini",
        extract_model="gemini-2.5-flash",
        session_limit_strategy="pause",
        output_language="uz",
    )
    base.update(over)
    return base


def test_hard_failure_abandons_sibling_as_failed(monkeypatch):
    """Phase 'a' fails hard instantly; phase 'b' would sleep forever (cancelled
    once 'a' resolves). The hard-failure branch must abandon exactly ['b'] as
    'failed' with a reason mentioning the sibling."""
    async def fake_execute_one_phase(*, phase_name, **kw):
        if phase_name == "a":
            raise RuntimeError("malformed response envelope")
        await asyncio.sleep(3600)

    monkeypatch.setattr(pipeline, "_execute_one_phase", fake_execute_one_phase)
    abandon = AsyncMock()
    monkeypatch.setattr(pipeline, "_abandon_inflight", abandon)

    with pytest.raises(RuntimeError, match="content phase failed"):
        asyncio.run(pipeline._run_content_phases_parallel(**_sched_kwargs()))

    abandon.assert_awaited_once()
    args = abandon.await_args.args
    assert args[1] == ["b"], f"expected ['b'] abandoned, got {args[1]!r}"
    assert args[2] == "failed", f"expected status='failed', got {args[2]!r}"
    assert "sibling" in args[3], f"expected 'sibling' in reason, got {args[3]!r}"


def test_transient_failure_abandons_sibling_as_pending(monkeypatch):
    """Gate correction 4: phase 'a' raises TransientPhaseError (the job is
    being requeued) — its sibling 'b' is WAITING, not failed, so it must be
    abandoned as 'pending' with no error message baked into a 'failed' row."""
    async def fake_execute_one_phase(*, phase_name, **kw):
        if phase_name == "a":
            raise TransientPhaseError("a: 429 RESOURCE_EXHAUSTED")
        await asyncio.sleep(3600)

    monkeypatch.setattr(pipeline, "_execute_one_phase", fake_execute_one_phase)
    abandon = AsyncMock()
    monkeypatch.setattr(pipeline, "_abandon_inflight", abandon)

    with pytest.raises(TransientPhaseError):
        asyncio.run(pipeline._run_content_phases_parallel(**_sched_kwargs()))

    abandon.assert_awaited_once()
    args = abandon.await_args.args
    assert args[1] == ["b"], f"expected ['b'] abandoned, got {args[1]!r}"
    assert args[2] == "pending", f"expected status='pending', got {args[2]!r}"
    assert "requeued" in args[3], f"expected 'requeued' in reason, got {args[3]!r}"


def test_external_cancel_abandons_all_inflight_as_failed(monkeypatch):
    """External cancel (user pressed Cancel): both 'a' and 'b' sleep forever;
    cancelling the whole coroutine must abandon every in-flight phase as
    'failed' with a reason mentioning the job cancel."""
    async def fake_execute_one_phase(*, phase_name, **kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(pipeline, "_execute_one_phase", fake_execute_one_phase)
    abandon = AsyncMock()
    monkeypatch.setattr(pipeline, "_abandon_inflight", abandon)

    async def _drive():
        task = asyncio.ensure_future(
            pipeline._run_content_phases_parallel(**_sched_kwargs())
        )
        await asyncio.sleep(0.05)  # let both phases launch and go in_flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())

    abandon.assert_awaited_once()
    args = abandon.await_args.args
    assert sorted(args[1]) == ["a", "b"], f"expected both abandoned, got {args[1]!r}"
    assert args[2] == "failed", f"expected status='failed', got {args[2]!r}"
    assert "cancelled" in args[3], f"expected 'cancelled' in reason, got {args[3]!r}"
