from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch
from app.models.book import Book
from app.models.homework_job import HomeworkJob


async def get_or_create_for_book(
    session: AsyncSession,
    *,
    book_id: UUID,
    subject: str,
    grade: Optional[str],
    provider: str,
    model: Optional[str],
    transport: str,
    extract_transport: str = "inherit",
    judge_transport: str = "inherit",
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    notion_source: Optional[str] = None,
) -> Batch:
    """Race-safe find-or-create THE batch for a (book, transport) pair
    (UNIQUE(book_id, transport) + ON CONFLICT). Core insert bypasses the ORM
    Python defaults, so id/created_at/updated_at are supplied explicitly. On
    conflict the existing row is kept (only updated_at is touched) and its id is
    returned — a different-transport re-launch forks a new batch."""
    stmt = (
        pg_insert(Batch)
        .values(
            id=uuid4(),
            book_id=book_id,
            subject=subject,
            grade=grade,
            provider=provider,
            model=model,
            transport=transport,
            extract_transport=extract_transport,
            judge_transport=judge_transport,
            extract_provider=extract_provider,
            extract_model=extract_model,
            judge_provider=judge_provider,
            judge_model=judge_model,
            notion_source=notion_source,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["book_id", "transport"],
            set_={"updated_at": func.now()},
        )
        .returning(Batch.id)
    )
    batch_id = (await session.execute(stmt)).scalar_one()
    return await session.get(Batch, batch_id)


async def rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Per-lesson-latest status tally for a batch: one row per toc_entry (its
    newest job), then GROUP BY status. Mirrors `jobs.latest_by_section` (DISTINCT
    ON) but scoped to batch_id, so retries/top-ups can't inflate the count. The
    denominator is sum(tally.values())."""
    latest = (
        select(HomeworkJob.status)
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = await session.execute(
        select(latest.c.status, func.count()).group_by(latest.c.status)
    )
    return {status: count for status, count in rows.all()}


async def list_with_rollups(session: AsyncSession) -> list[dict]:
    """Every batch (newest first) + its computed rollup + the book filename
    (for subject-variant labeling)."""
    rows = (
        await session.execute(
            select(Batch, Book.original_filename)
            .join(Book, Book.id == Batch.book_id)
            .order_by(Batch.created_at.desc())
        )
    ).all()
    out = []
    for b, original_filename in rows:
        tally = await rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally, "original_filename": original_filename})
    return out


async def list_jobs(session: AsyncSession, batch_id: UUID) -> list[dict]:
    """Per-lesson-latest rows for a batch: one row per toc_entry (its newest job),
    joined to the lesson title, ordered by order_index. Mirrors rollup_for_batch's
    DISTINCT ON but returns rows; row count == the rollup denominator."""
    from app.models.toc_entry import TOCEntry
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.attempts.label("attempts"),
            HomeworkJob.current_phase.label("current_phase"),
            HomeworkJob.error_message.label("error_message"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    stmt = (
        select(
            latest.c.job_id, latest.c.toc_entry_id, latest.c.status,
            latest.c.attempts, latest.c.current_phase, latest.c.error_message,
            TOCEntry.section_title, TOCEntry.order_index,
        )
        .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
        .order_by(TOCEntry.order_index)
    )
    rows = await session.execute(stmt)
    return [
        {
            "job_id": str(r.job_id),
            "toc_entry_id": str(r.toc_entry_id),
            "section_title": r.section_title,
            "order_index": r.order_index,
            "status": r.status,
            "attempts": r.attempts,
            "current_phase": r.current_phase,
            "error_message": r.error_message,
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────
# Batch-pause primitive (reused by C5 fleet-ctrl-3 kill-switch / manual pause)
# ─────────────────────────────────────────────────────────────────────────


async def pause_batch(
    session: AsyncSession, batch_id: UUID, reason: str
) -> None:
    """Gate a batch: set paused_at=now() + paused_reason. Does NOT alter any
    job row — pause affects only claim eligibility, never cancels in-flight work
    ('never hard-cancel paid work' contract)."""
    await session.execute(
        update(Batch)
        .where(Batch.id == batch_id)
        .values(paused_at=func.now(), paused_reason=reason)
    )


async def unpause_batch(session: AsyncSession, batch_id: UUID) -> None:
    """Lift the gate: clear paused_at + paused_reason for one batch."""
    await session.execute(
        update(Batch)
        .where(Batch.id == batch_id)
        .values(paused_at=None, paused_reason=None)
    )


async def unpause_by_reason(session: AsyncSession, reason: str) -> int:
    """Lift the gate for ALL batches paused with this reason.
    Returns the number of rows unpaused. Used by C5's kill-switch reset."""
    result = await session.execute(
        update(Batch)
        .where(Batch.paused_at.is_not(None))
        .where(Batch.paused_reason == reason)
        .values(paused_at=None, paused_reason=None)
    )
    return result.rowcount or 0


async def active_batch_ids(session: AsyncSession) -> list[UUID]:
    """Return ids of all batches that are NOT currently paused."""
    rows = await session.execute(
        select(Batch.id).where(Batch.paused_at.is_(None))
    )
    return list(rows.scalars().all())


async def paused_batch_ids_by_reason(session: AsyncSession, reason: str) -> list[UUID]:
    """Return ids of all batches paused with the given reason.

    Used by the budget monitor to reconcile its OWN pauses without touching
    batches paused by a different reason (e.g. C5 manual / fleet gate).
    """
    rows = await session.execute(
        select(Batch.id)
        .where(Batch.paused_at.is_not(None))
        .where(Batch.paused_reason == reason)
    )
    return list(rows.scalars().all())
