"""Real-DB proof for the TOC re-extract guard.

Documents WHY the guard exists (the raw clear-before-insert raises a FK
violation when a job references a to-be-deleted TOC entry) AND that
jobs_repo.list_for_book surfaces the exact blocking jobs.

Run (scratch DB, pinned to 127.0.0.1):
  createdb -U macmini5 edu_tocfk_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_tocfk_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_tocfk_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_toc_reextract_guard.py -q
  dropdb -U macmini5 edu_tocfk_test
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed(s, *, with_job: bool):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob

    book = Book(
        subject="math-algebra",
        original_filename=f"alg-{uuid4().hex[:8]}.pdf",
        content_sha256=uuid4().hex + uuid4().hex,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="Lesson 1", order_index=0)
    s.add(toc)
    await s.flush()
    if with_job:
        s.add(HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", status="done",
        ))
        await s.flush()
    await s.commit()
    return book.id, toc.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_list_for_book_finds_referencing_job_and_delete_would_fk():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as s:
        book_id, _ = await _seed(s, with_job=True)
    try:
        # list_for_book surfaces the blocking job
        async with SessionLocal() as s:
            blocking = await jobs_repo.list_for_book(s, book_id)
        assert len(blocking) == 1
        assert blocking[0].status == "done"
        # the raw clear-before-insert really does violate the FK (the WHY)
        with pytest.raises(IntegrityError):
            async with SessionLocal() as s:
                await toc_repo.delete_for_book(s, book_id)
                await s.commit()
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_list_for_book_empty_when_no_jobs_and_delete_succeeds():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as s:
        book_id, _ = await _seed(s, with_job=False)
    try:
        async with SessionLocal() as s:
            assert await jobs_repo.list_for_book(s, book_id) == []
        # a job-free book re-extracts fine — delete_for_book removes the entry
        async with SessionLocal() as s:
            removed = await toc_repo.delete_for_book(s, book_id)
            await s.commit()
        assert removed == 1
    finally:
        await _cleanup(book_id)
