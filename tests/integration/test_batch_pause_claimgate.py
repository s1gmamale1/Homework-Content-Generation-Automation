"""DB-integration tests for the batch-pause claim gate (Task 4 / C4).

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL points at a throwaway
Postgres (same pattern as test_claim_contention.py).

Run:
  docker run -d --name fleet-pg -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
    -e POSTGRES_DB=edu_homework -p 5433:5432 postgres:16-alpine
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    uv run alembic upgrade head
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    uv run python -m pytest tests/integration/test_batch_pause_claimgate.py -v

Test matrix:
  1. paused-batch job is NOT claimable; after unpause_batch it IS.
  2. NULL-arm regression — batchless job (batch_id IS NULL) stays claimable
     while a DIFFERENT batch is paused. (This test FAILS if the IS NULL arm
     is removed — confirmed during RED phase.)
  3. job in a non-paused batch claims normally (unaffected by another paused
     batch in the same DB).
  4. pause-claim guarantee — pause_batch does NOT alter any job row status.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# Attempts fence: same pattern as test_claim_contention.py — keeps test jobs
# invisible to real workers that poll with max_attempts=3.
_FENCE_ATTEMPTS = 7
_FENCE_MAX = 8
_FENCE_PRIORITY = 1000


# ---------------------------------------------------------------------------
# Seed / cleanup helpers
# ---------------------------------------------------------------------------

async def _seed_book(s, name: str):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256=("a" * 64),
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _seed_job(s, book, toc, **kwargs):
    from app.repositories import jobs as jobs_repo

    kwargs.setdefault("status", "pending")
    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra", **kwargs
    )
    job.attempts = _FENCE_ATTEMPTS
    job.priority = _FENCE_PRIORITY
    return job


async def _cleanup_book(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        # Batches FK -> book; cleanup after jobs.
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _claim(own_ids: set, worker_id: str = "W"):
    """Claim under default (all-cli) caps; commit iff we claimed our own row."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        job = await jobs_repo.claim_next_job(
            s, worker_id=worker_id, max_attempts=_FENCE_MAX
        )
        if job is not None and job.id not in own_ids:
            await s.rollback()
            return None  # foreign live row — ignored
        if job is not None:
            jid = job.id
            await s.commit()
            return jid
        await s.commit()
        return None


# ---------------------------------------------------------------------------
# Test 1 — paused-batch job is not claimable; after unpause it is
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paused_batch_job_not_claimable_then_claimable_after_unpause():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "pause-test-1.pdf")
        # Create a batch row to attach the job to
        from uuid import uuid4
        batch = Batch(
            id=uuid4(),
            book_id=book.id,
            subject="math-algebra",
            provider="gemini",
            transport="cli",
        )
        s.add(batch)
        await s.flush()
        job = await _seed_job(s, book, toc, batch_id=batch.id, transport="cli")
        await s.commit()
        book_id, batch_id, job_id = book.id, batch.id, job.id

    try:
        # 1a. Pause the batch.
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_id, "test-pause")
            await s.commit()

        # 1b. Job must NOT be claimable while batch is paused.
        claimed = await _claim({job_id}, "W1")
        assert claimed != job_id, "job in paused batch must not be claimable"

        # Confirm job is still pending (not consumed).
        async with SessionLocal() as s:
            j = await jobs_repo.get(s, job_id)
            assert j.status == "pending", "job must still be pending, not consumed"

        # 1c. Unpause and verify the job is now claimable.
        async with SessionLocal() as s:
            await batches_repo.unpause_batch(s, batch_id)
            await s.commit()

        claimed = await _claim({job_id}, "W2")
        assert claimed == job_id, "after unpause, job must be claimable"
    finally:
        await _cleanup_book(book_id)


