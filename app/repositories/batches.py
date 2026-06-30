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
    output_language: str,
    extract_transport: str = "inherit",
    judge_transport: str = "inherit",
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    notion_source: Optional[str] = None,
    custom_prompts: Optional[dict] = None,
    selected_phases: Optional[list] = None,
    session_limit_strategy: str = "inherit",
) -> Batch:
    """Race-safe find-or-create THE batch for a (book, transport, output_language)
    triple (UNIQUE(book_id, transport, output_language) + ON CONFLICT). Core insert
    bypasses the ORM Python defaults, so id/created_at/updated_at are supplied
    explicitly. On conflict the existing row is kept (only updated_at is touched)
    and its id is returned — a different-transport or different-language re-launch
    forks a new batch."""
    insert = pg_insert(Batch).values(
        id=uuid4(),
        book_id=book_id,
        subject=subject,
        grade=grade,
        provider=provider,
        model=model,
        transport=transport,
        output_language=output_language,
        extract_transport=extract_transport,
        judge_transport=judge_transport,
        extract_provider=extract_provider,
        extract_model=extract_model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        notion_source=notion_source,
        custom_prompts=custom_prompts,
        selected_phases=selected_phases,
        session_limit_strategy=session_limit_strategy,
        created_at=func.now(),
        updated_at=func.now(),
    )
    # On conflict, only OVERWRITE custom_prompts/selected_phases when this launch
    # actually carries them; a plain re-launch/top-up (None) must leave an earlier
    # custom launch's provenance intact. NOTE: a COALESCE(excluded.x, batches.x)
    # does NOT work here — SQLAlchemy serializes Python None into a JSONB column as
    # JSON 'null' (not SQL NULL), so COALESCE keeps the JSON-null and still wipes
    # the stored value. Conditionally omitting the column from set_ is the fix.
    on_conflict_set: dict = {"updated_at": func.now()}
    if custom_prompts is not None:
        on_conflict_set["custom_prompts"] = insert.excluded.custom_prompts
    if selected_phases is not None:
        on_conflict_set["selected_phases"] = insert.excluded.selected_phases
    stmt = (
        insert.on_conflict_do_update(
            index_elements=["book_id", "transport", "output_language"],
            set_=on_conflict_set,
        )
        .returning(Batch.id)
    )
    batch_id = (await session.execute(stmt)).scalar_one()
    return await session.get(Batch, batch_id)


async def rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Per-lesson-latest status tally for a batch over the WHOLE book: one row
    per launched toc_entry (its newest job) GROUP BY status — DISTINCT ON, so
    retries/top-ups can't inflate the count — PLUS a synthetic ``not_started``
    count for the book's lessons that have no job in this batch yet. The
    denominator (sum of values) is therefore the book's full lesson count, so a
    partial launch reads as e.g. 5/47, not 5/5."""
    from app.models.toc_entry import TOCEntry

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
    tally = {status: count for status, count in rows.all()}

    book_id = (
        await session.execute(select(Batch.book_id).where(Batch.id == batch_id))
    ).scalar_one_or_none()
    if book_id is not None:
        total = (
            await session.execute(
                select(func.count())
                .select_from(TOCEntry)
                .where(TOCEntry.book_id == book_id)
            )
        ).scalar_one()
        not_started = total - sum(tally.values())
        if not_started > 0:
            tally["not_started"] = not_started
    return tally


async def archive_rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Among the batch's `done` lessons (latest job per toc_entry), split by
    Notion archive state: {"archived": n, "unarchived": m}. Mirrors
    rollup_for_batch's DISTINCT-ON latest-per-lesson so retries don't double-count."""
    latest = (
        select(
            HomeworkJob.status.label("status"),
            HomeworkJob.notion_archived_at.label("notion_archived_at"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.notion_archived_at).where(latest.c.status == "done")
        )
    ).all()
    archived = sum(1 for (ts,) in rows if ts is not None)
    unarchived = sum(1 for (ts,) in rows if ts is None)
    return {"archived": archived, "unarchived": unarchived}


async def done_unarchived_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry in the batch that is `done` AND not yet archived —
    the worklist the head-side re-archive sweep iterates. Stable order."""
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.notion_archived_at.label("notion_archived_at"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.job_id)
            .where(latest.c.status == "done")
            .where(latest.c.notion_archived_at.is_(None))
            .order_by(latest.c.toc_entry_id)
        )
    ).all()
    return [r.job_id for r in rows]


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
        archive = await archive_rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally, "archive": archive,
                    "original_filename": original_filename})
    return out


async def list_jobs(session: AsyncSession, batch_id: UUID) -> list[dict]:
    """One row per lesson in the batch's BOOK (full TOC), LEFT-joined to the
    latest job per toc_entry within this batch. Launched lessons carry their
    job's status/fields; un-launched lessons come back with job_id/status None.
    Ordered by order_index. Companion to rollup_for_batch's whole-book tally:
    this returns the rows, that returns the per-status counts (incl. not_started)."""
    from app.models.toc_entry import TOCEntry

    book_id = (
        await session.execute(select(Batch.book_id).where(Batch.id == batch_id))
    ).scalar_one_or_none()
    if book_id is None:
        return []

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
            latest.c.job_id, latest.c.status, latest.c.attempts,
            latest.c.current_phase, latest.c.error_message,
            TOCEntry.id.label("toc_entry_id"),
            TOCEntry.section_title, TOCEntry.order_index,
        )
        .select_from(TOCEntry)
        .outerjoin(latest, latest.c.toc_entry_id == TOCEntry.id)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    rows = await session.execute(stmt)
    return [
        {
            "job_id": str(r.job_id) if r.job_id is not None else None,
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
