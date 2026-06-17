"""Real-DB: jobs_repo.create persists the per-role provider/model columns, and
leaves them NULL when not given (role-default fallback). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lesson(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256="5" * 64, file_size_bytes=1, status="toc_ready")
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


@pytest.mark.asyncio
async def test_create_persists_role_provider_model():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s)
        book_id = book.id
        try:
            job = await jobs_repo.create(
                s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
                extract_provider="gemini", extract_model="gemini-2.5-flash",
                judge_provider=None, judge_model=None,
            )
            await s.flush()
            assert job.extract_provider == "gemini"
            assert job.extract_model == "gemini-2.5-flash"
            assert job.judge_provider is None
            assert job.judge_model is None
        finally:
            await s.rollback()
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
