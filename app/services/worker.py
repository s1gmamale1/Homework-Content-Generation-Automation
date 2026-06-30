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
from datetime import datetime, timedelta, timezone
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.models.base import _utcnow
from app.repositories import batches as batches_repo
from app.repositories import budget as budget_repo
from app.repositories import cost as cost_repo
from app.repositories import jobs as jobs_repo
from app.repositories import workers as workers_repo
from app.services import agent, pipeline, providers
from app.services.errors import SessionLimitPause


# Maps job_id -> the in-flight _execute_job task, so a same-process cancel
# endpoint can cancel the exact running job instantly. Process-local: in a
# separate-pod deployment the API's registry is empty and the owning worker
# self-cancels via the heartbeat (see _heartbeat).
RUNNING_JOBS: dict[UUID, asyncio.Task] = {}


def _api_capable(env: dict) -> dict[str, bool]:
    """Shared truthiness for api key availability. Used by both _compute_capabilities
    and _capability_blob so the two never drift on the acceptance rules."""
    return {
        "claude": bool(env.get("ANTHROPIC_API_KEY")),
        "gemini": bool(
            env.get("GEMINI_API_KEY")
            or (env.get("GOOGLE_APPLICATION_CREDENTIALS") and env.get("GOOGLE_CLOUD_PROJECT"))
        ),
    }


def _capability_blob(env: dict) -> dict:
    """Published worker capability blob — provider × transport view.
    Shape: {"cli": {name: bool ...}, "api": {"claude": bool, "gemini": bool}}.
    The cli flags follow shutil.which (via agent.provider_cli_installed); the api
    flags use the same acceptance rules as _compute_capabilities via _api_capable.
    Computed once at module load (CAPABILITY_BLOB) and published on each heartbeat."""
    api = _api_capable(env)
    return {
        "cli": {name: agent.provider_cli_installed(name) for name in providers.PROVIDERS},
        "api": {"claude": api["claude"], "gemini": api["gemini"]},
    }


def _compute_capabilities(env) -> dict:
    """Credential-only api capability. The claim gate evaluates each job's own
    stamped provider x transport against these; no model/provider value lives on
    the worker anymore (those moved to the launch_defaults DB row)."""
    cap = _api_capable(env)
    return {"can_claude_api": cap["claude"], "can_gemini_api": cap["gemini"]}


# Computed once at module load (the locked fail-fast mechanism).
CAPABILITIES: dict = _compute_capabilities(os.environ)

# Published capability blob: provider × transport view for the fleet registry.
# Sent on every heartbeat so the head can display which providers each worker
# can serve — computed once at startup (a restart is needed to update anyway).
CAPABILITY_BLOB: dict = _capability_blob(os.environ)


