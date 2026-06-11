from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch
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
    """Every batch (newest first) + its computed rollup."""
    batches = (
        await session.execute(select(Batch).order_by(Batch.created_at.desc()))
    ).scalars().all()
    out = []
    for b in batches:
        tally = await rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally})
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
