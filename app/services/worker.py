"""Postgres-backed job queue worker.

Polls `homework_jobs` for `status='pending'` rows, claims them via
`SELECT ... FOR UPDATE SKIP LOCKED`, and runs `pipeline.run` to completion.
Designed to run in two topologies:

  - **Embedded** (default): one Worker task per FastAPI process, started
    in `main.py`'s lifespan. Set `WORKER_CONCURRENCY=0` to disable.

  - **Standalone**: `python -m app.services.worker` runs only the queue
    loop, no HTTP server. For horizontal scaling, run multiple of these
    behind the same Postgres instance.

Concurrency control:
  - Worker holds an `asyncio.Semaphore(N)` so at most N pipelines run
    simultaneously per worker process.
  - Across the entire deployment, `gemini.py`'s process-wide semaphore
    caps the total in-flight Gemini calls regardless of worker count.

Failure handling:
  - Any exception → `mark_failed_with_retry` (exponential backoff, up to
    `queue_max_attempts` retries; terminal failure after).
  - Pipeline that exceeds `job_timeout_seconds` → asyncio.TimeoutError →
    same retry path.
  - Worker process dies mid-pipeline → row stays in `running` until
    another worker's periodic `reclaim_stuck_jobs` sweep promotes it back.
"""

from __future__ import annotations

import asyncio
import os
import socket
import signal
from datetime import datetime, timezone
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.services import pipeline


