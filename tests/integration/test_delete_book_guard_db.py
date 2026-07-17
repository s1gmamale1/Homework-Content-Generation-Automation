"""Real-DB proof for the DELETE /books/{id} guards (BE-02 task 2): a 409 from
either the ingest-status guard or the active-jobs guard must leave every
book-scoped row untouched — the guard runs BEFORE books_repo.delete, so
nothing is half-deleted. Exercises the actual route function against a real
session (not mocked), which is the only way to prove the transaction really
never started.

Run (scratch DB, pinned to 127.0.0.1):
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_bookdel \\
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_delete_book_guard_db.py -q
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book(s, *, status: str):
    from app.models.book import Book

    book = Book(
        subject="math-algebra",
        original_filename=f"alg-{uuid4().hex[:8]}.pdf",
        content_sha256=uuid4().hex + uuid4().hex,
        file_size_bytes=1,
        status=status,
    )
    s.add(book)
    await s.flush()
    return book.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import delete

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_409_on_running_job_leaves_book_and_job_rows_untouched():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book_id = await _seed_book(s, status="toc_ready")
        toc = TOCEntry(book_id=book_id, section_title="L0", order_index=0)
        s.add(toc)
        await s.flush()
        job = await jobs_repo.create(
            s, book_id=book_id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", status="running")
        await s.commit()
        job_id = job.id

    try:
        async with SessionLocal() as s:
            with pytest.raises(HTTPException) as exc_info:
                await delete_book(book_id, s)
        assert exc_info.value.status_code == 409
        assert "active job(s)" in exc_info.value.detail

        # DB rows must be untouched — no half-delete.
        async with SessionLocal() as s:
            assert await s.get(Book, book_id) is not None
            assert await s.get(HomeworkJob, job_id) is not None
            assert await s.get(TOCEntry, toc.id) is not None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_409_on_toc_extracting_book_leaves_row_untouched():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book_id = await _seed_book(s, status="toc_extracting")
        await s.commit()

    try:
        async with SessionLocal() as s:
            with pytest.raises(HTTPException) as exc_info:
                await delete_book(book_id, s)
        assert exc_info.value.status_code == 409
        assert "still being ingested" in exc_info.value.detail

        async with SessionLocal() as s:
            assert await s.get(Book, book_id) is not None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_204_happy_path_actually_deletes_when_no_active_jobs():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book_id = await _seed_book(s, status="toc_ready")
        toc = TOCEntry(book_id=book_id, section_title="L0", order_index=0)
        s.add(toc)
        await s.flush()
        job = await jobs_repo.create(
            s, book_id=book_id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", status="done")
        await s.commit()
        job_id = job.id

    async with SessionLocal() as s:
        result = await delete_book(book_id, s)
    assert result is None  # 204 No Content, function returns None on success

    async with SessionLocal() as s:
        assert await s.get(Book, book_id) is None
        assert await s.get(HomeworkJob, job_id) is None
