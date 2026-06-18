"""Real-DB: phase_repo.set_status freezes a `done` phase so a late cancel-race
write can't corrupt the resumable set. RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_phase(status: str, output_md: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="e" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(toc); await s.flush()
        job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                          subject="math-algebra", provider="claude", status="running")
        s.add(job); await s.flush()
        # NOTE: PhaseOutput requires non-nullable prompt_hash and model_name —
        # the plan's seed omits them; we supply stub values here to match the model.
        po = PhaseOutput(
            job_id=job.id,
            phase_name="flashcards",
            phase_order=1,
            prompt_hash="a" * 64,   # non-nullable — stub value
            model_name="claude-3-5-sonnet",  # non-nullable — stub value
            status=status,
            output_md=output_md,
        )
        s.add(po); await s.commit()
        return book.id, po.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    async with SessionLocal() as s:
        await s.execute(delete(PhaseOutput).where(
            PhaseOutput.job_id.in_(
                __import__("sqlalchemy").select(HomeworkJob.id).where(
                    HomeworkJob.book_id == book_id))))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_done_phase_is_frozen():
    from app.db import SessionLocal
    from app.repositories import phase_outputs as phase_repo
    book_id, po_id = await _seed_phase("done", "REAL OUTPUT")
    try:
        async with SessionLocal() as s:
            changed = await phase_repo.set_status(s, po_id, "running")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            rows = await phase_repo.list_for_job(
                s, (await s.get(__import__("app.models.phase_output",
                    fromlist=["PhaseOutput"]).PhaseOutput, po_id)).job_id)
        assert [r for r in rows if r.id == po_id][0].status == "done"
        assert [r for r in rows if r.id == po_id][0].output_md == "REAL OUTPUT"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_running_phase_can_advance_to_done():
    from app.db import SessionLocal
    from app.repositories import phase_outputs as phase_repo
    book_id, po_id = await _seed_phase("running", "")
    try:
        async with SessionLocal() as s:
            changed = await phase_repo.set_status(s, po_id, "done", output_md="X")
            await s.commit()
        assert changed is True
    finally:
        await _cleanup(book_id)
