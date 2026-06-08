"""Real-DB: the batches table + homework_jobs.batch_id exist, UNIQUE(book_id)
holds, and a job can carry a batch_id. Skipped unless RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_batch_unique_per_book_and_job_fk():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="b.pdf",
                    content_sha256="2" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        b1 = Batch(book_id=book.id, subject="math-algebra", grade=None,
                   provider="claude", model=None)
        s.add(b1)
        await s.flush()
        job = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id,
                                     subject="math-algebra")
        job.batch_id = b1.id
        await s.commit()
        book_id, batch_id = book.id, b1.id

    try:
        # UNIQUE(book_id): a second batch for the same book must fail.
        async with SessionLocal() as s:
            s.add(Batch(book_id=book_id, subject="math-algebra", provider="claude"))
            with pytest.raises(IntegrityError):
                await s.commit()
        # The job kept its batch_id.
        async with SessionLocal() as s:
            jid = (await s.execute(
                select(HomeworkJob.id).where(HomeworkJob.batch_id == batch_id)
            )).scalar_one()
            j = await s.get(HomeworkJob, jid)
            assert j.batch_id == batch_id
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