def _rebind_capabilities() -> None:
    """Recompute the frozen capability globals from the CURRENT os.environ after a
    live SA-key apply/scrub. The claim gate reads CAPABILITIES at call time
    (worker.py _claim_one) and the heartbeat publishes CAPABILITY_BLOB, so
    reassigning the module globals is what makes a freshly-keyed worker start
    claiming gemini-api jobs without a restart."""
    global CAPABILITIES, CAPABILITY_BLOB
    CAPABILITIES = _compute_capabilities(os.environ)
    CAPABILITY_BLOB = _capability_blob(os.environ)


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
        self._last_budget_check_at = 0.0
        self._cooldown_until: datetime | None = None

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
                if now - self._last_budget_check_at > settings.cost_check_interval_seconds:
                    await self._budget_monitor()
                    self._last_budget_check_at = now

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

    def _in_cooldown(self) -> bool:
        """True when this worker is session-limited and should not claim jobs.

        Uses the host clock (datetime.now(timezone.utc)), NOT the DB clock —
        NTP skew between the host and DB server can cause slight drift
        (fleet-net-1 ops half). In practice the cooldown is ≥1h so sub-second
        skew is irrelevant.
        """
        return (
            self._cooldown_until is not None
            and datetime.now(timezone.utc) < self._cooldown_until
        )

    async def _claim_one(self) -> UUID | None:
        # Whole-worker cooldown gate: when session-limited, skip claiming until
        # the reset time passes. A healthy peer will grab the requeued job instead.
        if self._in_cooldown():
            remaining = (self._cooldown_until - datetime.now(timezone.utc)).total_seconds()
            sleep_for = min(remaining, self.poll_interval)
            logger.debug(
                f"worker {self.id} in session-limit cooldown for "
                f"{remaining:.0f}s more — skipping claim"
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=max(sleep_for, 0.1)
                )
            except asyncio.TimeoutError:
                pass
            return None
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    # Read the fleet-level budget state once per claim attempt.
                    # If api_paused_at is non-NULL, the fleet gate is active and
                    # no api-spending job may be claimed (cli jobs are unaffected).
                    budget_state = await budget_repo.get_state(session)
                    fleet_api_paused = budget_state.api_paused_at is not None

                    job = await jobs_repo.claim_next_job(
                        session,
                        worker_id=self.id,
                        max_attempts=self.max_attempts,
                        capabilities=CAPABILITIES,
                        fleet_api_paused=fleet_api_paused,
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
            except SessionLimitPause as e:
                # Requeue without burning a retry attempt so a healthy peer
                # can pick it up. Self-cooldown until the session resets.
                try:
                    async with SessionLocal() as session:
                        await jobs_repo.requeue_session_limited(
                            session, job_id, error=str(e)
                        )
                        await session.commit()
                except Exception:
                    logger.exception(
                        f"worker {self.id} job={job_id} requeue_session_limited failed"
                    )
                self._cooldown_until = e.reset_at or (
                    _utcnow() + timedelta(seconds=settings.session_limit_default_cooldown_seconds)
                )
                logger.warning(
                    f"worker {self.id} job={job_id} session-limit → requeued + "
                    f"worker cooldown until {self._cooldown_until.isoformat()}"
                )
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

    async def _budget_monitor(self) -> None:
        """Periodic kill-switch: trip or clear the pause gates based on live cost.

        Runs at most once per `settings.cost_check_interval_seconds`. One
        session for the whole check — all reads/writes are consistent within it.

        Per-batch gate (reason="batch-cap"):
          For each active (un-paused) batch: if its api spend exceeds
          cost_cap_batch_usd (and the cap is enabled), pause it.
          For each batch already paused with reason "batch-cap": if its cost
          is now at/under cap (or the cap was disabled), unpause it.
          Batches paused with a DIFFERENT reason (manual/fleet) are never touched.

        Fleet gate (reason="fleet-daily-cap"):
          If fleet api spend over the last 24h exceeds cost_cap_fleet_daily_usd
          (and the cap is enabled), set the fleet-level api pause.
          If the fleet is paused with reason "fleet-daily-cap" and cost is now
          at/under cap (or the cap disabled), clear it.
          Only reconciles its OWN reason — never clears a manual fleet pause.
        """
        if (
            settings.cost_cap_batch_usd <= 0
            and settings.cost_cap_fleet_daily_usd <= 0
        ):
            return
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    # ── Per-batch ─────────────────────────────────────────
                    cap = settings.cost_cap_batch_usd
                    if cap > 0:
                        # Trip: active batches over cap → pause("batch-cap")
                        for batch_id in await batches_repo.active_batch_ids(session):
                            cost = await cost_repo.batch_api_cost_usd(session, batch_id)
                            if cost > cap:
                                logger.warning(
                                    f"budget-monitor: batch={batch_id} api cost ${cost:.4f} "
                                    f"> cap ${cap:.4f} — pausing (batch-cap)"
                                )
                                await batches_repo.pause_batch(session, batch_id, "batch-cap")

                    # Reconcile: batch-cap-paused batches now at/under cap → unpause
                    for batch_id in await batches_repo.paused_batch_ids_by_reason(session, "batch-cap"):
                        if cap <= 0:
                            # Cap disabled — clear our own pause
                            await batches_repo.unpause_batch(session, batch_id)
                            logger.info(
                                f"budget-monitor: batch={batch_id} batch-cap disabled — unpausing"
                            )
                        else:
                            cost = await cost_repo.batch_api_cost_usd(session, batch_id)
                            if cost <= cap:
                                logger.info(
                                    f"budget-monitor: batch={batch_id} api cost ${cost:.4f} "
                                    f"<= cap ${cap:.4f} — unpausing"
                                )
                                await batches_repo.unpause_batch(session, batch_id)

                    # ── Fleet ─────────────────────────────────────────────
                    fleet_cap = settings.cost_cap_fleet_daily_usd
                    since = _utcnow() - timedelta(hours=24)
                    fleet_cost = await cost_repo.fleet_api_cost_usd(session, since)

                    budget_state = await budget_repo.get_state(session)
                    currently_fleet_paused_by_us = (
                        budget_state.api_paused_at is not None
                        and budget_state.api_paused_reason == "fleet-daily-cap"
                    )

                    if fleet_cap > 0 and fleet_cost > fleet_cap:
                        if not currently_fleet_paused_by_us:
                            logger.warning(
                                f"budget-monitor: fleet api cost ${fleet_cost:.4f} "
                                f"> cap ${fleet_cap:.4f} — setting fleet-daily-cap pause"
                            )
                        await budget_repo.set_api_paused(session, "fleet-daily-cap")
                    elif currently_fleet_paused_by_us:
                        logger.info(
                            f"budget-monitor: fleet api cost ${fleet_cost:.4f} "
                            f"<= cap ${fleet_cap:.4f} (or cap disabled) — clearing fleet-daily-cap"
                        )
                        await budget_repo.clear_api_paused(session)
        except Exception:
            logger.exception(f"worker {self.id} budget monitor failed")

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
                    n_failed = await jobs_repo.fail_exhausted_pending_jobs(
                        session,
                        max_attempts=settings.queue_max_attempts,
                    )
            if n > 0 or n_cancel > 0:
                logger.warning(
                    f"worker {self.id} reclaimed {n} stuck job(s) "
                    f"and finalized {n_cancel} stale-cancelling job(s) "
                    f"(stale > {settings.reclaim_stale_seconds}s)"
                )
            if n_failed > 0:
                logger.warning(
                    f"worker {self.id} failed {n_failed} attempts-exhausted pending job(s) "
                    f"(stale-pending sweep)"
                )
            if n_pruned > 0:
                logger.info(
                    f"worker {self.id} pruned {n_pruned} dead worker row(s) "
                    f"(no heartbeat for > {settings.worker_registry_prune_seconds}s)"
                )
        except Exception:
            logger.exception(f"worker {self.id} stuck-job sweep failed")

    async def _drain_check_and_beat(self, session) -> bool:
        """Read own status; if "draining" call stop() and return False (no beat).
        Any other value (including None = unregistered) falls through to the
        normal upsert_heartbeat and returns True. Extracted for testability."""
        status = await workers_repo.get_status(session, self.id)
        if status == "draining":
            logger.info(
                f"worker {self.id} drain requested -> stopping "
                f"(no new claims; in-flight will finish)"
            )
            self.stop()
            return False  # do NOT upsert "online" — that would clobber the drain signal
        await workers_repo.upsert_heartbeat(session, self.id, capabilities=CAPABILITY_BLOB)
        return True

    async def _registry_heartbeat(self) -> None:
        """Register this worker / refresh its heartbeat in the fleet `workers`
        table so the head-side liveness view knows this PC is alive.
        Reads own status first: if the head has set it to "draining", calls
        self.stop() and skips the upsert so the drain signal is not clobbered.
        Best-effort: a failed beat is logged, never fatal."""
        try:
            async with SessionLocal() as session:
                kept_beating = await self._drain_check_and_beat(session)
                if kept_beating:
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
    # loop.add_signal_handler is Unix-only; on Windows it raises
    # NotImplementedError, so fall back to signal.signal (handles Ctrl+C).
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: worker.stop())

    await worker.run()


def main() -> None:
    asyncio.run(run_standalone())


if __name__ == "__main__":
    main()
