"""Real-DB: set_status guard refuses to clobber a cancelling/terminal job
(cancel-race-1). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_job(status: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="d" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(toc); await s.flush()
        job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                          subject="math-algebra", provider="claude", status=status)
        s.add(job); await s.commit()
        return book.id, job.id


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
async def test_running_write_cannot_clobber_cancelling():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("cancelling")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "running",
                                                 current_phase="flashcards")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "cancelling"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_cancelling_can_finalize_to_cancelled():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("cancelling")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "cancelled")
            await s.commit()
        assert changed is True
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "cancelled"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_terminal_done_is_frozen():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("done")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "running")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "done"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_normal_pending_to_running_still_works():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("running")
    try:
        async with SessionLocal() as s:
            assert await jobs_repo.set_status(s, job_id, "done") is True
            await s.commit()
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "done"
    finally:
        await _cleanup(book_id)
