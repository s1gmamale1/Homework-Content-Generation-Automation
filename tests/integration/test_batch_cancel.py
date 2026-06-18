"""Real-DB: cancel_all_in_batch flips pending->cancelled and running->cancelling,
leaving done/failed untouched. RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_batch_with_statuses(statuses):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="a1" * 32, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        batch = Batch(book_id=book.id, subject="math-algebra", grade="9",
                      provider="claude", transport="cli")
        s.add(batch); await s.flush()
        job_ids = {}
        for i, st in enumerate(statuses):
            toc = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(toc); await s.flush()
            job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                              subject="math-algebra", provider="claude",
                              status=st, batch_id=batch.id)
            s.add(job); await s.flush()
            job_ids[st] = job.id
        await s.commit()
        return book.id, batch.id, job_ids


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_cancel_all_in_batch_mixed_states():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, batch_id, ids = await _seed_batch_with_statuses(
        ["pending", "running", "done", "failed"])
    try:
        async with SessionLocal() as s:
            counts = await jobs_repo.cancel_all_in_batch(s, batch_id)
            await s.commit()
        assert counts == {"cancelled": 1, "cancelling": 1}
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, ids["pending"]) == "cancelled"
            assert await jobs_repo.get_status(s, ids["running"]) == "cancelling"
            assert await jobs_repo.get_status(s, ids["done"]) == "done"
            assert await jobs_repo.get_status(s, ids["failed"]) == "failed"
    finally:
        await _cleanup(book_id)
