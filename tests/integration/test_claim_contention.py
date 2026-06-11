"""Real-DB proof: FOR UPDATE SKIP LOCKED prevents two workers claiming one job.

Skipped unless RUN_DB_INTEGRATION=1 AND a real DATABASE_URL points at a
throwaway Postgres (the default unit suite is DB-free — tests/conftest.py).

Run:
  docker run -d --name fleet-pg -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
    -e POSTGRES_DB=edu_homework -p 5433:5432 postgres:16-alpine
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    alembic upgrade head
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_claim_contention.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_two_concurrent_claims_never_collide():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    # seed: one book, one section, two pending jobs (committed)
    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="contention-test.pdf",
            content_sha256="0" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        # Both jobs keep their server-default scheduled_at = NOW(). No past-pinning
        # crutch is needed: claim_next_job now filters `scheduled_at <= func.now()`
        # (Phase 0.5), so claimability is wholly on the DB clock and host-vs-DB skew
        # can't flake this test. This un-pinned form IS the skew regression guard.
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        book_id = book.id

    try:
        # two sessions hold their claims open simultaneously
        async with SessionLocal() as sa, SessionLocal() as sb:
            job_a = await jobs_repo.claim_next_job(sa, worker_id="A", max_attempts=3)
            # sa's row is locked-but-uncommitted; sb must SKIP it and take the other
            job_b = await jobs_repo.claim_next_job(sb, worker_id="B", max_attempts=3)
            assert job_a is not None, "worker A claimed nothing"
            assert job_b is not None, "worker B claimed nothing"
            assert job_a.id != job_b.id, "two workers claimed the SAME job"
            await sa.commit()
            await sb.commit()

        # no pending jobs left -> a third claim returns None
        async with SessionLocal() as sc:
            assert await jobs_repo.claim_next_job(sc, worker_id="C", max_attempts=3) is None
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_api_jobs_gated_on_has_api_keys():
    """Fail-fast at claim time: a worker WITHOUT both API keys must never
    claim a `transport='api'` job, even if that api job sorts first (higher
    priority). Only `transport='cli'` jobs are claimable. When the worker
    declares both keys present (`has_api_keys=True`), both transports are
    claimable. Gating at claim covers the extract-failover path too."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="api-gate-test.pdf",
            content_sha256="1" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()

        # The api job is given higher priority so a transport-blind claim would
        # return it FIRST — proving the gate (not just luck of ordering).
        api_job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            transport="api",
        )
        api_job.priority = 10
        cli_job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            transport="cli",
        )
        cli_job.priority = 0
        await s.commit()
        book_id = book.id
        api_job_id = api_job.id
        cli_job_id = cli_job.id

    try:
        # No keys: only the cli job is ever claimable; the api job is invisible.
        async with SessionLocal() as s:
            first = await jobs_repo.claim_next_job(
                s, worker_id="nokeys", max_attempts=3, has_api_keys=False
            )
            assert first is not None, "cli job should be claimable without keys"
            assert first.id == cli_job_id, "must claim the cli job, never the api job"
            await s.commit()
        # Draining: the api job must STILL not be claimable.
        async with SessionLocal() as s:
            second = await jobs_repo.claim_next_job(
                s, worker_id="nokeys", max_attempts=3, has_api_keys=False
            )
            assert second is None, "api job must never be claimed without keys"
            await s.commit()

        # Reset the cli job to pending so the keys=True path sees both again.
        async with SessionLocal() as s:
            cli = await s.get(HomeworkJob, cli_job_id)
            cli.status = "pending"
            cli.claimed_by = None
            cli.attempts = 0
            await s.commit()

        # With both keys: BOTH transports are claimable. Drain both.
        claimed_ids = set()
        async with SessionLocal() as s:
            j = await jobs_repo.claim_next_job(
                s, worker_id="keys", max_attempts=3, has_api_keys=True
            )
            assert j is not None
            claimed_ids.add(j.id)
            await s.commit()
        async with SessionLocal() as s:
            j = await jobs_repo.claim_next_job(
                s, worker_id="keys", max_attempts=3, has_api_keys=True
            )
            assert j is not None
            claimed_ids.add(j.id)
            await s.commit()
        assert claimed_ids == {api_job_id, cli_job_id}, "both jobs claimable with keys"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
