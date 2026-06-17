from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, func, literal, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import HomeworkJob, PhaseOutput


async def create(
    session: AsyncSession,
    *,
    book_id: UUID,
    toc_entry_id: UUID,
    subject: str,
    status: str = "pending",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    batch_id: Optional[UUID] = None,
    transport: str = "cli",
    extract_transport: str = "inherit",
    judge_transport: str = "inherit",
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> HomeworkJob:
    kwargs: dict[str, Any] = dict(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        status=status,
        transport=transport,
        extract_transport=extract_transport,
        judge_transport=judge_transport,
    )
    if provider is not None:
        kwargs["provider"] = provider
    if model is not None:
        kwargs["model"] = model
    if batch_id is not None:
        kwargs["batch_id"] = batch_id
    for _k, _v in (
        ("extract_provider", extract_provider),
        ("extract_model", extract_model),
        ("judge_provider", judge_provider),
        ("judge_model", judge_model),
    ):
        if _v is not None:
            kwargs[_k] = _v
    job = HomeworkJob(**kwargs)
    session.add(job)
    await session.flush()
    return job


async def get(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    return await session.get(HomeworkJob, job_id)


async def get_with_phases(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .options(selectinload(HomeworkJob.phase_outputs))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_active_for_section(
    session: AsyncSession,
    book_id: UUID,
    toc_entry_id: UUID,
    *,
    transport: Optional[str] = None,
) -> Optional[HomeworkJob]:
    """Return the most recent pending/running/done job for the (book, section).

    `done` is included so idempotent regenerate returns the existing successful
    result. Callers that want to force a new run must pass `force=True` and skip
    this lookup entirely.

    When `transport` is given, the lookup is scoped to jobs of that transport —
    so an api batch over a cli-generated book doesn't find the cli jobs and skip
    every lesson (spec §9a). Default `None` preserves the transport-blind
    behavior for existing callers.
    """
    conds = [
        HomeworkJob.book_id == book_id,
        HomeworkJob.toc_entry_id == toc_entry_id,
        HomeworkJob.status.in_(["pending", "running", "done"]),
    ]
    if transport is not None:
        conds.append(HomeworkJob.transport == transport)
    stmt = (
        select(HomeworkJob)
        .where(*conds)
        .order_by(HomeworkJob.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_section_for_generate(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID
) -> None:
    """Serialize generate calls for the same (book, section) using a Postgres
    transaction-scoped advisory lock. The lock is auto-released on commit /
    rollback, so a fast follow-up request waits behind the in-flight one and
    then sees the just-created job via `find_active_for_section`.

    Without this lock, two concurrent POSTs (e.g., a double-click) both
    observe "no active job" and both insert — producing duplicate jobs that
    waste Gemini calls and confuse the SSE consumer.

    Uses `pg_advisory_xact_lock(bigint)` with a key derived from blake2b
    of the (book_id, toc_entry_id) pair so it's stable across requests and
    collision-resistant across other lock users in the same database.
    """
    import hashlib

    digest = hashlib.blake2b(
        f"generate:{book_id}:{toc_entry_id}".encode(),
        digest_size=8,
    ).digest()
    # Postgres bigint is signed 64-bit. blake2b digest_size=8 → 8 bytes →
    # int.from_bytes(signed=True) gives a value in [-2^63, 2^63).
    key = int.from_bytes(digest, "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


async def set_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    current_phase: Optional[str] = None,
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.status = status
    if started_at is not None:
        job.started_at = started_at
    if completed_at is not None:
        job.completed_at = completed_at
    if error_message is not None:
        job.error_message = error_message
    if current_phase is not None:
        job.current_phase = current_phase


async def set_notion_archived(
    session: AsyncSession, job_id: UUID, notion_archived_at: datetime
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_archived_at = notion_archived_at
    job.notion_skip_reason = None   # success clears any prior skip marker


async def set_notion_skip_reason(
    session: AsyncSession, job_id: UUID, reason: Optional[str]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_skip_reason = reason


async def reset_for_retry(
    session: AsyncSession, job_id: UUID
) -> Optional[HomeworkJob]:
    """Reset a failed job back to 'pending' so the worker can re-claim it.

    Clears `error_message`, `current_phase`, `started_at`, `completed_at`, and
    resets the queue retry counter (`attempts`) so the worker treats this as a
    fresh attempt rather than counting it against `queue_max_attempts`. The
    pipeline is idempotent against existing phase rows (`phase_repo.create_or_reset`
    handles the upsert), so no phase-output cleanup is needed here.

    Returns the updated row, or None if the job no longer exists.
    """
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return None
    job.status = "pending"
    job.error_message = None
    job.current_phase = None
    job.started_at = None
    job.completed_at = None
    job.attempts = 0
    return job


async def list_running_for_sweep(session: AsyncSession) -> list[HomeworkJob]:
    stmt = select(HomeworkJob).where(HomeworkJob.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())


async def latest_by_section(
    session: AsyncSession, book_id: UUID
) -> dict[UUID, HomeworkJob]:
    """One row per (book, section): the most recent job for that section.

    Uses Postgres' `DISTINCT ON` for a single-pass index scan instead of a
    correlated subquery. Returns an empty dict if the book has no jobs.
    """
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.book_id == book_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {row.toc_entry_id: row for row in rows}


# ─────────────────────────────────────────────────────────────────────────
# Queue (Postgres-backed work queue using FOR UPDATE SKIP LOCKED)
# ─────────────────────────────────────────────────────────────────────────


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    max_attempts: int,
    capabilities: Optional[dict] = None,
) -> Optional[HomeworkJob]:
    """Atomically claim the next pending job for this worker.

    Uses `FOR UPDATE SKIP LOCKED` so multiple workers polling concurrently
    never collide on the same row — Postgres serializes the dispatch.
    Returns None if no claimable job is available (worker should sleep
    and retry).

    Eligibility rules (claim gate v2 — Phase 4.1 §4/§4a):
      - status == 'pending'
      - scheduled_at <= NOW() (so delayed retries don't fire early)
      - attempts < max_attempts (don't reclaim poison-pill jobs forever)
      - per-role capability routing against `capabilities` (the dict
        `worker._compute_capabilities` builds at startup):
          * content: transport == 'cli', OR the worker has the api capability
            for the job's content provider (`can_claude_api`/`can_gemini_api`).
          * judge: if the job's resolved judge transport is api
            (judge_transport == 'api', or 'inherit' under an api job), the
            worker needs `judge_api_ok` — EXCEPT when the job generates ON the
            configured judge pair, in which case `judge_model_for` self-falls
            back to `model_tiers._SELF_FALLBACK` and the worker needs
            `judge_fallback_api_ok` instead.
          * extract: if the job's resolved extract transport is api, the
            worker needs `extract_api_ok`.
        This is the fail-fast gate: a worker missing a needed side never
        claims the job (covers the extract-failover path too, since the gate
        is at claim time, before any spawn). Default `capabilities=None` is
        the most-restrictive all-False set, i.e. cli-only.

    Order: highest priority first, then oldest scheduled_at first (FIFO
    within a priority band).
    """
    caps = capabilities or {}
    judge_pair = caps.get("judge_pair") or (None, None)
    content_ok = or_(
        HomeworkJob.transport == "cli",
        and_(HomeworkJob.provider == "claude", literal(bool(caps.get("can_claude_api")))),
        and_(HomeworkJob.provider == "gemini", literal(bool(caps.get("can_gemini_api")))),
    )
    judge_needs_api = or_(
        HomeworkJob.judge_transport == "api",
        and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"),
    )
    # §4a: jobs generating ON the configured judge pair are judged by the
    # self-fallback provider — gate on ITS capability for exactly those jobs.
    # NULL-model note: a model=NULL job bypasses this SQL equality; safe today
    # because judge_model_for resolves None via default_model(provider) (sonnet)
    # != the judge pair (opus). AuthEnvError keeps any future drift loud.
    # `coalesce(model, '')` keeps the NOT-pair branch usable for NULL-model
    # jobs: bare `NULL == 'x'` is SQL NULL, and not_(NULL AND ...) stays NULL,
    # which silently excluded NULL-model jobs whose provider == the judge
    # provider (proven by test_null_model_job_claims_via_not_pair_branch).
    job_is_judge_pair = and_(
        HomeworkJob.provider == (judge_pair[0] or ""),
        func.coalesce(HomeworkJob.model, "") == (judge_pair[1] or ""),
    )
    judge_ok = or_(
        not_(judge_needs_api),
        and_(job_is_judge_pair, literal(bool(caps.get("judge_fallback_api_ok")))),
        and_(not_(job_is_judge_pair), literal(bool(caps.get("judge_api_ok")))),
    )
    extract_needs_api = or_(
        HomeworkJob.extract_transport == "api",
        and_(HomeworkJob.extract_transport == "inherit", HomeworkJob.transport == "api"),
    )
    extract_ok = or_(not_(extract_needs_api), literal(bool(caps.get("extract_api_ok"))))

    pick_stmt = (
        select(HomeworkJob.id)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= func.now())
        .where(HomeworkJob.attempts < max_attempts)
        .where(content_ok)
        .where(judge_ok)
        .where(extract_ok)
        .order_by(HomeworkJob.priority.desc(), HomeworkJob.scheduled_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job_id = (await session.execute(pick_stmt)).scalar_one_or_none()
    if job_id is None:
        return None

    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="running",
            claimed_at=func.now(),
            claimed_by=worker_id,
            attempts=HomeworkJob.attempts + 1,
            last_attempt_at=func.now(),
            started_at=func.now(),
            error_message=None,  # clear stale message from prior attempt
        )
    )
    return await session.get(HomeworkJob, job_id)


async def touch_claim(session: AsyncSession, job_id: UUID) -> None:
    """Heartbeat: refresh claimed_at on a still-running job so the lease-TTL
    reclaim never treats a live worker's job as orphaned. No-ops once the job
    leaves `running` (done/failed), so it can't resurrect a finished row."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(claimed_at=func.now())
    )


async def reclaim_stuck_jobs(
    session: AsyncSession, *, stale_after_seconds: int
) -> int:
    """Promote `running` jobs whose claim is stale back to `pending`.

    Triggered on worker startup (recovers jobs whose worker died mid-run)
    and periodically by the running worker (recovers jobs from peer crashes).
    Returns the number of rows reclaimed.

    Stuck = running and (claimed_at is NULL or claimed_at < now - stale).
    The `attempts` counter persists, so a poison-pill job runs at most
    `max_attempts` times before being marked failed terminally.
    """
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "running")
        .where(
            (HomeworkJob.claimed_at.is_(None))
            | (HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        )
        .values(
            status="pending",
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
        )
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def mark_failed_with_retry(
    session: AsyncSession,
    job_id: UUID,
    *,
    error_message: str,
    max_attempts: int,
    backoff_seconds: int = 30,
) -> str:
    """Record a failed attempt. Either re-schedules with exponential backoff
    (status='pending', scheduled_at in the future) or marks terminal failure
    (status='failed') if attempts exhausted.

    Returns the resulting status ('pending' = will retry, 'failed' = terminal).
    """
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return "missing"

    if job.attempts >= max_attempts:
        # Terminal: stay in failed, store the error.
        await session.execute(
            update(HomeworkJob)
            .where(HomeworkJob.id == job_id)
            .values(
                status="failed",
                completed_at=func.now(),
                error_message=error_message,
                last_error=error_message,
                claimed_at=None,
                claimed_by=None,
            )
        )
        return "failed"

    # Retry: bump scheduled_at by exponential backoff (30s, 60s, 120s, ...).
    delay = backoff_seconds * (2 ** (job.attempts - 1))
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="pending",
            scheduled_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay),
            last_error=error_message,
            current_phase=None,
            claimed_at=None,
            claimed_by=None,
        )
    )
    return "pending"


async def queue_depth(session: AsyncSession) -> int:
    """Count of pending jobs eligible to run right now. Used by the
    `/generate` endpoint to enforce backpressure."""
    stmt = (
        select(func.count())
        .select_from(HomeworkJob)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= func.now())
    )
    return int((await session.execute(stmt)).scalar_one())


# ─────────────────────────────────────────────────────────────────────────
# Cancellation
# ─────────────────────────────────────────────────────────────────────────


async def cancel_if_pending(session: AsyncSession, job_id: UUID) -> bool:
    """Atomically cancel a still-queued job. Returns True iff it transitioned
    pending->cancelled (so the worker can never have claimed it). False means
    it was already claimed/running/done — caller falls through to request_cancel."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "pending")
        .values(status="cancelled", completed_at=func.now())
    )
    return result.rowcount > 0


async def request_cancel(session: AsyncSession, job_id: UUID) -> bool:
    """Signal cancel for a RUNNING job: running->cancelling. Returns True iff it
    transitioned (the owning worker / same-process registry then cancels the
    task and finalizes). False means it wasn't running (done/failed/etc)."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(status="cancelling")
    )
    return result.rowcount > 0


async def get_status(session: AsyncSession, job_id: UUID) -> Optional[str]:
    """Lightweight status read (used by the heartbeat to notice a cancel)."""
    return (
        await session.execute(
            select(HomeworkJob.status).where(HomeworkJob.id == job_id)
        )
    ).scalar_one_or_none()


async def mark_cancelled(session: AsyncSession, job_id: UUID) -> None:
    """Finalize a user-cancelled job: job -> cancelled; any non-done phase rows
    -> failed (they were interrupted/killed). DONE phases are preserved so a
    later /retry can resume (worklog 0031)."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(status="cancelled", completed_at=func.now())
    )
    await session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == job_id)
        .where(PhaseOutput.status != "done")
        .values(status="failed")
    )


async def reclaim_stale_cancelling(
    session: AsyncSession, stale_after_seconds: int
) -> int:
    """Finalize jobs stuck in `cancelling` whose claim is older than the lease
    window — i.e. the owning worker crashed mid-cancel. They're excluded from
    both claim (pending) and reclaim (running) sweeps, so without this they'd
    hang forever. The intent was to cancel, so -> cancelled."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.status == "cancelling")
        .where(HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        .values(status="cancelled", completed_at=func.now())
    )
    return result.rowcount or 0
