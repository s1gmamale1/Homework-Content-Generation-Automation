"""`stagger-backpressure-test-1`: queue_depth must count only the wave that is
DUE, never the jobs a batch launch deliberately pushed into the future.

Why this exists (gate finding on #132 / worklog 0172). The batch-launch wave
stagger stamps later waves with a future `scheduled_at` so the claim gate holds
them back. Its safety property — a staggered launch must NOT trip `/generate`'s
`queue_backpressure_limit` 503 with its own not-yet-due jobs — rests entirely on
`queue_depth`'s `scheduled_at <= func.now()` filter (`app/repositories/jobs.py`).

That filter is PRE-EXISTING: 0172 added no code for it, it simply relied on a
property that was already true. So nothing in the suite asserted it. If someone
later relaxed `queue_depth`, a big launch would silently start 503-ing on its own
waves and no test would fail. This pins it.

Mirrors the shape of `test_queue_depth_pause.py`, the sibling that pins the other
`queue_depth` predicate (the batch-pause arm).
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
from app.services.launch_stagger import stagger_offset

WAVE_SIZE = 2
INTERVAL = 3600  # an hour: far enough out that no test run can drift into it


async def _seed_staggered(s, *, lessons=6):
    """One book + `lessons` TOC entries, launched as a batch WOULD launch them:
    offsets straight from the shipped `stagger_offset`, so this test tracks the
    real rule rather than a hand-copied one.

    Returns (base_depth, wave0_count). `base_depth` is queue_depth BEFORE the
    seed — a shared scratch DB may already carry rows from other tests.
    """
    from app.models.toc_entry import TOCEntry

    base_depth = await jobs_repo.queue_depth(s)

    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8-stagger-depth.pdf", content_sha256="e" * 64,
        file_size_bytes=1, source_language="uz",
    )
    entries = [
        TOCEntry(book_id=book.id, section_number=str(i + 1),
                 section_title=f"L{i + 1}", order_index=i)
        for i in range(lessons)
    ]
    s.add_all(entries)
    await s.flush()

    batch = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-3.6-flash", transport="api",
        output_language="uz",
    )
    wave0 = 0
    for i, entry in enumerate(entries):
        offset = stagger_offset(i, wave_size=WAVE_SIZE, interval_seconds=INTERVAL)
        if offset == 0:
            wave0 += 1
        await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=entry.id,
            subject="geometriya-g7-11", output_language="uz",
            provider="gemini", model="gemini-3.6-flash",
            transport="api", batch_id=batch.id,
            start_offset_seconds=offset)
    await s.commit()
    return base_depth, wave0


@pytest.mark.asyncio
async def test_queue_depth_counts_only_the_due_wave():
    """6 lessons at wave_size 2 → 2 due now, 4 stamped into the future.

    queue_depth must see ONLY the 2. If it counted all 6, a whole-book launch
    would burn the operator's `queue_backpressure_limit` on jobs that are not
    even eligible to run yet, and unrelated `/generate` calls would 503.
    """
    async with SessionLocal() as s:
        base, wave0 = await _seed_staggered(s)
        assert wave0 == WAVE_SIZE, f"seed built {wave0} due jobs, expected {WAVE_SIZE}"

        depth = await jobs_repo.queue_depth(s)

    # RED-provable: drop the `scheduled_at <= func.now()` filter from
    # queue_depth and this reads base + 6 instead of base + 2.
    assert depth == base + WAVE_SIZE, (
        f"queue_depth counted {depth - base} of 6 staggered jobs; only the "
        f"{WAVE_SIZE} due ones may count")


@pytest.mark.asyncio
async def test_a_due_job_is_still_counted():
    """The other direction, so the test above cannot be satisfied by a
    queue_depth that simply counts nothing. An unstaggered job (offset 0) must
    still land in the backpressure count — the 503 guard has to keep working."""
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        base = await jobs_repo.queue_depth(s)
        book = await books_repo.create(
            s, subject="geometriya-g7-11", grade="8",
            original_filename="g8-due.pdf", content_sha256="f" * 64,
            file_size_bytes=1, source_language="uz",
        )
        entry = TOCEntry(book_id=book.id, section_number="1",
                         section_title="L1", order_index=0)
        s.add(entry)
        await s.flush()
        await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=entry.id,
            subject="geometriya-g7-11", output_language="uz",
            provider="gemini", model="gemini-3.6-flash",
            transport="api", start_offset_seconds=0)
        await s.commit()

        assert await jobs_repo.queue_depth(s) == base + 1


@pytest.mark.asyncio
async def test_a_future_wave_becomes_countable_once_it_is_due():
    """The filter is a moving boundary, not a permanent exclusion: a job stamped
    into the past is counted, which is what makes a wave 'fall due' rather than
    vanish. Uses a NEGATIVE offset written directly, since `create` only accepts
    forward offsets — the point is the comparison, not how the row got there."""
    from sqlalchemy import text

    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        base = await jobs_repo.queue_depth(s)
        book = await books_repo.create(
            s, subject="geometriya-g7-11", grade="8",
            original_filename="g8-elapsed.pdf", content_sha256="a" * 64,
            file_size_bytes=1, source_language="uz",
        )
        entry = TOCEntry(book_id=book.id, section_number="1",
                         section_title="L1", order_index=0)
        s.add(entry)
        await s.flush()
        job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=entry.id,
            subject="geometriya-g7-11", output_language="uz",
            provider="gemini", model="gemini-3.6-flash",
            transport="api", start_offset_seconds=INTERVAL)
        await s.commit()

        # not due yet
        assert await jobs_repo.queue_depth(s) == base

        # rewind its stamp past NOW() — the same transition a real wave makes
        await s.execute(text(
            "update homework_jobs set scheduled_at = now() - interval '1 minute'"
            " where id = :i"), {"i": job.id})
        await s.commit()

        assert await jobs_repo.queue_depth(s) == base + 1
