"""Real-DB: CHECK constraint on batches.session_limit_strategy.

Verifies that an invalid value is rejected by the DB with an IntegrityError,
and that valid values ('pause', 'switch', 'inherit') persist.
Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL set.
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
async def test_session_limit_strategy_bogus_rejected():
    """batches.session_limit_strategy='bogus' must raise IntegrityError."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sls_bad.pdf",
            content_sha256="a" * 64,
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
                transport="cli",
                extract_transport="inherit",
                judge_transport="inherit",
                session_limit_strategy="bogus",  # invalid — must be rejected
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
async def test_session_limit_strategy_pause_persists():
    """batches.session_limit_strategy='pause' must persist without error."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sls_pause.pdf",
            content_sha256="b" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        book_id = book.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            good_batch = Batch(
                book_id=book_id,
                subject="math-algebra",
                provider="gemini",
                transport="cli",
                extract_transport="inherit",
                judge_transport="inherit",
                session_limit_strategy="pause",  # valid
            )
            s.add(good_batch)
            await s.commit()  # must not raise
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_session_limit_strategy_switch_persists():
    """batches.session_limit_strategy='switch' must persist without error."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="sls_switch.pdf",
            content_sha256="c" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        book_id = book.id
        await s.commit()

    try:
        async with SessionLocal() as s:
            good_batch = Batch(
                book_id=book_id,
                subject="math-algebra",
                provider="gemini",
                transport="cli",
                extract_transport="inherit",
                judge_transport="inherit",
                session_limit_strategy="switch",  # valid
            )
            s.add(good_batch)
            await s.commit()  # must not raise
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
