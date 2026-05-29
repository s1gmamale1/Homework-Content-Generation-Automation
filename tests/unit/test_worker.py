"""Tests for app/services/worker.py — Worker class and helpers."""
import asyncio
import os
import socket
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.worker import Worker, _worker_id, build_worker_from_settings


# ─── _worker_id ──────────────────────────────────────────────────────────────

class TestWorkerId:
    def test_contains_hostname(self):
        assert socket.gethostname() in _worker_id()

    def test_contains_pid(self):
        assert str(os.getpid()) in _worker_id()

    def test_format_is_hostname_colon_pid(self):
        parts = _worker_id().split(":")
        assert len(parts) == 2
        assert parts[1].isdigit()


# ─── Worker construction ──────────────────────────────────────────────────────

class TestWorkerConstruction:
    def test_default_concurrency(self):
        w = Worker(concurrency=4)
        assert w.concurrency == 4

    def test_worker_id_set(self):
        w = Worker()
        assert w.id == _worker_id()

    def test_semaphore_matches_concurrency(self):
        w = Worker(concurrency=3)
        assert w._slots._value == 3

    def test_stop_event_initially_unset(self):
        w = Worker()
        assert not w._stop_event.is_set()


# ─── Worker.stop ─────────────────────────────────────────────────────────────

class TestWorkerStop:
    def test_stop_sets_event(self):
        w = Worker()
        w.stop()
        assert w._stop_event.is_set()

    def test_stop_idempotent(self):
        w = Worker()
        w.stop()
        w.stop()  # second call should not raise
        assert w._stop_event.is_set()


# ─── _wait_for_slot_or_stop ───────────────────────────────────────────────────

class TestWaitForSlotOrStop:
    @pytest.mark.asyncio
    async def test_returns_true_when_slot_acquired(self):
        w = Worker(concurrency=1)
        result = await w._wait_for_slot_or_stop()
        assert result is True
        # Release the slot we just acquired.
        w._slots.release()

    @pytest.mark.asyncio
    async def test_returns_false_when_stopped_before_slot(self):
        w = Worker(concurrency=0)  # No slots available
        w._slots = asyncio.Semaphore(0)
        # Stop immediately so the wait resolves via the stop path.
        asyncio.get_event_loop().call_soon(w.stop)
        result = await w._wait_for_slot_or_stop()
        assert result is False


# ─── _execute_job: timeout handling ──────────────────────────────────────────

class TestExecuteJobTimeout:
    @pytest.mark.asyncio
    async def test_timeout_calls_mark_failed(self):
        w = Worker(concurrency=1, job_timeout_seconds=1)
        job_id = uuid.uuid4()

        async def slow_pipeline(_):
            await asyncio.sleep(10)

        with (
            patch("app.services.worker.pipeline.run", side_effect=slow_pipeline),
            patch.object(w, "_mark_failed", new_callable=AsyncMock) as mock_fail,
        ):
            w._slots = asyncio.Semaphore(1)
            await w._slots.acquire()  # simulate slot already taken
            w._slots._value = 1  # restore so _execute_job can release

            await asyncio.wait_for(w._execute_job(job_id), timeout=3)
            mock_fail.assert_awaited_once()
            args = mock_fail.call_args[0]
            assert job_id == args[0]
            assert "timeout" in args[1].lower()


# ─── _execute_job: exception handling ────────────────────────────────────────

class TestExecuteJobException:
    @pytest.mark.asyncio
    async def test_exception_calls_mark_failed(self):
        w = Worker(concurrency=1, job_timeout_seconds=60)
        job_id = uuid.uuid4()

        async def boom(_):
            raise ValueError("something went wrong")

        with (
            patch("app.services.worker.pipeline.run", side_effect=boom),
            patch.object(w, "_mark_failed", new_callable=AsyncMock) as mock_fail,
        ):
            w._slots = asyncio.Semaphore(1)
            await w._execute_job(job_id)
            mock_fail.assert_awaited_once()
            assert "ValueError" in mock_fail.call_args[0][1]

    @pytest.mark.asyncio
    async def test_slot_always_released_on_exception(self):
        w = Worker(concurrency=1, job_timeout_seconds=60)
        job_id = uuid.uuid4()

        async def boom(_):
            raise RuntimeError("crash")

        with (
            patch("app.services.worker.pipeline.run", side_effect=boom),
            patch.object(w, "_mark_failed", new_callable=AsyncMock),
        ):
            await w._slots.acquire()
            initial_value = w._slots._value
            await w._execute_job(job_id)
            assert w._slots._value == initial_value + 1


# ─── build_worker_from_settings ──────────────────────────────────────────────

class TestBuildWorkerFromSettings:
    def test_returns_worker_instance(self):
        worker = build_worker_from_settings()
        assert isinstance(worker, Worker)

    def test_worker_uses_settings_concurrency(self):
        from app.config import settings
        worker = build_worker_from_settings()
        assert worker.concurrency == settings.worker_concurrency

    def test_worker_uses_settings_timeout(self):
        from app.config import settings
        worker = build_worker_from_settings()
        assert worker.job_timeout == settings.job_timeout_seconds

    def test_worker_uses_settings_max_attempts(self):
        from app.config import settings
        worker = build_worker_from_settings()
        assert worker.max_attempts == settings.queue_max_attempts
