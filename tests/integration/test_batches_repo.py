"""Real-DB: get_or_create_for_book is idempotent per book (race-safe), and the
rollup is per-lesson-latest (a retried lesson counts once). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lessons(s, n=3):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256="3" * 64, file_size_bytes=1, status="toc_ready")
    s.add(book)
    await s.flush()
    tocs = []
    for i in range(n):
        t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
        s.add(t)
        tocs.append(t)
    await s.flush()
    return book, tocs


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_per_book():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lessons(s)
        await s.commit()
        book_id = book.id
    try:
        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None)
            await s.commit()
            b1_id = b1.id
        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="gemini", model=None)
            await s.commit()
            b2_id = b2.id
        assert b1_id == b2_id, "second call must return the SAME batch (one per book)"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_rollup_is_per_lesson_latest():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None)
        for t in tocs:
            await jobs_repo.create(s, book_id=book.id, toc_entry_id=t.id,
                                   subject="math-algebra", batch_id=batch.id)
        await s.commit()
        book_id, batch_id, first_id = book.id, batch.id, tocs[0].id
    try:
        # Lesson 0: simulate a failed-then-retried lesson -> a SECOND (newer) job.
        async with SessionLocal() as s:
            old_id = (await s.execute(
                select(HomeworkJob.id).where(HomeworkJob.toc_entry_id == first_id))
            ).scalar_one()
            old = await s.get(HomeworkJob, old_id)
            old.status = "failed"
            await s.commit()
        async with SessionLocal() as s:
            await jobs_repo.create(s, book_id=book_id, toc_entry_id=first_id,
                                   subject="math-algebra", batch_id=batch_id)  # newer pending
            await s.commit()
        async with SessionLocal() as s:
            tally = await batches_repo.rollup_for_batch(s, batch_id)
        # Per-lesson-latest: lesson0's latest is the newer pending -> 3 pending,
        # 0 failed -> reconciles to 3 lessons, NOT 4 jobs.
        assert sum(tally.values()) == 3, f"denominator must be 3 lessons, got {tally}"
        assert tally.get("pending") == 3
        assert tally.get("failed", 0) == 0
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
