"""Real-DB: get_or_create_for_book is idempotent per (book, transport) (race-safe),
and the rollup is per-lesson-latest (a retried lesson counts once). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lessons(s, n=3):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256="3" * 64, file_size_bytes=1, status="toc_ready")
    s.add(book)
    await s.flush()
    tocs = []
    for i in range(n):
        t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
        s.add(t)
        tocs.append(t)
    await s.flush()
    return book, tocs


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_per_book_transport():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lessons(s)
        await s.commit()
        book_id = book.id
    try:
        # Same transport ("cli") twice → SAME batch id.
        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None, transport="cli", output_language="uz")
            await s.commit()
            b1_id = b1.id
        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="gemini", model=None, transport="cli", output_language="uz")
            await s.commit()
            b2_id = b2.id
        assert b1_id == b2_id, "same (book, transport) must return the SAME batch"

        # Different transport ("api") → a DISTINCT batch id (forks).
        async with SessionLocal() as s:
            b3 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model="claude-opus-4-8", transport="api",
                output_language="uz")
            await s.commit()
            b3_id = b3.id
        assert b3_id != b1_id, "different transport must fork a new batch"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_rollup_is_per_lesson_latest():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="uz")
        for t in tocs:
            await jobs_repo.create(s, book_id=book.id, toc_entry_id=t.id,
                                   subject="math-algebra", batch_id=batch.id,
                                   output_language="uz")
        await s.commit()
        book_id, batch_id, first_id = book.id, batch.id, tocs[0].id
    try:
        # Lesson 0: simulate a failed-then-retried lesson -> a SECOND (newer) job.
        async with SessionLocal() as s:
            old_id = (await s.execute(
                select(HomeworkJob.id).where(HomeworkJob.toc_entry_id == first_id))
            ).scalar_one()
            old = await s.get(HomeworkJob, old_id)
            old.status = "failed"
            await s.commit()
        async with SessionLocal() as s:
            await jobs_repo.create(s, book_id=book_id, toc_entry_id=first_id,
                                   subject="math-algebra", batch_id=batch_id,
                                   output_language="uz")  # newer pending
            await s.commit()
        async with SessionLocal() as s:
            tally = await batches_repo.rollup_for_batch(s, batch_id)
        # Per-lesson-latest: lesson0's latest is the newer pending -> 3 pending,
        # 0 failed -> reconciles to 3 lessons, NOT 4 jobs.
        assert sum(tally.values()) == 3, f"denominator must be 3 lessons, got {tally}"
        assert tally.get("pending") == 3
        assert tally.get("failed", 0) == 0
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_list_jobs_includes_unlaunched_lessons():
    """list_jobs returns one row per book lesson (full TOC), launched lessons
    carry status, un-launched lessons come back with job_id/status None — while
    rollup_for_batch tallies the LAUNCHED lessons only (BE-03: the denominator
    is the launch scope, not the whole book)."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="uz")
        # Launch ONLY lessons 0 and 1; lesson 2 stays un-launched.
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[0].id,
                               subject="math-algebra", batch_id=batch.id,
                               output_language="uz")
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[1].id,
                               subject="math-algebra", batch_id=batch.id,
                               output_language="uz")
        await s.commit()
        book_id, batch_id, third_toc = book.id, batch.id, tocs[2].id
    try:
        async with SessionLocal() as s:
            rows = await batches_repo.list_jobs(s, batch_id)
            tally = await batches_repo.rollup_for_batch(s, batch_id)

        # All three lessons present, ordered by order_index.
        assert [r["order_index"] for r in rows] == [0, 1, 2]
        # Launched lessons carry a job + status.
        assert rows[0]["job_id"] is not None and rows[0]["status"] == "pending"
        assert rows[1]["job_id"] is not None and rows[1]["status"] == "pending"
        # Un-launched lesson: present, but no job/status.
        third = next(r for r in rows if r["toc_entry_id"] == str(third_toc))
        assert third["job_id"] is None
        assert third["status"] is None
        assert third["section_title"] == "L2"
        # Rollup is launched-only: 2 launched (pending), no not_started key.
        assert tally.get("pending") == 2, f"expected 2 launched pending, got {tally}"
        assert "not_started" not in tally
        assert sum(tally.values()) == 2, f"denominator must be launch scope (2), got {tally}"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_rollup_partial_launch_has_no_not_started():
    """rollup_for_batch tallies the batch's LAUNCHED lessons only — a partial
    (lesson-only) launch must be able to read as complete (BE-03): 3 toc rows,
    2 launched (one done, one running) -> tally sums to 2, never 3."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="uz")
        # Launch ONLY lessons 0 and 1; lesson 2 stays un-launched.
        j0 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[0].id,
                                    subject="math-algebra", batch_id=batch.id,
                                    output_language="uz")
        j1 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[1].id,
                                    subject="math-algebra", batch_id=batch.id,
                                    output_language="uz")
        await s.commit()
        book_id, batch_id, j0_id, j1_id = book.id, batch.id, j0.id, j1.id
    try:
        async with SessionLocal() as s:
            j0_row = await s.get(HomeworkJob, j0_id)
            j0_row.status = "done"
            j1_row = await s.get(HomeworkJob, j1_id)
            j1_row.status = "running"
            await s.commit()
        async with SessionLocal() as s:
            tally = await batches_repo.rollup_for_batch(s, batch_id)
        assert tally == {"done": 1, "running": 1}
        assert "not_started" not in tally
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_toc_total_for_batch_counts_whole_book():
    """toc_total_for_batch is display-only whole-book context, separate from
    the rollup's launched-lesson denominator."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="uz")
        await s.commit()
        book_id, batch_id = book.id, batch.id
    try:
        async with SessionLocal() as s:
            total = await batches_repo.toc_total_for_batch(s, batch_id)
        assert total == 3
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_relaunch_without_prompts_preserves_stored():
    """A plain same-transport re-launch/top-up (no custom prompts) must NOT NULL
    out the batch's stored custom_prompts/selected_phases (the ON-CONFLICT bug).
    COALESCE keeps the existing provenance when the incoming value is NULL."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lessons(s)
        await s.commit()
        book_id = book.id
    try:
        # First launch carries custom prompts + a phase subset.
        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None, transport="api", output_language="uz",
                custom_prompts={"reading": "x"}, selected_phases=["reading"])
            await s.commit()
            b1_id = b1.id
        # Plain same-transport re-launch: no custom prompts passed.
        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None, transport="api", output_language="uz")
            await s.commit()
            assert b2.id == b1_id, "same (book, transport) must reuse the batch"
            assert b2.custom_prompts == {"reading": "x"}, "custom_prompts must NOT be nulled"
            assert b2.selected_phases == ["reading"], "selected_phases must NOT be nulled"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
