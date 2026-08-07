import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories import jobs as jobs_repo
from app.services import worker as worker_mod
from app.services.lease import HeartbeatOutcome, JobLease
from app.services.worker import RUNNING_JOBS, Worker


def test_touch_claim_exists_and_scoped():
    assert hasattr(jobs_repo, "touch_claim")
    src = inspect.getsource(jobs_repo.touch_claim)
    # Only refresh a row that is still RUNNING (never resurrect a finished job).
    assert 'status == "running"' in src or "status==\"running\"" in src
    assert "claimed_at" in src


def test_execute_job_runs_and_cancels_heartbeat():
    src = inspect.getsource(Worker._execute_job)
    assert "_heartbeat" in src          # heartbeat task started
    assert "cancel()" in src            # ...and cancelled when the job ends


def test_heartbeat_uses_configured_interval():
    src = inspect.getsource(Worker._heartbeat)
    assert "heartbeat_seconds" in src
    assert "touch_claim" in src


async def test_heartbeat_finished_stops_without_cancelling_own_task(monkeypatch):
    """D1: when heartbeat_check returns FINISHED (our own just-completed job,
    still carrying our token during post-done archive work), _heartbeat must
    STOP beating WITHOUT cancelling the live task — cancelling would kill the
    worker's own post-done archive mid-flight (the #120 stamp-loss window).

    RED-proof: drop the FINISHED branch in _heartbeat and this hangs — outcome
    FINISHED falls through to the RENEWED `continue`, looping forever (wait_for
    below times out)."""
    job_id = uuid.uuid4()
    lease = JobLease(job_id=job_id, claim_token=uuid.uuid4(), owner_id="w:1@sha")

    # No real sleep between beats.
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 0)

    # heartbeat_check reports the job is FINISHED (terminal, own token).
    monkeypatch.setattr(
        worker_mod.jobs_repo, "heartbeat_check",
        AsyncMock(return_value=HeartbeatOutcome.FINISHED),
    )

    # Mock the DB session context manager.
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        worker_mod, "SessionLocal", MagicMock(return_value=fake_session)
    )

    # A live "pipeline" task the heartbeat could cancel — it must be left alone.
    async def _live():
        await asyncio.sleep(3600)

    live_task = asyncio.create_task(_live())
    RUNNING_JOBS[job_id] = live_task
    try:
        w = Worker(concurrency=1)
        # Correct code returns promptly; broken code loops forever.
        await asyncio.wait_for(w._heartbeat(job_id, lease), timeout=1.0)
        # Give any (erroneous) cancellation a tick to propagate before checking.
        await asyncio.sleep(0)
        assert not live_task.cancelled(), "FINISHED must NOT cancel the live task"
        assert not live_task.done(), "the live task must still be running"
    finally:
        RUNNING_JOBS.pop(job_id, None)
        live_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await live_task
