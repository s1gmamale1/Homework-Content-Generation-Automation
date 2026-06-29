"""Real-DB proof of peer-aware startup reclaim (fleet-restart-reclaim-1).

Two cases:
  (a) Alone  — no live worker rows + a running job with a RECENT claimed_at
               → reclaim_orphans_on_startup resets it to `pending` (window=0).
  (b) Peer   — a fresh-beat workers row (peer) + two running jobs:
               one with RECENT claimed_at, one with STALE claimed_at.
               → after the call, the fresh job stays `running` (peer-protected)
               and the stale job is reset to `pending`.

RED-prove: before the real body the stub forces window=0 unconditionally;
case (b)'s "fresh job stays running" assertion must FAIL under the stub.
Then the real body is wired and both cases must be GREEN.

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_c5_accept \\
    uv run python -m pytest tests/integration/test_startup_reclaim.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# ── helpers ────────────────────────────────────────────────────────────────


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="startup-reclaim.pdf",
        content_sha256="a" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _make_running_job(s, book, toc, *, stale_seconds: int | None = None):
    """Create a running HomeworkJob.  If stale_seconds is set, back-date
    claimed_at by that many seconds using a raw SQL UPDATE so it's genuinely
    older than the lease window."""
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        output_language="uz",
    )
    # Directly set status to running + claimed_at to now()
    await s.execute(
        text(
            "UPDATE homework_jobs SET status='running', claimed_at=now(), "
            "claimed_by='test-worker' WHERE id=:id"
        ),
        {"id": job.id},
    )
    if stale_seconds is not None:
        await s.execute(
            text(
                "UPDATE homework_jobs "
                f"SET claimed_at = now() - interval '{stale_seconds} seconds' "
                "WHERE id=:id"
            ),
            {"id": job.id},
        )
    await s.flush()
    return job


async def _cleanup(book_ids: list):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        for bid in book_ids:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        await s.commit()


async def _cleanup_workers(pc_ids: list):
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    async with SessionLocal() as s:
        for pc in pc_ids:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
        await s.commit()


# ── case (a): alone → window=0 → fresh job IS reset ───────────────────────


@pytest.mark.asyncio
async def test_alone_resets_fresh_job():
    """No live workers → window=0 → even a recently-claimed running job is
    reclaimed to pending (instant single-host recovery preserved)."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    book_ids = []
    try:
        async with SessionLocal() as s:
            book, toc = await _seed_section(s)
            book_ids.append(book.id)
            job = await _make_running_job(s, book, toc)  # fresh claimed_at
            await s.commit()
            job_id = job.id

        async with SessionLocal() as s:
            from app.repositories import jobs as jobs_repo

            n = await jobs_repo.reclaim_orphans_on_startup(
                s, reclaim_stale_seconds=120
            )
            await s.commit()
            assert n >= 1, f"Expected ≥1 reclaimed, got {n}"

        async with SessionLocal() as s:
            row = await s.get(HomeworkJob, job_id)
            assert row is not None
            assert row.status == "pending", (
                f"Expected 'pending' but got '{row.status}' — "
                "alone path should reset even a fresh job"
            )
    finally:
        await _cleanup(book_ids)


# ── case (b): live peer → fresh job protected, stale job reset ────────────


@pytest.mark.asyncio
async def test_live_peer_protects_fresh_job_but_resets_stale():
    """A live peer forces lease window=120s.  Fresh job (claimed_at≈now) stays
    running; stale job (claimed_at=200s ago) is reset to pending."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    book_ids = []
    peer_pc = "test-peer-host:99999"
    try:
        async with SessionLocal() as s:
            # Seed peer worker with a fresh heartbeat
            from app.repositories import workers as workers_repo

            await workers_repo.upsert_heartbeat(s, peer_pc)

            book, toc = await _seed_section(s)
            book_ids.append(book.id)

            fresh_job = await _make_running_job(s, book, toc)  # fresh
            stale_job = await _make_running_job(
                s, book, toc, stale_seconds=200
            )  # stale (>120s)
            await s.commit()
            fresh_id = fresh_job.id
            stale_id = stale_job.id

        async with SessionLocal() as s:
            from app.repositories import jobs as jobs_repo

            n = await jobs_repo.reclaim_orphans_on_startup(
                s, reclaim_stale_seconds=120
            )
            await s.commit()
            assert n >= 1, f"Expected ≥1 reclaimed (the stale job), got {n}"

        async with SessionLocal() as s:
            fresh_row = await s.get(HomeworkJob, fresh_id)
            stale_row = await s.get(HomeworkJob, stale_id)
            assert fresh_row is not None
            assert stale_row is not None
            assert fresh_row.status == "running", (
                f"Fresh job should still be 'running' but got '{fresh_row.status}' — "
                "live peer should protect it"
            )
            assert stale_row.status == "pending", (
                f"Stale job should be 'pending' but got '{stale_row.status}' — "
                "stale job should still be reclaimed even when a peer is present"
            )
    finally:
        await _cleanup(book_ids)
        await _cleanup_workers([peer_pc])
