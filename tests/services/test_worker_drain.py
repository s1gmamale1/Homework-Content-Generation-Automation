"""Real-DB proof: worker self-drains when registry status is set to "draining".

Three cases:
  1. draining → stop() called, no clobber (status stays "draining")
  2. online   → keeps beating, _stop_event clear
  3. None     → unregistered worker registers itself ("online") on first beat

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL (real Postgres required;
_registry_heartbeat uses SessionLocal, not a mockable dep).

RED-prove (constraint #4 anti-regression): to validate these tests BITE we
momentarily patch _drain_check_and_beat to always upsert without checking
status and confirm the draining test fails on BOTH its assertions.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# Sentinel pc_ids — unlikely to conflict with real workers
_PC_DRAINING = "test-drain:11111"
_PC_ONLINE = "test-drain:11112"
_PC_UNREG = "test-drain:11113"
_PC_REDPROVE = "test-drain:11114"  # isolated sentinel for RED-prove test (avoids parallel-runner conflict with _PC_DRAINING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _cleanup(*pc_ids: str) -> None:
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    async with SessionLocal() as s:
        await s.execute(delete(WorkerNode).where(WorkerNode.pc_id.in_(list(pc_ids))))
        await s.commit()


# ---------------------------------------------------------------------------
# Case 1: draining → stop() called, status NOT clobbered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_draining_stops_worker_and_does_not_clobber():
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    try:
        # Seed: register the worker, then flip to draining
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_DRAINING)
            await s.commit()
        async with SessionLocal() as s:
            await workers_repo.set_status(s, _PC_DRAINING, "draining")
            await s.commit()

        # Build a Worker with the sentinel id
        w = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
        w.id = _PC_DRAINING

        # Pre-condition: _stop_event is clear
        assert not w._stop_event.is_set(), "stop_event should start clear"

        # Trigger the heartbeat — this should detect "draining" and call stop()
        await w._registry_heartbeat()

        # (a) stop() was called
        assert w._stop_event.is_set(), (
            "_stop_event must be set after draining heartbeat"
        )

        # (b) status must still be "draining" — no clobber to "online"
        async with SessionLocal() as s:
            status = await workers_repo.get_status(s, _PC_DRAINING)
        assert status == "draining", (
            f"draining status was clobbered to {status!r} — upsert must be skipped"
        )
    finally:
        await _cleanup(_PC_DRAINING)


# ---------------------------------------------------------------------------
# Case 2: online → keeps beating, _stop_event stays clear
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_online_worker_keeps_beating():
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    try:
        # Seed: register the worker (status=online)
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_ONLINE)
            await s.commit()

        w = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
        w.id = _PC_ONLINE

        await w._registry_heartbeat()

        # stop() must NOT have been called
        assert not w._stop_event.is_set(), (
            "_stop_event must remain clear for an online worker"
        )

        # Status must still be "online"
        async with SessionLocal() as s:
            status = await workers_repo.get_status(s, _PC_ONLINE)
        assert status == "online", (
            f"status changed unexpectedly: {status!r}"
        )
    finally:
        await _cleanup(_PC_ONLINE)


# ---------------------------------------------------------------------------
# Case 3: unregistered (status=None) → registers as "online"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unregistered_worker_registers_on_first_beat():
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    try:
        # Ensure no pre-existing row
        await _cleanup(_PC_UNREG)

        w = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
        w.id = _PC_UNREG

        # Pre-condition: no row yet
        async with SessionLocal() as s:
            status_before = await workers_repo.get_status(s, _PC_UNREG)
        assert status_before is None, f"expected no row, got status={status_before!r}"

        await w._registry_heartbeat()

        # Row must now exist with status "online"
        async with SessionLocal() as s:
            status_after = await workers_repo.get_status(s, _PC_UNREG)
        assert status_after == "online", (
            f"unregistered worker must register as online; got {status_after!r}"
        )

        # _stop_event must remain clear
        assert not w._stop_event.is_set(), (
            "_stop_event must remain clear for an unregistered (first-beat) worker"
        )
    finally:
        await _cleanup(_PC_UNREG)


# ---------------------------------------------------------------------------
# RED-prove: removing the drain branch breaks both draining assertions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_red_prove_without_drain_branch_both_assertions_fail():
    """Constraint #4 anti-regression: if _drain_check_and_beat blindly upserts
    without checking status, the draining test MUST fail on BOTH:
      (a) _stop_event.is_set() — stop() was never called
      (b) status == "draining" — it was clobbered to "online"
    We patch the method in-place, run the scenario, and assert it breaks.
    Then we restore and confirm the real implementation passes.
    """
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    try:
        # Seed: register + set draining (using isolated sentinel _PC_REDPROVE)
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_REDPROVE)
            await s.commit()
        async with SessionLocal() as s:
            await workers_repo.set_status(s, _PC_REDPROVE, "draining")
            await s.commit()

        # --- RED: patch _drain_check_and_beat to always upsert (broken impl) ---
        async def _broken_drain_check_and_beat(self, session):
            """Simulates the pre-fix code that blindly upserts "online"."""
            await workers_repo.upsert_heartbeat(session, self.id)
            return True

        w_broken = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
        w_broken.id = _PC_REDPROVE
        # Bind the broken method
        import types
        w_broken._drain_check_and_beat = types.MethodType(_broken_drain_check_and_beat, w_broken)

        await w_broken._registry_heartbeat()

        # With the broken impl: stop_event must NOT be set (drain branch never ran)
        stop_event_set = w_broken._stop_event.is_set()
        async with SessionLocal() as s:
            status_after_broken = await workers_repo.get_status(s, _PC_REDPROVE)

        # RED assertions — these MUST be the wrong (broken) values
        assert not stop_event_set, (
            "RED: broken impl must NOT set stop_event — got set (implementation already correct?)"
        )
        assert status_after_broken == "online", (
            f"RED: broken impl must clobber status to 'online'; got {status_after_broken!r}"
        )

        # --- GREEN: reset and re-seed, run the REAL implementation ---
        # Reset status to draining again (broken impl clobbered it to online)
        async with SessionLocal() as s:
            await workers_repo.set_status(s, _PC_REDPROVE, "draining")
            await s.commit()

        w_real = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
        w_real.id = _PC_REDPROVE
        await w_real._registry_heartbeat()

        # GREEN assertions — the real implementation fixes both
        assert w_real._stop_event.is_set(), (
            "GREEN: real impl must set stop_event on draining status"
        )
        async with SessionLocal() as s:
            status_after_real = await workers_repo.get_status(s, _PC_REDPROVE)
        assert status_after_real == "draining", (
            f"GREEN: real impl must NOT clobber status; got {status_after_real!r}"
        )

    finally:
        await _cleanup(_PC_REDPROVE)
