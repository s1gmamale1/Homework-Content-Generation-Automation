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
import contextlib
import os
import socket
import signal
from datetime import datetime, timezone
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import jobs as jobs_repo
from app.repositories import workers as workers_repo
from app.services import model_tiers, pipeline
from app.services.agent_models import default_model


# Maps job_id -> the in-flight _execute_job task, so a same-process cancel
# endpoint can cancel the exact running job instantly. Process-local: in a
# separate-pod deployment the API's registry is empty and the owning worker
# self-cancels via the heartbeat (see _heartbeat).
RUNNING_JOBS: dict[UUID, asyncio.Task] = {}


def _compute_capabilities(env, judge_provider: str, judge_model: str, extract_provider: str) -> dict:
    """Per-side api capability + role-level readiness (Phase 4.1 §4/§4a).
    The gemini side is satisfied by an AI-Studio key OR a Vertex SA pair
    (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT — fleet-api-6).
    Half-configured counts as NOT capable (mirrors `agent._auth_env`'s
    acceptance rules exactly). `claim_next_job` ANDs these per-role flags
    against each job's resolved transports, so e.g. an api-content gemini job
    with cli judge/extract needs NO anthropic key."""
    can_claude = bool(env.get("ANTHROPIC_API_KEY"))
    can_gemini = bool(
        env.get("GEMINI_API_KEY")
        or (env.get("GOOGLE_APPLICATION_CREDENTIALS") and env.get("GOOGLE_CLOUD_PROJECT"))
    )
    cap = {"claude": can_claude, "gemini": can_gemini}
    fb_provider, _ = model_tiers._SELF_FALLBACK
    return {
        "can_claude_api": can_claude,
        "can_gemini_api": can_gemini,
        "judge_api_ok": cap.get(judge_provider, False),
        # §4a: jobs generating ON the judge pair get judged by _SELF_FALLBACK
        "judge_fallback_api_ok": cap.get(fb_provider, False),
        "extract_api_ok": cap.get(extract_provider, False),
        "judge_pair": (judge_provider, judge_model),
    }


# Computed once at module load (the locked fail-fast mechanism).
CAPABILITIES: dict = _compute_capabilities(
    os.environ, settings.judge_provider, settings.judge_model, settings.extract_provider
)


