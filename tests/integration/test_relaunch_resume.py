"""Real-DB: a no-force relaunch over a cancelled-with-saved-phases section
RESUMES it (reuses the job row + done phase) instead of creating a fresh job
that discards the saved output. RUN_DB_INTEGRATION=1 (fleet-relaunch-dataloss-1)."""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1", reason="needs Postgres")

_HDR = {"Authorization": "Bearer 123"}


async def _seed_cancelled_with_phase():
    """Seed a toc_ready book + 1 TOC entry + Batch (transport=cli) + a
    cancelled HomeworkJob (batch_id set) + a done PhaseOutput with non-empty
    output_md. Returns (book_id, batch_id, toc_id, old_job_id)."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="x.pdf",
            content_sha256="b2" * 32,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()

        toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(toc)
        await s.flush()

        # Batch must exist with the same (book_id, transport) the relaunch will use
        batch = Batch(
            book_id=book.id,
            subject="math-algebra",
            grade="9",
            provider="claude",
            transport="cli",
        )
        s.add(batch)
        await s.flush()

        job = HomeworkJob(
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math-algebra",
            provider="claude",
            status="cancelled",
            batch_id=batch.id,
            transport="cli",
        )
        s.add(job)
        await s.flush()

        # A done phase with real output — this is the "saved work" we must NOT discard
        po = PhaseOutput(
            job_id=job.id,
            phase_name="flashcards",
            phase_order=1,
            prompt_hash="a" * 64,      # non-nullable
            model_name="claude-3-5-sonnet",  # non-nullable
            status="done",
            output_md="SAVED PHASE OUTPUT — must not be discarded",
        )
        s.add(po)
        await s.commit()

        return book.id, batch.id, toc.id, job.id


async def _cleanup(book_id):
    """Delete phases -> jobs -> batch -> toc -> book for the book_id."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    import sqlalchemy
    async with SessionLocal() as s:
        await s.execute(
            delete(PhaseOutput).where(
                PhaseOutput.job_id.in_(
                    sqlalchemy.select(HomeworkJob.id).where(
                        HomeworkJob.book_id == book_id))))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_relaunch_resumes_saved_section():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    import main
    book_id, batch_id, toc_id, old_job_id = await _seed_cancelled_with_phase()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://t"
        ) as ac:
            r = await ac.post("/api/v1/jobs/batch", headers=_HDR, json={
                "book_id": str(book_id), "transport": "cli"})
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["jobs_resumed"] == 1, f"Expected jobs_resumed=1, got {body}"
        assert body["jobs_created"] == 0, f"Expected jobs_created=0, got {body}"
        async with SessionLocal() as s:
            jobs = (await s.execute(select(HomeworkJob).where(
                HomeworkJob.toc_entry_id == toc_id))).scalars().all()
            assert len(jobs) == 1, f"Expected 1 job (reused row), got {len(jobs)}"
            assert jobs[0].id == old_job_id, "Expected the SAME job row to be reused"
            assert jobs[0].status == "pending", f"Expected pending, got {jobs[0].status}"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_relaunch_preview_reports_saved_count_without_mutating():
    import main
    book_id, batch_id, toc_id, old_job_id = await _seed_cancelled_with_phase()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://t"
        ) as ac:
            r = await ac.post("/api/v1/jobs/batch", headers=_HDR, json={
                "book_id": str(book_id), "transport": "cli", "preview": True})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["resumable"] == 1, f"Expected resumable=1, got {body}"
        # Strict zero-write: the job must still be cancelled, no batch mutation
        from app.db import SessionLocal
        from app.repositories import jobs as jobs_repo
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, old_job_id) == "cancelled"
    finally:
        await _cleanup(book_id)
