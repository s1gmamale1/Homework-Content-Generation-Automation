"""Real-DB: toc_entries.get_next_in_book returns the next TEACHING lesson
(smallest order_index strictly greater than the given one, skipping
NULL-section end-matter rows). RUN_DB_INTEGRATION=1."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_get_next_in_book_returns_successor_and_none_at_end():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.create(
            session,
            subject="matematika",
            grade="8",
            original_filename="t.pdf",
            content_sha256="1" * 64,
            file_size_bytes=13,
        )
        entries = await toc_repo.bulk_create(
            session,
            book.id,
            [
                TOCEntryExtracted(section_number="17", section_title="Pifagor teoremasi",
                                  page_start=41, page_end=43),
                TOCEntryExtracted(section_number="18", section_title="Pifagor teoremasiga teskari",
                                  page_start=44, page_end=46),
            ],
        )
        await session.commit()

        first, last = entries[0], entries[1]
        nxt = await toc_repo.get_next_in_book(session, book.id, first.order_index)
        assert nxt is not None and nxt.section_title == "Pifagor teoremasiga teskari"
        assert await toc_repo.get_next_in_book(session, book.id, last.order_index) is None


@pytest.mark.asyncio
async def test_get_next_in_book_skips_null_section_end_matter():
    """Gate R2: a NULL-section end-matter row (Ответы/Тестовые) between two
    teaching lessons must be SKIPPED — the successor is the next NUMBERED lesson.
    If the only rows after `first` are NULL-section, return None."""
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.create(
            session,
            subject="matematika",
            grade="8",
            original_filename="t2.pdf",
            content_sha256="2" * 64,
            file_size_bytes=13,
        )
        entries = await toc_repo.bulk_create(
            session, book.id,
            [
                TOCEntryExtracted(section_number="17", section_title="Lesson 17",
                                  page_start=41, page_end=43),
                TOCEntryExtracted(section_number=None, section_title="Тестовые задания",
                                  page_start=44, page_end=45),
                TOCEntryExtracted(section_number="18", section_title="Lesson 18",
                                  page_start=46, page_end=48),
                TOCEntryExtracted(section_number=None, section_title="Ответы",
                                  page_start=49, page_end=50),
            ],
        )
        await session.commit()
        first, mid_null, last_lesson, end_null = entries
        # skips the NULL end-matter row, lands on the next numbered lesson
        nxt = await toc_repo.get_next_in_book(session, book.id, first.order_index)
        assert nxt is not None and nxt.section_title == "Lesson 18"
        # only NULL-section rows remain after the last lesson → None
        assert await toc_repo.get_next_in_book(session, book.id, last_lesson.order_index) is None
