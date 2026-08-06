"""Real-DB end-to-end: an obsolete worker CANNOT mutate a reclaimed job
(fenced job leases, Task 7).

The core scenario the whole feature exists to prevent:

  * Worker A claims a job (mints token T1).
  * A's claim is forced stale (claimed_at pushed into the past, no live
    registry row for A) and a reclaim sweep promotes it back to pending,
    rotating the claim_token off the row.
  * Worker B re-claims the same job (mints a fresh token T2) and now owns it.
  * A "wakes up" and tries its worker-owned writes with its DEAD token T1 —
    the fenced `done` / `failed` / phase writes all no-op with `LeaseLost`,
    the job is NEVER marked done (or failed) by A, and B still owns it.

This is the anti-double-completion guarantee. The broader race matrix and the
startup-reclaim path are covered by tasks 8/10; this test stays focused.

RUN_DB_INTEGRATION=1 required (real Postgres via the scratch DB recipe).
"""
from __future__ import annotations

import os
import uuid as _uuid

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)


@pytest.fixture
async def seed_pending_job():
    """A committed, claimable (pending, cli) homework job over a fresh book +
    section. Yields the job id; tears the whole graph down afterward."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    book_ids: list = []

    async def make():
        async with SessionLocal() as s:
            book = Book(
                subject="math-algebra",
                original_filename="reclaim-e2e.pdf",
                content_sha256=_uuid.uuid4().hex.ljust(64, "f"),
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
                transport="cli",
            )
            await s.commit()
            book_ids.append(book.id)
            return job.id

    yield make

    async with SessionLocal() as s:
        for bid in book_ids:
            job_ids = select(HomeworkJob.id).where(HomeworkJob.book_id == bid)
            await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_(job_ids)))
            await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        await s.commit()


@pytest.mark.asyncio
async def test_paused_worker_cannot_mutate_after_reclaim(seed_pending_job):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    from app.services.lease import JobLease

    job_id = await seed_pending_job()

    # ── A claims (token T1) ───────────────────────────────────────────────
    async with SessionLocal() as s:
        async with s.begin():
            claimed_a = await jobs_repo.claim_next_job(
                s, worker_id="A-worker:1@shaAAA", max_attempts=3
            )
    assert claimed_a is not None
    token_a = claimed_a.lease.claim_token
    lease_a = JobLease(job_id=job_id, claim_token=token_a, owner_id="A-worker:1@shaAAA")

    # ── force A's claim stale (past claimed_at; no live registry row for A) ─
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE homework_jobs SET claimed_at = now() - interval '1 hour' "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )

    # ── reclaim: job -> pending, token rotated off the row ────────────────
    async with SessionLocal() as s:
        async with s.begin():
            n = await jobs_repo.reclaim_stuck_jobs(s, stale_after_seconds=1)
    assert n >= 1
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status == "pending"
        assert job.claim_token is None

    # ── B re-claims (token T2) ────────────────────────────────────────────
    async with SessionLocal() as s:
        async with s.begin():
            claimed_b = await jobs_repo.claim_next_job(
                s, worker_id="B-worker:2@shaBBB", max_attempts=3
            )
    assert claimed_b is not None
    token_b = claimed_b.lease.claim_token
    assert token_b != token_a, "B must mint a fresh token, not inherit A's"

    # ── A "wakes up" and tries its worker-owned writes with the DEAD T1 ────
    # 1) the critical anti-double-completion write: mark done.
    async with SessionLocal() as s:
        async with s.begin():
            done_res = await jobs_repo.set_status(
                s, job_id, "done",
                completed_at=datetime.now(timezone.utc),
                claim_token=token_a,
            )
    assert done_res is lease.LeaseLost, "A must NOT be able to mark a reclaimed job done"

    # 2) A cannot even fail the job B now owns.
    async with SessionLocal() as s:
        async with s.begin():
            fail_res = await jobs_repo.mark_failed_with_retry(
                s, job_id, error_message="A stale failure", max_attempts=3,
                claim_token=token_a,
            )
    assert fail_res is lease.LeaseLost

    # 3) A cannot open/reset a phase row on the reclaimed job.
    async with SessionLocal() as s:
        async with s.begin():
            phase_res = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="preview", phase_order=1,
                prompt_hash="h", model_name="m", status="running", lease=lease_a,
            )
    assert phase_res is lease.LeaseLost

    # ── B still owns the job; it is NOT done, NOT failed, and carries T2 ──
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status == "running", f"expected B still running, got {job.status!r}"
        assert job.claim_token == token_b, "B's token must remain on the row"
        assert job.claimed_by == "B-worker:2@shaBBB"
        # no phase row was written by A
        rows = (
            await s.execute(
                select(phase_repo.PhaseOutput).where(
                    phase_repo.PhaseOutput.job_id == job_id
                )
            )
        ).scalars().all()
        assert rows == [], "A's stale create_or_reset must have written nothing"
