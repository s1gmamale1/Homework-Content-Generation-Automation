"""Real-DB: CHECK constraints on homework_jobs and batches enum-like columns.

Verifies that invalid values for status / transport / extract_transport /
judge_transport are rejected by the DB with an IntegrityError, and that valid
rows are accepted. Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL set.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_homework_jobs_bad_status_rejected():
    """A homework_jobs row with status='bogus' must raise IntegrityError."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ck_test.pdf",
            content_sha256="a" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        book_id = book.id
        toc_id = toc.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            bad_job = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="bogus",          # invalid — must be rejected
                provider="gemini",
                transport="cli",
                extract_transport="inherit",
                judge_transport="inherit",
            )
            s.add(bad_job)
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_homework_jobs_valid_status_accepted():
    """A homework_jobs row with status='pending' and valid transports must succeed."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ck_valid.pdf",
            content_sha256="b" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L2", order_index=0)
        s.add(toc)
        await s.flush()
        book_id = book.id
        toc_id = toc.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            good_job = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="pending",        # valid
                provider="gemini",
                transport="cli",         # valid
                extract_transport="inherit",  # valid
                judge_transport="api",        # valid
            )
            s.add(good_job)
            await s.commit()             # must not raise
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_homework_jobs_bad_transport_rejected():
    """A homework_jobs row with transport='bogus' must raise IntegrityError."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ck_transport.pdf",
            content_sha256="c" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L3", order_index=0)
        s.add(toc)
        await s.flush()
        book_id = book.id
        toc_id = toc.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            bad_job = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="pending",
                provider="gemini",
                transport="bogus",       # invalid — must be rejected
                extract_transport="inherit",
                judge_transport="inherit",
            )
            s.add(bad_job)
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_batches_bad_transport_rejected():
    """A batches row with transport='bogus' must raise IntegrityError."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ck_batch.pdf",
            content_sha256="d" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        book_id = book.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            bad_batch = Batch(
                book_id=book_id,
                subject="math-algebra",
                provider="gemini",
                transport="bogus",       # invalid — must be rejected
                extract_transport="inherit",
                judge_transport="inherit",
            )
            s.add(bad_batch)
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_homework_jobs_bad_extract_transport_rejected():
    """extract_transport='bogus' must raise IntegrityError."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ck_xt.pdf",
            content_sha256="e" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L4", order_index=0)
        s.add(toc)
        await s.flush()
        book_id = book.id
        toc_id = toc.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            bad_job = HomeworkJob(
                book_id=book_id,
                toc_entry_id=toc_id,
                subject="math-algebra",
                status="pending",
                provider="gemini",
                transport="cli",
                extract_transport="bogus",   # invalid
                judge_transport="inherit",
            )
            s.add(bad_job)
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