# ---------------------------------------------------------------------------
# Test 2 — NULL-arm regression: batchless job stays claimable while a
# different batch is paused. MUST FAIL if IS NULL arm is removed.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batchless_job_unaffected_by_paused_batch():
    """A job with batch_id IS NULL must remain claimable even when another
    batch is paused.

    Without the IS NULL arm: `NULL NOT IN (non-empty set)` = SQL NULL →
    the row is excluded → this test FAILS. That's the RED proof.
    """
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        # Book A: has a batch (will be paused)
        book_a, toc_a = await _seed_book(s, "pause-test-2a.pdf")
        from uuid import uuid4
        batch = Batch(
            id=uuid4(),
            book_id=book_a.id,
            subject="math-algebra",
            provider="gemini",
            transport="cli",
        )
        s.add(batch)
        await s.flush()
        batched_job = await _seed_job(s, book_a, toc_a, batch_id=batch.id, transport="cli")

        # Book B: batchless job (batch_id IS NULL)
        book_b, toc_b = await _seed_book(s, "pause-test-2b.pdf")
        batchless_job = await _seed_job(s, book_b, toc_b, transport="cli")
        # Give batchless a LOWER priority so it's not picked first normally —
        # but since the batched job is blocked, batchless must be returned.
        batchless_job.priority = _FENCE_PRIORITY - 1

        await s.commit()
        book_a_id, book_b_id = book_a.id, book_b.id
        batch_id, batched_id, batchless_id = batch.id, batched_job.id, batchless_job.id

    try:
        # Pause the batch (batched job must be invisible to workers).
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_id, "null-arm-regression")
            await s.commit()

        # 🔴 KEY ASSERTION: batchless job must still be claimable.
        claimed = await _claim({batched_id, batchless_id}, "W-nullarm")
        assert claimed == batchless_id, (
            "batchless job (batch_id IS NULL) must remain claimable while another "
            "batch is paused — FAILED: IS NULL arm is missing or broken"
        )
    finally:
        await _cleanup_book(book_a_id)
        await _cleanup_book(book_b_id)


# ---------------------------------------------------------------------------
# Test 3 — job in non-paused batch claims normally alongside a paused batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unpaused_batch_job_claims_normally():
    """A job in a non-paused batch is unaffected by another paused batch."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        # Book A: paused batch
        book_a, toc_a = await _seed_book(s, "pause-test-3a.pdf")
        from uuid import uuid4
        batch_a = Batch(
            id=uuid4(),
            book_id=book_a.id,
            subject="math-algebra",
            provider="gemini",
            transport="cli",
        )
        s.add(batch_a)
        await s.flush()
        job_a = await _seed_job(s, book_a, toc_a, batch_id=batch_a.id, transport="cli")

        # Book B: active (non-paused) batch
        book_b, toc_b = await _seed_book(s, "pause-test-3b.pdf")
        batch_b = Batch(
            id=uuid4(),
            book_id=book_b.id,
            subject="math-algebra",
            provider="gemini",
            transport="cli",
        )
        s.add(batch_b)
        await s.flush()
        job_b = await _seed_job(s, book_b, toc_b, batch_id=batch_b.id, transport="cli")
        # Make job_b sort second so we prove the paused-batch job is truly skipped
        job_b.priority = _FENCE_PRIORITY - 1

        await s.commit()
        book_a_id, book_b_id = book_a.id, book_b.id
        batch_a_id = batch_a.id
        job_a_id, job_b_id = job_a.id, job_b.id

    try:
        # Pause only batch_a.
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_a_id, "test-pause-3")
            await s.commit()

        both = {job_a_id, job_b_id}
        # First claim must be job_b (the active-batch job); job_a is blocked.
        claimed = await _claim(both, "W3")
        assert claimed == job_b_id, (
            f"job in non-paused batch must claim normally; got {claimed}"
        )
        # No more claimable jobs (job_a is paused).
        second = await _claim(both, "W3b")
        assert second != job_a_id, "job in paused batch must not be claimable"
    finally:
        await _cleanup_book(book_a_id)
        await _cleanup_book(book_b_id)


# ---------------------------------------------------------------------------
# Test 4 — pause-claim guarantee: pause_batch does NOT alter job row status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_batch_does_not_alter_job_status():
    """pause_batch only gates claiming — it must NOT change any job row status.

    A pending job and a running job in the paused batch must both keep their
    status unchanged. This enforces the 'never hard-cancel paid work' contract.
    """
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "pause-test-4.pdf")
        from uuid import uuid4
        batch = Batch(
            id=uuid4(),
            book_id=book.id,
            subject="math-algebra",
            provider="gemini",
            transport="cli",
        )
        s.add(batch)
        await s.flush()

        # pending job
        pending_job = await _seed_job(s, book, toc, batch_id=batch.id)

        # running job (simulate claimed)
        from app.models.toc_entry import TOCEntry
        toc2 = TOCEntry(book_id=book.id, section_title="L2", order_index=1)
        s.add(toc2)
        await s.flush()
        running_job_obj = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc2.id,
            subject="math-algebra", batch_id=batch.id, status="running",
        )

        await s.commit()
        book_id, batch_id = book.id, batch.id
        pending_id, running_id = pending_job.id, running_job_obj.id

    try:
        # Pause the batch.
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_id, "test-pause-4")
            await s.commit()

        # Verify job statuses are unchanged.
        async with SessionLocal() as s:
            p = await jobs_repo.get(s, pending_id)
            r = await jobs_repo.get(s, running_id)
            assert p.status == "pending", (
                f"pending job status must not change after pause; got {p.status}"
            )
            assert r.status == "running", (
                f"running job status must not change after pause; got {r.status}"
            )
    finally:
        await _cleanup_book(book_id)
