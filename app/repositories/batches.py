from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.toc_entry import TOCEntry


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
    solver_transport: str = "inherit",
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    solver_provider: Optional[str] = None,
    solver_model: Optional[str] = None,
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
        solver_transport=solver_transport,
        extract_provider=extract_provider,
        extract_model=extract_model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        solver_provider=solver_provider,
        solver_model=solver_model,
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
    """Tally over the batch's launched lessons only (DISTINCT ON latest job per
    toc_entry); the denominator is the launch scope derived from member jobs —
    rest-of-book is ``toc_total_for_batch``."""
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


async def toc_total_for_batch(session: AsyncSession, batch_id: UUID) -> int:
    """Whole-book TOC row count for this batch's book — display-only context
    (the rollup denominator is the launched-lesson count, never this)."""
    from app.models.toc_entry import TOCEntry
    return (await session.execute(
        select(func.count()).select_from(TOCEntry)
        .join(Batch, Batch.book_id == TOCEntry.book_id)
        .where(Batch.id == batch_id)
    )).scalar_one()


async def archive_rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Among the batch's `done` lessons (latest job per toc_entry), split by
    Notion archive state: {"archived": n, "unarchived": m, "stale": k}. `stale`
    is the subset of `archived` whose Notion page still holds an OLDER job's
    output (toc_entries.notion_archived_job_id != this latest job's id) — e.g.
    after a regen that hasn't been re-archived yet. Mirrors rollup_for_batch's
    DISTINCT-ON latest-per-lesson so retries don't double-count."""
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
            select(
                latest.c.job_id,
                latest.c.notion_archived_at,
                TOCEntry.notion_archived_job_id,
            )
            .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
            .where(latest.c.status == "done")
        )
    ).all()
    archived = sum(1 for r in rows if r.notion_archived_at is not None)
    unarchived = sum(1 for r in rows if r.notion_archived_at is None)
    stale = sum(
        1 for r in rows
        if r.notion_archived_at is not None
        and r.notion_archived_job_id is not None
        and r.notion_archived_job_id != r.job_id
    )
    return {"archived": archived, "unarchived": unarchived, "stale": stale}


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


async def done_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry in the batch that is `done` — including
    already-archived jobs. The worklist for a FORCE re-archive sweep (refresh
    stale Notion content after a regen). Stable order."""
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.status.label("status"),
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
            .order_by(latest.c.toc_entry_id)
        )
    ).all()
    return [r.job_id for r in rows]


async def done_stale_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry that is `done` and archived, but whose page holds
    an OLDER job's output (toc_entries.notion_archived_job_id != this job). The
    targeted worklist for the operator 'refresh stale' sweep — a subset of
    done_job_ids, so a force-refresh rewrites only the husks, not all pages."""
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
            .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
            .where(latest.c.status == "done")
            .where(latest.c.notion_archived_at.is_not(None))
            .where(TOCEntry.notion_archived_job_id.is_not(None))
            .where(TOCEntry.notion_archived_job_id != latest.c.job_id)
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
        toc_total = await toc_total_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally, "archive": archive,
                    "toc_total": toc_total,
                    "original_filename": original_filename})
    return out


async def list_jobs(session: AsyncSession, batch_id: UUID) -> list[dict]:
    """One row per TOC entry in the batch's BOOK (full TOC), LEFT-joined to
    the latest job per toc_entry within this batch. Launched lessons carry
    their job's status/fields; un-launched entries come back with
    job_id/status/attempts/current_phase/error_message all None. Every row
    also carries `toc_class` — the pure classifier's tag
    (app.services.toc_classifier.classify_entries), run once over the
    fetched rows — so the FE can render un-launched/excluded rows with their
    class chip instead of a bare "not started". Ordered by order_index.
    rollup_for_batch is launched-only (its denominator is the launch scope,
    not this whole-book row count); toc_total_for_batch is the whole-book
    display context."""
    from app.models.toc_entry import TOCEntry
    from app.services.toc_classifier import classify_entries

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
            TOCEntry.page_start, TOCEntry.page_end,
        )
        .select_from(TOCEntry)
        .outerjoin(latest, latest.c.toc_entry_id == TOCEntry.id)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    rows = (await session.execute(stmt)).all()
    # classify_entries duck-types .section_title/.page_start/.page_end off
    # each Row (present via the select above) and returns classes aligned to
    # input order — rows are never reordered, so a straight zip lines up.
    toc_classes = classify_entries(rows)
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
            "toc_class": toc_class,
        }
        for r, toc_class in zip(rows, toc_classes)
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
