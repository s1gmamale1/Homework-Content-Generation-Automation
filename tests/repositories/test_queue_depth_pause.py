"""queue-backpressure-paused-1: queue_depth must not count a paused batch's
dormant pending jobs (they are unclaimable by design — the claim gate's
batch-pause predicate skips them), else a paused batch fills the
`queue_backpressure_limit` and /generate 503s on unrelated enqueues
(hit live 2026-07-03: 57 paused RU jobs blocked a 1-job enqueue).

Mirrors the claim gate's semantics exactly, including the critical batchless
arm: a job with batch_id IS NULL is never governed by the pause gate.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from app.db import SessionLocal
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo


async def _seed(s):
    """Book + 3 TOC entries; batch A (cli) + batch B (api) each with one
    pending job; plus one batchless pending job. Returns (batch_a, base_depth)
    where base_depth is queue_depth BEFORE this seed ran (the scratch DB may
    carry rows from other tests in the same run)."""
    from app.models.toc_entry import TOCEntry

    base_depth = await jobs_repo.queue_depth(s)

    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8-depth.pdf", content_sha256="d" * 64,
        file_size_bytes=1, source_language="uz",
    )
    e1 = TOCEntry(book_id=book.id, section_number="1", section_title="L1", order_index=0)
    e2 = TOCEntry(book_id=book.id, section_number="2", section_title="L2", order_index=1)
    e3 = TOCEntry(book_id=book.id, section_number="3", section_title="L3", order_index=2)
    s.add_all([e1, e2, e3])
    await s.flush()

    # UNIQUE(book_id, transport) → same book, two transports = two batches.
    batch_a = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-2.5-pro", transport="cli",
        output_language="uz",
    )
    batch_b = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-2.5-pro", transport="api",
        output_language="uz",
    )
    await jobs_repo.create(s, book_id=book.id, toc_entry_id=e1.id,
                           subject="geometriya-g7-11", output_language="uz",
                           provider="gemini", model="gemini-2.5-pro",
                           transport="cli", batch_id=batch_a.id)
    await jobs_repo.create(s, book_id=book.id, toc_entry_id=e2.id,
                           subject="geometriya-g7-11", output_language="uz",
                           provider="gemini", model="gemini-2.5-pro",
                           transport="api", batch_id=batch_b.id)
    await jobs_repo.create(s, book_id=book.id, toc_entry_id=e3.id,
                           subject="geometriya-g7-11", output_language="uz",
                           provider="gemini", model="gemini-2.5-pro",
                           transport="cli", batch_id=None)   # batchless /generate job
    await s.commit()
    return batch_a, base_depth


@pytest.mark.asyncio
async def test_queue_depth_excludes_paused_batch_jobs():
    """Pause batch A → its pending job vanishes from queue_depth while the
    unpaused batch's job AND the batchless job stay counted (the claim gate's
    batchless IS-NULL arm). Unpause → all 3 counted again (RED-provable both
    ways: dropping the pause filter fails the paused assert; a naive INNER
    JOIN fails the batchless assert)."""
    async with SessionLocal() as s:
        batch_a, base = await _seed(s)

        # all 3 seeded jobs eligible before any pause
        assert await jobs_repo.queue_depth(s) == base + 3

        await batches_repo.pause_batch(s, batch_a.id, "manual")
        await s.commit()
        # paused batch's job is dormant → excluded; unpaused + batchless remain
        assert await jobs_repo.queue_depth(s) == base + 2

        await batches_repo.unpause_batch(s, batch_a.id)
        await s.commit()
        assert await jobs_repo.queue_depth(s) == base + 3
