"""Real-DB regression: pipeline.run() must load _ld before its first use.

Pre-fix, `_ld` (the launch_defaults DB row) was referenced at:
    judge_provider_ov = getattr(job, "judge_provider", None) or _ld.judge_provider
    judge_model_ov    = getattr(job, "judge_model", None)    or _ld.judge_model
    extract_provider, extract_model = _resolve_extract(..., _ld)
...BEFORE the `_ld = await _ld_repo.get(session)` assignment appeared in the
code (it had been placed after the `_batch` load, several lines below first use).
This caused every job to crash immediately with:
    UnboundLocalError: local variable '_ld' referenced before assignment

The fix moved the assignment to right after `selected_phases`, before first use.

This test verifies: pipeline.run() must pass the context-load block (which reads
_ld from DB) and fail at the PDF-fetch step with a "Book PDF missing on disk"
error — NOT with an UnboundLocalError on `_ld`.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_fixture():
    """Create a toc_ready book + toc_entry + pending job with NULL judge/extract
    provider columns. No PDF on disk. Returns (book_id, toc_id, job_id)."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="ld_regression.pdf",
            content_sha256="7" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L0 — ld ordering", order_index=0)
        s.add(toc)
        await s.flush()
        # Deliberately omit judge_provider / judge_model / extract_provider / extract_model
        # so pipeline.run() MUST fall back to the _ld singleton for those fields.
        # Pre-fix: accessing _ld before it was assigned raised UnboundLocalError.
        job = await jobs_repo.create(
            s,
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math-algebra",
            provider="claude",
        )
        await s.commit()
        return book.id, toc.id, job.id


async def _cleanup(book_id) -> None:
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_pipeline_run_loads_ld_before_first_use():
    """pipeline.run() must read _ld from DB before using it.

    Bite: with the pre-fix ordering (use before define), this test fails because
    job.error_message contains 'referenced before assignment' instead of the
    expected PDF-missing error. With the fix, the job fails at PDF-fetch step.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.services import pipeline

    book_id, _toc_id, job_id = await _seed_fixture()
    try:
        # pipeline.run():
        #   1. Loads job context (including _ld from launch_defaults singleton).
        #   2. Calls ensure_book_pdf_sync() — fails because no PDF on disk
        #      and fleet_head_url is empty in test env.
        #   3. Outer except sets job status='failed', error_message=str(exc).
        #
        # Pre-fix: crashed at step 1 with UnboundLocalError on _ld before step 2.
        await pipeline.run(job_id)

        async with SessionLocal() as s:
            result = await s.execute(
                select(HomeworkJob).where(HomeworkJob.id == job_id)
            )
            job = result.scalars().first()

        assert job is not None, "job row disappeared unexpectedly"
        assert job.status == "failed", (
            f"expected status='failed', got {job.status!r}; "
            f"error_message={job.error_message!r}"
        )

        err = job.error_message or ""

        # MUST NOT be the _ld UnboundLocalError (the pre-fix failure mode).
        assert "_ld" not in err, (
            f"error_message contains '_ld': {err!r} — "
            "the UnboundLocalError bug may still be present"
        )
        assert "referenced before assignment" not in err, (
            f"error_message suggests an UnboundLocalError: {err!r}"
        )
        assert "UnboundLocalError" not in err, (
            f"error_message contains 'UnboundLocalError': {err!r}"
        )

        # MUST be a PDF/fetch/source error — proves run() got past the _ld load.
        # Depending on whether fleet_head_url is set, ensure_book_pdf_sync either
        # raises RuntimeError("Book PDF missing on disk: <path>") or tries a remote
        # fetch and raises RuntimeError("fetch from head failed: <reason>").
        # Both are legitimate evidence that run() loaded _ld successfully.
        assert any(
            kw in err.lower()
            for kw in ("pdf", "missing", "source", "fetch", "head", "http", "file")
        ), (
            f"expected a PDF/fetch/source error (evidence that _ld was loaded OK) "
            f"but got: {err!r}"
        )
    finally:
        await _cleanup(book_id)
