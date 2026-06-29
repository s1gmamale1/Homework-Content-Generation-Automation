"""Real-DB proof of Phase 0.5: every queue/lease comparison reasons on the
DB clock (func.now()), so a host-clock skew can't break claiming, and every
converted interval query actually compiles + runs on Postgres.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL points at a throwaway PG.

Run:
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_clock_skew.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="clock-skew.pdf",
        content_sha256="1" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


@pytest.mark.asyncio
async def test_just_scheduled_job_is_immediately_claimable():
    """The canonical skew symptom: a job whose scheduled_at == DB now() must be
    claimable RIGHT AWAY. Under the old host-clock filter (`scheduled_at <= host
    now()`), a DB-set scheduled_at microseconds ahead of a drifting host clock
    made the job briefly 'not due' (the T1 flake). With `scheduled_at <= func.now()`
    the comparison is wholly on the DB clock and is deterministic. NO past-pinning."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        # scheduled_at defaults to server NOW() (we do NOT pin it to the past)
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra", output_language="uz")
        await s.commit()
        book_id = book.id
    try:
        async with SessionLocal() as s:
            claimed = await jobs_repo.claim_next_job(s, worker_id="W", max_attempts=3)
            assert claimed is not None, "a just-scheduled job was not immediately claimable"
            await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            from app.models.toc_entry import TOCEntry
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            from app.models.book import Book
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_converted_interval_queries_run_on_postgres():
    """make_interval-based cutoffs + queue_depth filter must compile + execute
    on real Postgres (the unit suite is DB-free and can't catch a bad render)."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        # Each returns an int and does not raise -> the func.now()/make_interval SQL is valid.
        assert isinstance(await jobs_repo.reclaim_stuck_jobs(s, stale_after_seconds=120), int)
        assert isinstance(await jobs_repo.reclaim_stale_cancelling(s, 120), int)
        assert isinstance(await jobs_repo.queue_depth(s), int)
        await s.commit()


@pytest.mark.asyncio
async def test_retry_backoff_schedules_in_the_future_server_side():
    """mark_failed_with_retry must push scheduled_at into the future using the DB
    clock (func.now() + make_interval), leaving the job not-yet-claimable."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        job = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra", output_language="uz")
        job.attempts = 1  # one attempt already spent -> retry branch, not terminal
        await s.commit()
        job_id, book_id = job.id, book.id
    try:
        async with SessionLocal() as s:
            status = await jobs_repo.mark_failed_with_retry(
                s, job_id, error_message="boom", max_attempts=3, backoff_seconds=30
            )
            await s.commit()
            assert status == "pending"
        async with SessionLocal() as s:
            # Backoff is in the future on the DB clock -> not claimable right now.
            assert await jobs_repo.claim_next_job(s, worker_id="W", max_attempts=3) is None
            await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            from app.models.toc_entry import TOCEntry
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            from app.models.book import Book
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_heartbeat_is_db_stamped_and_reports_online():
    """upsert_heartbeat stamps the DB clock; list_with_liveness evaluates against
    the DB clock -> a just-beaten worker is online, and its stored last_heartbeat
    matches the DB now() (not the host clock) within a small window."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "skew-host:5555"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            db_now = await s.scalar(select(func.now()))
            row = await s.scalar(select(WorkerNode).where(WorkerNode.pc_id == pc))
            # DB-stamped: within 5s of the DB's own now(), independent of host clock.
            assert abs((db_now - row.last_heartbeat).total_seconds()) < 5
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == pc]
        assert mine and mine[0]["online"] is True
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()
