"""Set-based reads for the coverage dashboard. Three queries total, none N+1.

Modeled on `jobs.count_by_book_ids` (one grouped COUNT) rather than
`batches.list_with_rollups` (3 queries per batch), which must not be used here.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, Book, HomeworkJob, TOCEntry


async def all_books(session: AsyncSession) -> list[Book]:
    """Every book, newest first. No limit — the dashboard is a whole-fleet view
    (unlike `GET /books`, which paginates at 100)."""
    stmt = select(Book).order_by(Book.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def toc_rows_by_book(
    session: AsyncSession, book_ids: list[UUID]
) -> dict[str, list[TOCEntry]]:
    """All TOC rows for the given books in one query, grouped in Python and kept
    in `order_index` order (the classifier's page-containment rule reads
    neighbouring rows, so order matters)."""
    if not book_ids:
        return {}
    stmt = (
        select(TOCEntry)
        .where(TOCEntry.book_id.in_(book_ids))
        .order_by(TOCEntry.book_id, TOCEntry.order_index)
    )
    out: dict[str, list[TOCEntry]] = {}
    for row in (await session.execute(stmt)).scalars().all():
        out.setdefault(str(row.book_id), []).append(row)
    return out


async def job_status_by_book(
    session: AsyncSession, output_language: str
) -> dict[str, dict[str, str]]:
    """`{book_id: {toc_entry_id: latest_status}}` for this output language.

    Returns per-TOC-entry statuses rather than a pre-summed per-book tally: the
    builder must scope the tally to LESSON-class rows, and it can only do that
    if it can see which TOC entry each job belongs to (gate-1 finding — legacy
    unfiltered launches left `done` jobs on test/revision rows).

    "Latest job per (book, toc_entry)" is the same scope `rollup_for_batch`
    uses, so a retried lesson counts once. Still ONE query.

    `kind='homework'` scopes the lookup so a `teacher_material` job never
    replaces a lesson's homework status here (Task 9) — this dashboard is a
    homework-only view; a teacher-deck job for the same (book, toc_entry)
    created later must not shadow the homework job's status.
    """
    latest = (
        select(
            HomeworkJob.book_id.label("book_id"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
            HomeworkJob.status.label("status"),
        )
        .where(
            HomeworkJob.output_language == output_language,
            HomeworkJob.kind == "homework",
        )
        .order_by(
            HomeworkJob.book_id,
            HomeworkJob.toc_entry_id,
            HomeworkJob.created_at.desc(),
        )
        .distinct(HomeworkJob.book_id, HomeworkJob.toc_entry_id)
        .subquery()
    )
    stmt = select(latest.c.book_id, latest.c.toc_entry_id, latest.c.status)
    out: dict[str, dict[str, str]] = {}
    for book_id, toc_entry_id, status in (await session.execute(stmt)).all():
        out.setdefault(str(book_id), {})[str(toc_entry_id)] = status
    return out


async def batch_by_book(
    session: AsyncSession, output_language: str
) -> dict[str, tuple[str, bool]]:
    """Newest batch per book for this language → (batch_id, is_paused), for the
    drill-in link. Transport-agnostic: a viewer asking "is homework generated?"
    does not care which transport produced it.

    `kind='homework'` scopes the lookup (Task 9) — a `teacher_material` batch
    forks its own row (`uq_batches_book_id_transport_output_language_kind`) and
    must never be picked as "the" batch for a homework book's drill-in link."""
    stmt = (
        select(Batch.book_id, Batch.id, Batch.paused_at)
        .where(Batch.output_language == output_language, Batch.kind == "homework")
        .order_by(Batch.book_id, Batch.created_at.desc())
        .distinct(Batch.book_id)
    )
    return {
        str(book_id): (str(batch_id), paused_at is not None)
        for book_id, batch_id, paused_at in (await session.execute(stmt)).all()
    }