def _worker_id() -> str:
    """Stable identity for `claimed_by`. Hostname:pid is enough to attribute
    a stuck job to a specific process in logs / Kubernetes pod listings."""
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    """Single-process queue worker. Holds N execution slots; loops forever
    claiming and running jobs until `stop()` is called."""

    def __init__(
        self,
        *,
        concurrency: int = 4,
        poll_interval: float = 2.0,
        job_timeout_seconds: int = 600,
        max_attempts: int = 3,
        sweep_interval_seconds: int = 60,
    ):
        self.id = _worker_id()
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout_seconds
        self.max_attempts = max_attempts
        self.sweep_interval = sweep_interval_seconds
        self._slots = asyncio.Semaphore(concurrency)
        self._stop_event = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._last_sweep_at = 0.0

    async def run(self) -> None:
        """Main loop. Runs until `stop()`."""
        logger.info(
            f"worker {self.id} starting | concurrency={self.concurrency} "
            f"poll={self.poll_interval}s timeout={self.job_timeout}s "
            f"max_attempts={self.max_attempts}"
        )
        # On startup, reclaim anything left in `running` from a prior crash
        # of this or any other worker. Cheap: usually 0 rows, occasionally a
        # handful.
        await self._sweep_stuck_jobs()

        try:
            while not self._stop_event.is_set():
                # Throttle sweep to once per `sweep_interval_seconds`. Doing
                # it inline (instead of a separate task) keeps the worker
                # single-threaded and easier to reason about.
                now = asyncio.get_event_loop().time()
                if now - self._last_sweep_at > self.sweep_interval:
                    await self._sweep_stuck_jobs()
                    self._last_sweep_at = now

                # Block until a slot is free OR stop is requested.
                slot_acquired = await self._wait_for_slot_or_stop()
                if not slot_acquired:
                    break  # stop requested

                claimed = await self._claim_one()
                if claimed is None:
                    # Empty queue — release the slot and wait before polling
                    # again. Use stop_event.wait(timeout) so shutdown is fast.
                    self._slots.release()
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=self.poll_interval
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                # Claimed: dispatch as a background task. The task releases
                # the slot in its `finally` block.
                task = asyncio.create_task(self._execute_job(claimed))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            await self._drain()
            logger.info(f"worker {self.id} stopped")

    def stop(self) -> None:
        """Signal the loop to exit at the next safe point. In-flight jobs
        are awaited to completion (no kill mid-pipeline)."""
        logger.info(f"worker {self.id} received stop signal")
        self._stop_event.set()

    async def _wait_for_slot_or_stop(self) -> bool:
        """Block until a slot is available or stop is requested.
        Returns True if a slot was acquired, False if stop was requested."""
        acquire_task = asyncio.create_task(self._slots.acquire())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                {acquire_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            if acquire_task in done:
                return True
            # Stop won — release the never-acquired slot intent.
            return False
        except asyncio.CancelledError:
            for t in (acquire_task, stop_task):
                if not t.done():
                    t.cancel()
            raise

    async def _claim_one(self) -> UUID | None:
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    job = await jobs_repo.claim_next_job(
                        session,
                        worker_id=self.id,
                        max_attempts=self.max_attempts,
                    )
                if job is None:
                    return None
                logger.info(
                    f"worker {self.id} claimed job={job.id} "
                    f"attempt={job.attempts}/{self.max_attempts} priority={job.priority}"
                )
                return job.id
        except Exception:
            logger.exception(f"worker {self.id} claim failed")
            return None

    async def _execute_job(self, job_id: UUID) -> None:
        """Run one pipeline. Releases the slot in `finally` so the next
        iteration of the main loop can claim another job."""
        try:
            try:
                await asyncio.wait_for(
                    pipeline.run(job_id), timeout=self.job_timeout
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"worker {self.id} job={job_id} TIMED OUT after "
                    f"{self.job_timeout}s"
                )
                await self._mark_failed(job_id, f"timeout after {self.job_timeout}s")
            except asyncio.CancelledError:
                # Worker is shutting down. Don't transition the job — the
                # row stays `running`, the next worker reclaims it via the
                # stuck-job sweep. Re-raise so the task ends cleanly.
                logger.warning(
                    f"worker {self.id} job={job_id} CANCELLED during shutdown"
                )
                raise
            except Exception as exc:
                logger.exception(
                    f"worker {self.id} job={job_id} CRASHED: {exc!r}"
                )
                await self._mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            self._slots.release()

    async def _mark_failed(self, job_id: UUID, error_message: str) -> None:
        try:
            async with SessionLocal() as session:
                outcome = await jobs_repo.mark_failed_with_retry(
                    session,
                    job_id,
                    error_message=error_message,
                    max_attempts=self.max_attempts,
                )
                await session.commit()
            if outcome == "failed":
                logger.error(
                    f"worker {self.id} job={job_id} TERMINAL failure: {error_message}"
                )
            else:
                logger.warning(
                    f"worker {self.id} job={job_id} will retry: {error_message}"
                )
        except Exception:
            # If the DB itself is down we can't do much; the stuck-job
            # sweep will eventually pick this up.
            logger.exception(
                f"worker {self.id} job={job_id} failed to record failure"
            )

    async def _sweep_stuck_jobs(self) -> None:
        """Reclaim any `running` jobs whose claim is older than 2x the job
        timeout. Cheap query, runs at startup + every sweep_interval."""
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    n = await jobs_repo.reclaim_stuck_jobs(
                        session,
                        stale_after_seconds=self.job_timeout * 2,
                    )
            if n > 0:
                logger.warning(
                    f"worker {self.id} reclaimed {n} stuck job(s) "
                    f"(stale > {self.job_timeout * 2}s)"
                )
        except Exception:
            logger.exception(f"worker {self.id} stuck-job sweep failed")

    async def _drain(self) -> None:
        """Wait for in-flight tasks to finish before returning. Bounded by
        the sum of remaining job timeouts; in practice <30s for graceful
        shutdown if pipelines are nearly done."""
        if not self._tasks:
            return
        logger.info(
            f"worker {self.id} draining {len(self._tasks)} in-flight job(s)"
        )
        await asyncio.gather(*self._tasks, return_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────


def build_worker_from_settings() -> Worker:
    """Construct a Worker using values from `settings`. Single source of
    truth for embedded and standalone modes."""
    return Worker(
        concurrency=settings.worker_concurrency,
        poll_interval=settings.worker_poll_interval,
        job_timeout_seconds=settings.job_timeout_seconds,
        max_attempts=settings.queue_max_attempts,
    )


async def run_standalone() -> None:
    """Entrypoint for `python -m app.services.worker`. Loads prompts,
    starts the worker, installs SIGTERM/SIGINT handlers for graceful
    shutdown."""
    from app.log import configure as configure_logging
    from app.services.prompts import load_all as load_prompts

    configure_logging()
    load_prompts()
    logger.info("standalone worker bootstrapping")

    worker = build_worker_from_settings()

    # Graceful shutdown on SIGTERM / SIGINT (Ctrl+C, container stop).
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)

    await worker.run()


def main() -> None:
    asyncio.run(run_standalone())


if __name__ == "__main__":
    main()
