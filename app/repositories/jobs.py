from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import HomeworkJob


async def create(
    session: AsyncSession,
    *,
    book_id: UUID,
    toc_entry_id: UUID,
    subject: str,
    status: str = "pending",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> HomeworkJob:
    kwargs: dict[str, Any] = dict(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        status=status,
    )
    if provider is not None:
        kwargs["provider"] = provider
    if model is not None:
        kwargs["model"] = model
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
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID
) -> Optional[HomeworkJob]:
    """Return the most recent pending/running/done job for the (book, section).

    `done` is included so idempotent regenerate returns the existing successful
    result. Callers that want to force a new run must pass `force=True` and skip
    this lookup entirely.
    """
    stmt = (
        select(HomeworkJob)
        .where(
            HomeworkJob.book_id == book_id,
            HomeworkJob.toc_entry_id == toc_entry_id,
            HomeworkJob.status.in_(["pending", "running", "done"]),
        )
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
    assembled_md: Optional[str] = None,
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
    if assembled_md is not None:
        job.assembled_md = assembled_md


async def set_games_json(
    session: AsyncSession, job_id: UUID, games_json: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.games_json = games_json


async def set_flashcards_json(
    session: AsyncSession, job_id: UUID, flashcards_json: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.flashcards_json = flashcards_json


async def set_final_challenge_json(
    session: AsyncSession, job_id: UUID, payload: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.final_challenge_json = payload


async def set_memory_sprint_json(
    session: AsyncSession, job_id: UUID, payload: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.memory_sprint_json = payload


async def set_reading_json(
    session: AsyncSession, job_id: UUID, payload: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.reading_json = payload


async def set_difficulty(session: AsyncSession, job_id: UUID, difficulty: str) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.difficulty = difficulty


async def set_source_map_json(
    session: AsyncSession, job_id: UUID, payload: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.source_map_json = payload


async def set_flow_manifest_json(
    session: AsyncSession, job_id: UUID, payload: dict[str, Any]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.flow_manifest_json = payload


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
    session: AsyncSession, *, worker_id: str, max_attempts: int
) -> Optional[HomeworkJob]:
    """Atomically claim the next pending job for this worker.

    Uses `FOR UPDATE SKIP LOCKED` so multiple workers polling concurrently
    never collide on the same row — Postgres serializes the dispatch.
    Returns None if no claimable job is available (worker should sleep
    and retry).

    Eligibility rules:
      - status == 'pending'
      - scheduled_at <= NOW() (so delayed retries don't fire early)
      - attempts < max_attempts (don't reclaim poison-pill jobs forever)

    Order: highest priority first, then oldest scheduled_at first (FIFO
    within a priority band).
    """
    now = datetime.now(timezone.utc)
    pick_stmt = (
        select(HomeworkJob.id)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= now)
        .where(HomeworkJob.attempts < max_attempts)
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
            claimed_at=now,
            claimed_by=worker_id,
            attempts=HomeworkJob.attempts + 1,
            last_attempt_at=now,
            started_at=now,
            error_message=None,  # clear stale message from prior attempt
        )
    )
    return await session.get(HomeworkJob, job_id)


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
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "running")
        .where(
            (HomeworkJob.claimed_at.is_(None)) | (HomeworkJob.claimed_at < cutoff)
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
                completed_at=datetime.now(timezone.utc),
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
            scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
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
        .where(HomeworkJob.scheduled_at <= datetime.now(timezone.utc))
    )
    return int((await session.execute(stmt)).scalar_one())
