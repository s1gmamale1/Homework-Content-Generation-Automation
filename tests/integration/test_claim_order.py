"""Real-DB proof: claim_next_job returns jobs in ascending TOC order.

Tests that when multiple pending jobs share the same priority and scheduled_at,
they are claimed in ascending `toc_entries.order_index` order (i.e., lesson 1
before lesson 2 before lesson 3, etc.), regardless of the order in which the
job rows were inserted.

Run:
  createdb -U macmini5 edu_claimorder_test
  DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_claimorder_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_claimorder_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_claim_order.py tests/integration/test_claim_gate_self_grade.py \\
    tests/integration/test_claim_contention.py -q
  dropdb -U macmini5 edu_claimorder_test
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# ─── Constants ────────────────────────────────────────────────────────────────

_FENCE_MAX = 8
_FENCE_ATTEMPTS = 7
_FIXED_PRIORITY = 500
# Insert jobs in this scrambled order_index sequence to prove ordering is not
# based on insert order.
_SCRAMBLED_ORDER = [3, 0, 5, 1, 4, 2]

# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _seed_book_with_toc(s, name: str, n_entries: int):
    """Create one book with n_entries TOC entries (order_index 0..n_entries-1)."""
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256="c" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()

    toc_entries = []
    for i in range(n_entries):
        toc = TOCEntry(
            book_id=book.id,
            section_title=f"Lesson {i + 1}",
            order_index=i,
        )
        s.add(toc)
        toc_entries.append(toc)

    await s.flush()
    return book, toc_entries


async def _cleanup_book(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _claim_one(caps: dict, own_ids: set, worker_id: str = "W-order"):
    """Claim the next job and return its id (committed), or None if queue empty."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        claimed = await jobs_repo.claim_next_job(
            s,
            worker_id=worker_id,
            max_attempts=_FENCE_MAX,
            capabilities=caps,
        )
        job = claimed.job if claimed is not None else None
        if job is not None and job.id not in own_ids:
            # A foreign job leaked in (parallel test pollution) — skip it.
            await s.rollback()
            return None
        if job is not None:
            job_id = job.id
            await s.commit()
            return job_id
        await s.commit()
        return None


def _cli_caps() -> dict:
    """All-pass CLI capability dict (no API keys needed)."""
    from app.services.worker import _compute_capabilities

    return _compute_capabilities({})


# ─── Test ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_ascending_lesson_order():
    """Jobs with equal priority+scheduled_at are claimed in ascending order_index.

    Seed 6 TOC entries (order_index 0-5) and 6 pending jobs inserted in scrambled
    order (3,0,5,1,4,2).  Repeated claim_next_job calls must return them as
    [0,1,2,3,4,5].

    BITE-PROVE: without the lesson_order.asc() tiebreaker the assertion fails
    (claims arrive in DB-insert / heap order, NOT TOC order).
    """
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import jobs as jobs_repo

    n = len(_SCRAMBLED_ORDER)  # 6

    async with SessionLocal() as s:
        book, toc_entries = await _seed_book_with_toc(s, "order-test.pdf", n)

        # Create one batch so all jobs share the same batch_id.
        batch = Batch(
            book_id=book.id,
            subject="math-algebra",
            provider="claude",
            transport="cli",
        )
        s.add(batch)
        await s.flush()

        # Build a map from order_index → TOCEntry for easy lookup.
        toc_by_order = {toc.order_index: toc for toc in toc_entries}

        # Use a fixed past timestamp so scheduled_at < NOW() for all jobs.
        fixed_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

        # Insert jobs in SCRAMBLED order to prove ordering is not insert-based.
        job_ids = []
        toc_order_by_job_id: dict = {}  # job_id → order_index
        for oi in _SCRAMBLED_ORDER:
            toc = toc_by_order[oi]
            job = await jobs_repo.create(
                s,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math-algebra",
                output_language="uz",
                provider="claude",
                transport="cli",
                extract_transport="cli",
                judge_transport="cli",
                batch_id=batch.id,
            )
            # Pin attempts to just under the fence so they remain claimable.
            job.attempts = _FENCE_ATTEMPTS
            job.priority = _FIXED_PRIORITY
            job.scheduled_at = fixed_at
            await s.flush()
            job_ids.append(job.id)
            toc_order_by_job_id[job.id] = oi

        await s.commit()
        book_id = book.id

    own_ids = set(job_ids)
    caps = _cli_caps()

    claimed_order_indices: list[int] = []
    try:
        for _ in range(n):
            jid = await _claim_one(caps, own_ids)
            assert jid is not None, (
                f"Expected to claim job #{_ + 1} of {n}, got None. "
                "Queue may have dried up prematurely."
            )
            claimed_order_indices.append(toc_order_by_job_id[jid])

        assert claimed_order_indices == list(range(n)), (
            f"Jobs were NOT claimed in ascending lesson order.\n"
            f"  Expected: {list(range(n))}\n"
            f"  Got:      {claimed_order_indices}\n"
            "The lesson_order.asc() tiebreaker in claim_next_job is missing or wrong."
        )
    finally:
        await _cleanup_book(book_id)
