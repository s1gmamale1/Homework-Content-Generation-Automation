from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TOCEntry
from app.schemas import TOCEntryExtracted


async def bulk_create(
    session: AsyncSession, book_id: UUID, entries: list[TOCEntryExtracted]
) -> list[TOCEntry]:
    # The extractor LLM emits entries in whatever order it read the mundarija
    # (two-column contents pages come back interleaved), so order_index must be
    # derived from page_start, not emission order — order_index drives
    # get_next_in_book (curriculum-boundary note) and the FE listing. Stable
    # sort: same-page ties keep emission order; page-less entries go last.
    ordered = sorted(
        enumerate(entries),
        key=lambda ie: (ie[1].page_start is None, ie[1].page_start or 0, ie[0]),
    )
    rows: list[TOCEntry] = []
    for idx, (_, e) in enumerate(ordered):
        row = TOCEntry(
            book_id=book_id,
            chapter_number=e.chapter_number,
            chapter_title=e.chapter_title,
            section_number=e.section_number,
            section_title=e.section_title,
            page_start=e.page_start,
            page_end=e.page_end,
            order_index=idx,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def delete_for_book(session: AsyncSession, book_id: UUID) -> int:
    """Delete every TOC entry for a book. Used by the extractor's
    clear-before-insert so a re-extract replaces rather than appends (the table
    has no unique constraint and bulk_create is a naive append). Returns the
    number of rows removed.

    NOTE: uses ``sa_delete`` (aliased) because this module also defines a public
    ``delete(session, toc_entry_id)`` single-entry function that would otherwise
    shadow SQLAlchemy's ``delete`` at call time."""
    result = await session.execute(sa_delete(TOCEntry).where(TOCEntry.book_id == book_id))
    return result.rowcount or 0


async def list_for_book(session: AsyncSession, book_id: UUID) -> list[TOCEntry]:
    stmt = (
        select(TOCEntry)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_by_book_ids(session: AsyncSession, book_ids: list[UUID]) -> dict[UUID, int]:
    """Grouped `COUNT(*)` of toc_entries per book, ONE query for the whole
    list (GK2 batch-load expectation — backs the Notion availability
    enrichment route's `toc_total`, alongside `books_repo.get_many` and
    `jobs_repo.count_by_book_ids`). A book with zero entries is simply absent
    from the returned mapping — callers default-0 on lookup. Empty input
    short-circuits without touching the session."""
    if not book_ids:
        return {}
    stmt = (
        select(TOCEntry.book_id, func.count())
        .where(TOCEntry.book_id.in_(book_ids))
        .group_by(TOCEntry.book_id)
    )
    rows = (await session.execute(stmt)).all()
    return {book_id: count for book_id, count in rows}


async def get_next_in_book(
    session: AsyncSession, book_id: UUID, order_index: int
) -> TOCEntry | None:
    """Return the next TEACHING lesson in reading order — the smallest order_index
    strictly greater than `order_index` within the same book whose section_number
    is not NULL — or None when there is no later numbered lesson. Uses `> order_index`
    (not `+1`) so a non-contiguous index sequence still resolves the true successor,
    and skips NULL-section end-matter rows (Упражнения/Ответы/Тестовые — 214 such
    rows in production) so the boundary note never announces "next lesson = «Ответы»".
    Backed by ix_toc_entries_book_id_order."""
    stmt = (
        select(TOCEntry)
        .where(
            TOCEntry.book_id == book_id,
            TOCEntry.order_index > order_index,
            TOCEntry.section_number.isnot(None),
        )
        .order_by(TOCEntry.order_index)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get(session: AsyncSession, toc_entry_id: UUID) -> TOCEntry | None:
    return await session.get(TOCEntry, toc_entry_id)


async def titles_for_subject_grade(
    session: AsyncSession, *, subject: str, grade: str
) -> list[tuple[str | None, str, str]]:
    """Every TOC entry's (section_number, section_title, chapter_title) across
    ALL books of one subject+grade.

    Scope matches the Notion container: `Generated Homeworks` lives under a
    subject page keyed `{lang}:{subject}|{grade}`, so every book at that
    subject+grade shares one namespace of lesson-page titles — including the
    Part I / Part II split of a single textbook, whose repeated rubric headings
    collide across the two books. Language is deliberately NOT a filter: it
    selects the container, not which TOC rows exist.
    """
    from app.models import Book  # local import — avoids a circular import

    rows = await session.execute(
        select(TOCEntry.section_number, TOCEntry.section_title, TOCEntry.chapter_title)
        .join(Book, Book.id == TOCEntry.book_id)
        .where(Book.subject == subject, Book.grade == grade)
    )
    return [(r[0], r[1] or "", r[2] or "") for r in rows.all()]


async def set_notion_homework_page_id(
    session: AsyncSession, toc_entry_id: UUID, page_id: str
) -> None:
    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return
    entry.notion_homework_page_id = page_id


async def set_notion_archived_job(
    session: AsyncSession, toc_entry_id: UUID, job_id: UUID
) -> None:
    """Stamp which homework_job's content is currently on the lesson's Notion
    page. Set only when archive_job actually writes (first archive or replace)."""
    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return
    entry.notion_archived_job_id = job_id


async def update(
    session: AsyncSession,
    toc_entry_id: UUID,
    *,
    chapter_number: str | None = None,
    chapter_title: str | None = None,
    section_number: str | None = None,
    section_title: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> TOCEntry | None:
    """Patch user-editable fields on a TOC entry. Pass only the fields you
    want to change; others are left untouched."""
    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return None
    if chapter_number is not None:
        entry.chapter_number = chapter_number
    if chapter_title is not None:
        entry.chapter_title = chapter_title
    if section_number is not None:
        entry.section_number = section_number
    if section_title is not None:
        entry.section_title = section_title
    if page_start is not None:
        entry.page_start = page_start
    if page_end is not None:
        entry.page_end = page_end
    return entry


async def delete(session: AsyncSession, toc_entry_id: UUID) -> bool:
    """Remove a TOC entry. Homework jobs that referenced it are deleted
    explicitly first since `homework_jobs.toc_entry_id` has no cascade."""
    from app.models import HomeworkJob

    job_rows = (
        await session.execute(
            select(HomeworkJob).where(HomeworkJob.toc_entry_id == toc_entry_id)
        )
    ).scalars().all()
    for job in job_rows:
        await session.delete(job)

    entry = await session.get(TOCEntry, toc_entry_id)
    if entry is None:
        return False
    await session.delete(entry)
    return True
