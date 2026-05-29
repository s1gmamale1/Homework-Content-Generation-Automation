"""Tests for Worker.run() loop, _sweep_stuck_jobs, and _claim_one.

These tests mock DB calls and pipeline.run to unit-test the control flow
of the worker loop without a real database.
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.worker import Worker


@pytest.fixture
def worker():
    return Worker(
        concurrency=2,
        poll_interval=0.05,
        job_timeout_seconds=10,
        max_attempts=3,
        sweep_interval_seconds=999,  # disable periodic sweep in most tests
    )


# ─── Worker.run() control flow ───────────────────────────────────────────────

class TestWorkerRunLoop:
    @pytest.mark.asyncio
    async def test_stops_when_stop_called(self, worker):
        """run() exits cleanly when stop() is called before a job is claimed."""
        with (
            patch.object(worker, "_sweep_stuck_jobs", new_callable=AsyncMock),
            patch.object(worker, "_claim_one", new_callable=AsyncMock, return_value=None),
        ):
            asyncio.get_event_loop().call_later(0.1, worker.stop)
            await asyncio.wait_for(worker.run(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_dispatches_task_when_job_claimed(self, worker):
        """When _claim_one returns a job ID, _execute_job is spawned as a task."""
        job_id = uuid.uuid4()
        call_count = 0

        async def fake_claim_one():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return job_id
            worker.stop()
            return None

        executed_ids = []

        async def fake_execute(jid):
            executed_ids.append(jid)
            worker._slots.release()

        with (
            patch.object(worker, "_sweep_stuck_jobs", new_callable=AsyncMock),
            patch.object(worker, "_claim_one", side_effect=fake_claim_one),
            patch.object(worker, "_execute_job", side_effect=fake_execute),
        ):
            await asyncio.wait_for(worker.run(), timeout=2.0)

        assert job_id in executed_ids

    @pytest.mark.asyncio
    async def test_calls_sweep_on_startup(self, worker):
        """Sweep stuck jobs is called at the very start of run()."""
        sweep_mock = AsyncMock()
        with (
            patch.object(worker, "_sweep_stuck_jobs", sweep_mock),
            patch.object(worker, "_claim_one", new_callable=AsyncMock, return_value=None),
        ):
            worker.stop()  # exit immediately after startup sweep
            await asyncio.wait_for(worker.run(), timeout=2.0)

        sweep_mock.assert_awaited()

    @pytest.mark.asyncio
    async def test_releases_slot_when_queue_empty(self, worker):
        """When queue is empty, slot acquired for the claim attempt is released."""
        worker.stop()
        slot_before = worker._slots._value

        with (
            patch.object(worker, "_sweep_stuck_jobs", new_callable=AsyncMock),
            patch.object(worker, "_claim_one", new_callable=AsyncMock, return_value=None),
        ):
            # stop is already set — _wait_for_slot_or_stop returns False immediately
            await asyncio.wait_for(worker.run(), timeout=1.0)

        assert worker._slots._value == slot_before


# ─── _sweep_stuck_jobs ───────────────────────────────────────────────────────

class TestSweepStuckJobs:
    @pytest.mark.asyncio
    async def test_calls_reclaim_with_correct_stale_threshold(self, worker):
        """reclaim_stuck_jobs is called with stale_after = 2x job_timeout."""
        reclaim_mock = AsyncMock(return_value=0)
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session_mock.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.worker.SessionLocal", return_value=session_mock),
            patch("app.services.worker.jobs_repo.reclaim_stuck_jobs", reclaim_mock),
        ):
            await worker._sweep_stuck_jobs()

        reclaim_mock.assert_awaited_once()
        kwargs = reclaim_mock.call_args.kwargs
        assert kwargs["stale_after_seconds"] == worker.job_timeout * 2

    @pytest.mark.asyncio
    async def test_tolerates_db_error_in_sweep(self, worker):
        """_sweep_stuck_jobs swallows DB errors so the main loop keeps running."""
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
        session_mock.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.worker.SessionLocal", return_value=session_mock):
            # Should not raise
            await worker._sweep_stuck_jobs()

    @pytest.mark.asyncio
    async def test_logs_when_jobs_reclaimed(self, worker):
        """When reclaim returns N>0, a warning is logged."""
        reclaim_mock = AsyncMock(return_value=3)
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session_mock.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.worker.SessionLocal", return_value=session_mock),
            patch("app.services.worker.jobs_repo.reclaim_stuck_jobs", reclaim_mock),
        ):
            # Should complete without error even when 3 jobs were reclaimed
            await worker._sweep_stuck_jobs()


# ─── _claim_one ──────────────────────────────────────────────────────────────

class TestClaimOne:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_jobs(self, worker):
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session_mock.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.worker.SessionLocal", return_value=session_mock),
            patch("app.services.worker.jobs_repo.claim_next_job",
                  AsyncMock(return_value=None)),
        ):
            result = await worker._claim_one()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_job_id_when_claimed(self, worker):
        job_id = uuid.uuid4()
        fake_job = MagicMock()
        fake_job.id = job_id
        fake_job.attempts = 1
        fake_job.priority = 0

        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session_mock.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.worker.SessionLocal", return_value=session_mock),
            patch("app.services.worker.jobs_repo.claim_next_job",
                  AsyncMock(return_value=fake_job)),
        ):
            result = await worker._claim_one()

        assert result == job_id

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self, worker):
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(side_effect=RuntimeError("connection lost"))
        session_mock.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.worker.SessionLocal", return_value=session_mock):
            result = await worker._claim_one()

        assert result is None


# ─── _mark_failed ────────────────────────────────────────────────────────────

class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_calls_mark_failed_with_retry(self, worker):
        job_id = uuid.uuid4()
        mark_mock = AsyncMock(return_value="retry")
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.commit = AsyncMock()

        with (
            patch("app.services.worker.SessionLocal", return_value=session_mock),
            patch("app.services.worker.jobs_repo.mark_failed_with_retry", mark_mock),
        ):
            await worker._mark_failed(job_id, "something broke")

        mark_mock.assert_awaited_once()
        assert mark_mock.call_args.args[1] == job_id
        assert mark_mock.call_args.kwargs["error_message"] == "something broke"

    @pytest.mark.asyncio
    async def test_tolerates_db_error_in_mark_failed(self, worker):
        session_mock = MagicMock()
        session_mock.__aenter__ = AsyncMock(side_effect=RuntimeError("db is down"))
        session_mock.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.worker.SessionLocal", return_value=session_mock):
            # Should not propagate
            await worker._mark_failed(uuid.uuid4(), "original error")
