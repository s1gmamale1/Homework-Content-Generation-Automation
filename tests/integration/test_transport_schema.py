"""Real-DB integration for the transport/auth_mode schema (Phase 4 Task 1).

Run with RUN_DB_INTEGRATION=1 + a DATABASE_URL pointing at a migrated Postgres.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book(sha: str, *, n: int = 1, status: str = "toc_ready"):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status=status)
        s.add(book)
        await s.flush()
        toc_ids = []
        for i in range(n):
            t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(t)
            await s.flush()
            toc_ids.append(t.id)
        await s.commit()
        return book.id, toc_ids


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        await s.execute(delete(AgentUsage).where(AgentUsage.book_id == book_id))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_homework_job_transport_defaults_to_cli():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, toc_ids = await _seed_book("a")
    try:
        async with SessionLocal() as s:
            job = HomeworkJob(book_id=book_id, toc_entry_id=toc_ids[0],
                              subject="math-algebra", status="pending")
            s.add(job)
            await s.commit()
            await s.refresh(job)
            assert job.transport == "cli"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_homework_job_transport_api_roundtrips():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, toc_ids = await _seed_book("b")
    try:
        async with SessionLocal() as s:
            job = HomeworkJob(book_id=book_id, toc_entry_id=toc_ids[0],
                              subject="math-algebra", status="pending",
                              transport="api")
            s.add(job)
            await s.commit()
            job_id = job.id
        async with SessionLocal() as s:
            fetched = await s.get(HomeworkJob, job_id)
            assert fetched.transport == "api"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_transport_defaults_to_cli():
    from app.db import SessionLocal
    from app.models.batch import Batch
    book_id, _ = await _seed_book("c")
    try:
        async with SessionLocal() as s:
            batch = Batch(book_id=book_id, subject="math-algebra", provider="gemini")
            s.add(batch)
            await s.commit()
            await s.refresh(batch)
            assert batch.transport == "cli"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_transport_api_roundtrips():
    from app.db import SessionLocal
    from app.models.batch import Batch
    book_id, _ = await _seed_book("d")
    try:
        async with SessionLocal() as s:
            batch = Batch(book_id=book_id, subject="math-algebra", provider="gemini",
                          transport="api")
            s.add(batch)
            await s.commit()
            batch_id = batch.id
        async with SessionLocal() as s:
            fetched = await s.get(Batch, batch_id)
            assert fetched.transport == "api"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_unique_per_book_and_transport():
    """Migration 0024 swapped UNIQUE(book_id) -> UNIQUE(book_id, transport):
    a cli and an api batch for the same book both succeed, but a second batch
    on an already-taken (book_id, transport) pair is rejected."""
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models.batch import Batch
    book_id, _ = await _seed_book("f")
    try:
        # cli + api for the same book are both allowed (the point of 0024).
        async with SessionLocal() as s:
            s.add(Batch(book_id=book_id, subject="math-algebra",
                        provider="gemini", transport="cli"))
            s.add(Batch(book_id=book_id, subject="math-algebra",
                        provider="gemini", transport="api"))
            await s.commit()
        # A second cli batch collides on (book_id, "cli") and must fail.
        async with SessionLocal() as s:
            s.add(Batch(book_id=book_id, subject="math-algebra",
                        provider="gemini", transport="cli"))
            with pytest.raises(IntegrityError):
                await s.commit()
    finally:
        # The flush above poisons that session; clean up via a fresh one.
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_agent_usage_auth_mode_defaults_to_cli():
    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    book_id, _ = await _seed_book("e")
    try:
        async with SessionLocal() as s:
            usage = AgentUsage(book_id=book_id, operation="toc.extract",
                               provider="gemini")
            s.add(usage)
            await s.commit()
            await s.refresh(usage)
            assert usage.auth_mode == "cli"
    finally:
        await _cleanup(book_id)
