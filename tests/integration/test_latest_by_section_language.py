"""Real-DB: jobs_repo.latest_by_section is language-scoped when output_language
is given, so the Fleet/Section launcher status reflects the SELECTED language —
a book complete in uz is NOT 'complete' under ru. Skipped unless RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_latest_by_section_scopes_by_output_language():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="b.pdf",
                    content_sha256="3" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()

        # same section generated in uz (done) and ru (failed)
        uz = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id,
                                    subject="math-algebra", output_language="uz")
        uz.status = "done"
        ru = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id,
                                    subject="math-algebra", output_language="ru")
        ru.status = "failed"
        await s.flush()

        uz_map = await jobs_repo.latest_by_section(s, book.id, output_language="uz")
        ru_map = await jobs_repo.latest_by_section(s, book.id, output_language="ru")
        en_map = await jobs_repo.latest_by_section(s, book.id, output_language="en")
        all_map = await jobs_repo.latest_by_section(s, book.id)  # None → all-language

        assert uz_map[toc.id].id == uz.id and uz_map[toc.id].status == "done"
        assert ru_map[toc.id].id == ru.id and ru_map[toc.id].status == "failed"
        # en has no jobs → the section is absent → Fleet shows it as not-done → launchable
        assert toc.id not in en_map
        # None preserves the all-language aggregate (most recent of the two)
        assert toc.id in all_map

        await s.rollback()