def _worker_id() -> str:
    """Stable identity for `claimed_by`. Hostname:pid is enough to attribute
    a stuck job to a specific process in logs / Kubernetes pod listings."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _warn_if_gemini_selected_type() -> None:
    """Advisory startup guard (spec §4): if `~/.gemini/settings.json` carries
    `security.auth.selectedType`, an interactive gemini run has re-persisted it.
    That pins gemini's auth and silently breaks the api/cli transport toggle.
    Best-effort: any error (missing file, bad JSON, odd shape) is swallowed —
    this is purely advisory and must never block worker startup."""
    try:
        import json
        from pathlib import Path

        path = Path.home() / ".gemini" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        selected = (
            data.get("security", {}).get("auth", {}).get("selectedType")
        )
        if selected:
            logger.warning(
                "~/.gemini/settings.json has security.auth.selectedType="
                f"{selected!r} — an interactive gemini run re-persisted it and "
                "will silently break the api/cli transport toggle"
            )
    except Exception:
        pass  # advisory only — never fatal


def _warn_if_gemini_reads_local_env() -> None:
    """Advisory startup guard (2026-06-12, the .env self-poisoning incident):
    unless `~/.gemini/settings.json` sets `advanced.ignoreLocalEnv: true`,
    gemini-cli dotenv-loads the nearest project `.env` INSIDE each spawn —
    importing GOOGLE_CLOUD_PROJECT (and even GEMINI_API_KEY; both are on the
    CLI's auth-var whitelist, applied even in untrusted folders) AFTER
    `_auth_env` already scrubbed the parent env, defeating the scrub. On a
    worker whose repo `.env` carries Vertex creds this 403s EVERY cli gemini
    spawn ("Cloud Code Private API has not been used in project …" — proven
    live on the head PC: same call with `.env` renamed away → success). The
    setting needs gemini-cli >= 0.46; note `--ignore-env` is read by
    loadEnvironment but NOT registered as a CLI option, so passing it as argv
    hard-fails ("Unknown arguments"). Best-effort: any error is swallowed —
    advisory only, never blocks startup."""
    try:
        import json
        from pathlib import Path

        path = Path.home() / ".gemini" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("advanced", {}).get("ignoreLocalEnv"):
            logger.warning(
                "~/.gemini/settings.json lacks advanced.ignoreLocalEnv=true — "
                "gemini-cli imports GOOGLE_CLOUD_PROJECT/GEMINI_API_KEY from the "
                "repo's .env inside every spawn, bypassing _auth_env (cli spawns "
                "can 403 or silently bill the wrong account)"
            )
    except Exception:
        pass  # advisory only — never fatal


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
        # Fail-fast capability check (Phase 4.1 §4): enumerate the per-side api
        # capabilities. A missing side doesn't block all api jobs anymore —
        # only the jobs whose resolved role transports need that side.
        if not (CAPABILITIES["can_claude_api"] and CAPABILITIES["can_gemini_api"]):
            logger.warning(
                f"api capability: claude={CAPABILITIES['can_claude_api']} "
                f"gemini={CAPABILITIES['can_gemini_api']} — api jobs needing "
                "the missing side won't be claimed (claude side: "
                "ANTHROPIC_API_KEY; gemini side: GEMINI_API_KEY or Vertex "
                "GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT)"
            )
        # NULL-model gate edge (claim gate v2 §4a): a model=NULL job bypasses
        # the SQL judge-pair equality. Safe while JUDGE_MODEL differs from the
        # judge provider's default model (judge_model_for resolves None via
        # default_model). If an operator pins them equal, the edge opens —
        # warn loudly (Task 2's AuthEnvError still keeps the failure typed).
        if settings.judge_model == default_model(settings.judge_provider):
            logger.warning(
                "JUDGE_MODEL equals default_model(JUDGE_PROVIDER) — the "
                "NULL-model claim-gate edge is open: model=NULL jobs on the "
                "judge provider resolve to the judge pair at judge time but "
                "bypass the SQL pair-equality in claim_next_job"
            )
        _warn_if_gemini_selected_type()
        _warn_if_gemini_reads_local_env()

        # On startup, reclaim anything left in `running` from a prior crash
        # of this or any other worker. Cheap: usually 0 rows, occasionally a
        # handful.
        await self._sweep_stuck_jobs()

        # Registry heartbeat on its OWN task so a busy worker (all slots full)
        # still reports alive — the main loop blocks while slots are occupied.
        registry_hb = asyncio.create_task(self._registry_heartbeat_loop())

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
            registry_hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await registry_hb   # let the cancellation settle (matches _execute_job)
            await self._drain()
            # Best-effort deregistration AFTER the heartbeat task is dead (a
            # live beat would just re-insert the row). Kills/crashes skip this
            # path entirely — prune_stale in the sweep is the real cleanup.
            try:
                async with SessionLocal() as session:
                    await workers_repo.deregister(session, self.id)
                    await session.commit()
                logger.info(f"worker {self.id} deregistered from fleet registry")
            except Exception:
                logger.warning(
                    f"worker {self.id} deregistration failed (prune will catch it)"
                )
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
                        capabilities=CAPABILITIES,
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

    async def _heartbeat(self, job_id: UUID) -> None:
        """Refresh the job's claim while its pipeline runs, AND notice a
        cross-process cancel: if the API (possibly in another pod) flipped the
        job to `cancelling`, self-cancel the local task so its CLIs die."""
        while True:
            await asyncio.sleep(settings.heartbeat_seconds)
            try:
                async with SessionLocal() as session:
                    status = await jobs_repo.get_status(session, job_id)
                    if status == "cancelling":
                        task = RUNNING_JOBS.get(job_id)
                        if task is not None:
                            task.cancel()
                        return  # nothing more to do; the task will finalize
                    await jobs_repo.touch_claim(session, job_id)
                    await session.commit()
            except Exception:
                logger.warning(f"worker {self.id} heartbeat failed for job={job_id}")

    async def _execute_job(self, job_id: UUID) -> None:
        """Run one pipeline. Releases the slot in `finally` so the next
        iteration of the main loop can claim another job."""
        RUNNING_JOBS[job_id] = asyncio.current_task()
        hb = asyncio.create_task(self._heartbeat(job_id))
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
                # Distinguish a user-cancel from a worker-shutdown cancel.
                cancelling = False
                try:
                    async with SessionLocal() as session:
                        cancelling = (await jobs_repo.get_status(session, job_id)) == "cancelling"
                except Exception:
                    logger.warning(f"worker {self.id} job={job_id} cancel status read failed")
                if cancelling:
                    # User cancel. Clear our own cancellation so a rare
                    # double-cancel (idempotent endpoint hit twice, or shutdown
                    # racing the user-cancel) can't re-fire a CancelledError at
                    # the finalize awaits - `except Exception` can't catch it
                    # (CancelledError is BaseException). Python 3.13 uncancel().
                    # shield() is belt-and-suspenders; task 8's stale-cancelling
                    # sweep is the ultimate backstop for anything that slips past.
                    current = asyncio.current_task()
                    if current is not None:
                        current.uncancel()

                    async def _finalize() -> None:
                        async with SessionLocal() as session:
                            await jobs_repo.mark_cancelled(session, job_id)
                            await session.commit()

                    try:
                        await asyncio.shield(_finalize())
                        logger.warning(f"worker {self.id} job={job_id} CANCELLED by user")
                    except Exception:
                        logger.exception(f"worker {self.id} job={job_id} cancel finalize failed")
                    # do NOT re-raise: the job is finalized cancelled.
                else:
                    # Shutdown cancel - leave the row running for reclaim.
                    logger.warning(f"worker {self.id} job={job_id} CANCELLED during shutdown")
                    raise
            except Exception as exc:
                logger.exception(
                    f"worker {self.id} job={job_id} CRASHED: {exc!r}"
                )
                await self._mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            RUNNING_JOBS.pop(job_id, None)
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb   # let the cancellation settle — avoids a stray "Task destroyed" warning
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
        """Reclaim any `running` jobs whose claim is older than the lease
        window (`settings.reclaim_stale_seconds`). With heartbeats keeping
        live jobs fresh, this window can safely be shorter than the old 2x
        job-timeout heuristic. Cheap query, runs at startup + every
        sweep_interval."""
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    n = await jobs_repo.reclaim_stuck_jobs(
                        session,
                        stale_after_seconds=settings.reclaim_stale_seconds,
                    )
                    n_cancel = await jobs_repo.reclaim_stale_cancelling(
                        session,
                        stale_after_seconds=settings.reclaim_stale_seconds,
                    )
                    n_pruned = await workers_repo.prune_stale(
                        session,
                        older_than_seconds=settings.worker_registry_prune_seconds,
                    )
            if n > 0 or n_cancel > 0:
                logger.warning(
                    f"worker {self.id} reclaimed {n} stuck job(s) "
                    f"and finalized {n_cancel} stale-cancelling job(s) "
                    f"(stale > {settings.reclaim_stale_seconds}s)"
                )
            if n_pruned > 0:
                logger.info(
                    f"worker {self.id} pruned {n_pruned} dead worker row(s) "
                    f"(no heartbeat for > {settings.worker_registry_prune_seconds}s)"
                )
        except Exception:
            logger.exception(f"worker {self.id} stuck-job sweep failed")

    async def _registry_heartbeat(self) -> None:
        """Register this worker / refresh its heartbeat in the fleet `workers`
        table so the head-side liveness view knows this PC is alive.
        Best-effort: a failed beat is logged, never fatal."""
        try:
            async with SessionLocal() as session:
                await workers_repo.upsert_heartbeat(session, self.id)
                await session.commit()
        except Exception:
            logger.warning(f"worker {self.id} registry heartbeat failed")

    async def _registry_heartbeat_loop(self) -> None:
        """Beat on its OWN task — NOT the main loop — so a busy worker (all
        slots full with long jobs, main loop blocked in _wait_for_slot_or_stop)
        still reports alive. Mirrors the per-job _heartbeat; shutdown-aware via
        stop_event so it exits promptly."""
        await self._registry_heartbeat()  # register immediately on startup
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=settings.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                await self._registry_heartbeat()

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
