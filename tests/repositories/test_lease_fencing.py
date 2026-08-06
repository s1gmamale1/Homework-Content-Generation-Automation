"""Real-DB: claim_next_job mints a per-claim token and records the `claimed`
ledger event in the same transaction (fenced job leases, Task 3).

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)

# Credential-only capability shape (`worker._compute_capabilities`). The
# seeded job below is transport='cli' throughout (content/judge/extract/
# solver all default cli/inherit), so the claim gate's api-capability arms
# never fire either way — kept True to prove they aren't what's gating.
ANY_CAPS = {
    "can_claude_api": True,
    "can_gemini_api": True,
    "can_clodex_api": True,
}


# ---------------------------------------------------------------------------
# db_session fixture — provides a real AsyncSession for each test, rolling
# back after each test to keep tests isolated. Mirrors the idiom in
# tests/repositories/test_launch_defaults.py / test_migration_0052_lease.py.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# seed_pending_job fixture — a single claimable pending job with valid FKs
# (book + toc_entry), mirroring tests/integration/test_clock_skew.py's
# _seed_section helper. Seeded + committed in ITS OWN session (not
# db_session) and cleaned up afterward — mirrors production: the worker's
# claiming session never already holds the job in its identity map, so
# `claim_next_job`'s post-UPDATE `session.get()` reads the fresh row rather
# than a stale in-memory copy from before the UPDATE.
# ---------------------------------------------------------------------------

@pytest.fixture
async def seed_pending_job():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import delete

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="lease-fencing.pdf",
            content_sha256="2" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()

        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()

        job = await jobs_repo.create(
            s,
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math-algebra",
            output_language="uz",
        )
        await s.commit()
        book_id, toc_id, job_id = book.id, toc.id, job.id

    yield job

    async with SessionLocal() as s:
        from app.models.job_lease_event import JobLeaseEvent

        await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id == job_id))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_claim_mints_token_and_records_event(db_session, seed_pending_job):
    from app.repositories import jobs as jobs_repo
    from app.models.job_lease_event import JobLeaseEvent

    claimed = await jobs_repo.claim_next_job(
        db_session, worker_id="h:1@sha", capabilities=ANY_CAPS, max_attempts=5
    )

    assert claimed is not None
    assert claimed.lease.claim_token is not None
    assert claimed.job.claim_token == claimed.lease.claim_token
    assert claimed.job.id == seed_pending_job.id
    assert claimed.lease.job_id == seed_pending_job.id
    assert claimed.lease.owner_id == "h:1@sha"

    ev = (
        await db_session.execute(
            select(JobLeaseEvent).where(JobLeaseEvent.job_id == claimed.job.id)
        )
    ).scalars().all()
    assert any(
        e.event_type == "claimed" and e.claim_token == claimed.lease.claim_token
        for e in ev
    )

    # Commit (rather than letting the db_session fixture roll back) so the
    # claim's row lock on homework_jobs is released before seed_pending_job's
    # teardown tries to DELETE that same row — an open db_session rollback
    # racing an in-flight DELETE from a second connection self-deadlocks.
    await db_session.commit()
