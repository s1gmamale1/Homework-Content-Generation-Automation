"""Real-DB integration test: unpause_by_reason reason-scoping.

Guards constraint #2: `unpause_by_reason("batch-cap")` must NOT clear a batch
paused with reason="manual". The test uses a real Postgres DB (no mocks) and
FAILS if the WHERE clause on paused_reason is removed from unpause_by_reason.

Run:
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_c5_accept \\
    uv run python -m pytest tests/integration/test_batch_pause_reason_scope.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ---------------------------------------------------------------------------
# Seed / cleanup helpers (same pattern as test_batch_pause_claimgate.py)
# ---------------------------------------------------------------------------

async def _seed_book(s, name: str):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256=("b" * 64),
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book


async def _seed_batch(s, book, transport: str = "cli"):
    from uuid import uuid4
    from app.models.batch import Batch

    batch = Batch(
        id=uuid4(),
        book_id=book.id,
        subject="math-algebra",
        provider="gemini",
        transport=transport,
    )
    s.add(batch)
    await s.flush()
    return batch


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


# ---------------------------------------------------------------------------
# Test: reason-scoping regression
# unpause_by_reason("batch-cap") must leave "manual"-paused batches untouched.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unpause_by_reason_does_not_clear_different_reason():
    """
    Seed batch A (paused reason='manual') and batch B (paused reason='batch-cap').
    Call unpause_by_reason('batch-cap').
    Assert:
      - batch A is STILL paused (paused_at non-null, paused_reason='manual')
      - batch B IS unpaused (paused_at None)

    This test FAILS if the .where(Batch.paused_reason == reason) guard is
    dropped from unpause_by_reason — batch A would also be cleared.
    """
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book_a = await _seed_book(s, "reason-scope-test-a.pdf")
        book_b = await _seed_book(s, "reason-scope-test-b.pdf")
        batch_a = await _seed_batch(s, book_a, transport="cli")
        batch_b = await _seed_batch(s, book_b, transport="api")
        await s.commit()
        book_a_id, book_b_id = book_a.id, book_b.id
        batch_a_id, batch_b_id = batch_a.id, batch_b.id

    try:
        # Pause batch A with reason="manual"
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_a_id, "manual")
            await s.commit()

        # Pause batch B with reason="batch-cap"
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_b_id, "batch-cap")
            await s.commit()

        # Budget monitor fires: unpause_by_reason("batch-cap")
        async with SessionLocal() as s:
            count = await batches_repo.unpause_by_reason(s, "batch-cap")
            await s.commit()

        # Exactly 1 row should have been unpaused (batch B only)
        assert count == 1, (
            f"unpause_by_reason('batch-cap') should unpause exactly 1 row (batch B); got {count}"
        )

        # batch A must STILL be paused
        async with SessionLocal() as s:
            a = await s.get(Batch, batch_a_id)
            assert a.paused_at is not None, (
                "batch A (reason='manual') must remain paused after unpause_by_reason('batch-cap')"
            )
            assert a.paused_reason == "manual", (
                f"batch A paused_reason must still be 'manual', got {a.paused_reason!r}"
            )

        # batch B must be unpaused
        async with SessionLocal() as s:
            b = await s.get(Batch, batch_b_id)
            assert b.paused_at is None, (
                "batch B (reason='batch-cap') must be unpaused after unpause_by_reason('batch-cap')"
            )
            assert b.paused_reason is None, (
                f"batch B paused_reason must be None, got {b.paused_reason!r}"
            )

    finally:
        await _cleanup_book(book_a_id)
        await _cleanup_book(book_b_id)
