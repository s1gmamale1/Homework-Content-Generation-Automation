from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TOCEntry
from app.schemas import TOCEntryExtracted


async def bulk_create(
    session: AsyncSession, book_id: UUID, entries: list[TOCEntryExtracted]
) -> list[TOCEntry]:
    rows: list[TOCEntry] = []
    for idx, e in enumerate(entries):
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


async def list_for_book(session: AsyncSession, book_id: UUID) -> list[TOCEntry]:
    stmt = (
        select(TOCEntry)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get(session: AsyncSession, toc_entry_id: UUID) -> TOCEntry | None:
    return await session.get(TOCEntry, toc_entry_id)


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
