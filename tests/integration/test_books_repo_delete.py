"""Real-DB: books_repo.delete must remove EVERY book-scoped row in one
transaction, including `batches` (BE-02 task 1 — the audit's reproduced 500:
`batches.book_id` has no ondelete, so deleting jobs+book without deleting the
batch first raises IntegrityError on batches_book_id_fkey). RUN_DB_INTEGRATION=1.

`agent_usages` FKs (book_id/homework_job_id/phase_output_id) are all
ondelete=SET NULL — those rows must SURVIVE the delete with their FKs nulled,
proving billing/audit history is retained rather than cascaded away."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_batch_and_job(s, *, transport="cli", output_language="uz"):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256=os.urandom(32).hex(), file_size_bytes=1,
                status="toc_ready")
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
    s.add(toc)
    await s.flush()
    batch = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="math-algebra", grade=None,
        provider="claude", model=None, transport=transport,
        output_language=output_language)
    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        batch_id=batch.id, output_language=output_language, transport=transport,
        status="done")
    return book, toc, batch, job


@pytest.mark.asyncio
async def test_delete_with_batch_does_not_raise_integrity_error():
    """Regression guard for the audit's exact repro (BE-02 task 1): a book with
    a batch (and a done job stamped to it) used to raise IntegrityError on
    batches_book_id_fkey, because books_repo.delete never touched batches —
    confirmed as the RED state (see task-1-report.md) before this fix landed.
    delete() must now succeed instead of raising."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        book, toc, batch, job = await _seed_book_with_batch_and_job(s)
        await s.commit()
        book_id = book.id
    try:
        async with SessionLocal() as s:
            ok = await books_repo.delete(s, book_id)
            await s.commit()
        assert ok is True
    finally:
        # Cleanup tolerates either outcome (belt-and-suspenders if a future
        # regression reintroduces the IntegrityError and the delete above
        # raises instead of returning).
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_delete_removes_all_book_scoped_rows_and_retains_usage_nulled():
    """GREEN target: delete succeeds; books/batches/homework_jobs/phase_outputs/
    toc_entries rows for the book are all gone. An agent_usages row seeded
    against the job SURVIVES the delete with book_id/homework_job_id NULLed
    (ondelete=SET NULL) — billing/audit history is retained, not cascaded away."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import agent_usage as usage_repo
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        book, toc, batch, job = await _seed_book_with_batch_and_job(s)
        phase = PhaseOutput(
            job_id=job.id, phase_name="preview", phase_order=0,
            prompt_hash="test:sha256:" + ("0" * 64), model_name="claude-opus-4",
            status="done")
        s.add(phase)
        await s.flush()
        usage = await usage_repo.create(
            s, operation="phase.run", book_id=book.id, homework_job_id=job.id,
            phase_output_id=phase.id, provider="claude", auth_mode="api",
            prompt_tokens=10, output_tokens=5, total_tokens=15)
        await s.commit()
        book_id, batch_id, job_id, phase_id, usage_id = (
            book.id, batch.id, job.id, phase.id, usage.id)

    async with SessionLocal() as s:
        ok = await books_repo.delete(s, book_id)
        await s.commit()
    assert ok is True

    async with SessionLocal() as s:
        assert await s.get(Book, book_id) is None
        assert await s.get(Batch, batch_id) is None
        assert await s.get(HomeworkJob, job_id) is None
        assert await s.get(PhaseOutput, phase_id) is None
        assert (
            await s.execute(select(TOCEntry).where(TOCEntry.book_id == book_id))
        ).scalar_one_or_none() is None

        from app.models.agent_usage import AgentUsage
        row = await s.get(AgentUsage, usage_id)
        assert row is not None, "agent_usages row must SURVIVE the book delete"
        assert row.book_id is None
        assert row.homework_job_id is None
        assert row.phase_output_id is None


@pytest.mark.asyncio
async def test_delete_with_two_batches_uz_and_ru_both_gone():
    """A book with TWO batches forked on output_language (uz + ru, same
    transport) — both batches, and both batches' jobs, are gone after delete."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        _b1, _t1, batch_uz, job_uz = await _seed_book_with_batch_and_job(
            s, transport="cli", output_language="uz")
        book_id = _b1.id
        # Second batch on the SAME book forked by output_language="ru".
        from app.models.toc_entry import TOCEntry as _TOC
        from app.repositories import batches as batches_repo
        from app.repositories import jobs as jobs_repo

        toc2 = _TOC(book_id=book_id, section_title="L1", order_index=1)
        s.add(toc2)
        await s.flush()
        batch_ru = await batches_repo.get_or_create_for_book(
            s, book_id=book_id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="ru")
        job_ru = await jobs_repo.create(
            s, book_id=book_id, toc_entry_id=toc2.id, subject="math-algebra",
            batch_id=batch_ru.id, output_language="ru", transport="cli",
            status="done")
        await s.commit()
        batch_uz_id, batch_ru_id = batch_uz.id, batch_ru.id
        job_uz_id, job_ru_id = job_uz.id, job_ru.id

    assert batch_uz_id != batch_ru_id, "uz/ru must fork distinct batches"

    async with SessionLocal() as s:
        ok = await books_repo.delete(s, book_id)
        await s.commit()
    assert ok is True

    async with SessionLocal() as s:
        assert await s.get(Batch, batch_uz_id) is None
        assert await s.get(Batch, batch_ru_id) is None
        assert await s.get(HomeworkJob, job_uz_id) is None
        assert await s.get(HomeworkJob, job_ru_id) is None
        assert (
            await s.execute(select(Batch).where(Batch.book_id == book_id))
        ).scalar_one_or_none() is None
        assert (
            await s.execute(select(TOCEntry).where(TOCEntry.book_id == book_id))
        ).scalar_one_or_none() is None
