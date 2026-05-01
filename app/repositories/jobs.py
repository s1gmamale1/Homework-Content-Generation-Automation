from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
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
) -> HomeworkJob:
    job = HomeworkJob(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        status=status,
    )
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
