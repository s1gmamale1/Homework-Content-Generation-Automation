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
