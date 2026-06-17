"""Real-DB: cross-job extract reuse is keyed on (toc_entry_id, prompt_hash,
provider, model). A different provider/model must NOT match (else a
gemini-produced extract could be served to a claude job); a legacy
provider IS NULL row is a safe miss. RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_PROMPT_HASH = "builtin:extract:v2"


async def _seed_book_with_lesson(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(subject="math-algebra", original_filename="r.pdf",
                content_sha256="6" * 64, file_size_bytes=1, status="toc_ready")
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _insert_done_extract(s, *, book_id, toc_entry_id, provider, model_name):
    from datetime import datetime, timezone

    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book_id, toc_entry_id=toc_entry_id, subject="math-algebra",
    )
    await s.flush()
    po = PhaseOutput(
        job_id=job.id,
        phase_name="extract",
        phase_order=0,
        prompt_hash=_PROMPT_HASH,
        model_name=model_name,
        provider=provider,
        output_md="x",
        status="done",
        completed_at=datetime.now(timezone.utc),
    )
    s.add(po)
    await s.flush()
    return job, po


@pytest.mark.asyncio
async def test_reuse_matches_only_same_provider_model():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import phase_outputs as phase_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s)
        book_id, toc_id = book.id, toc.id
        try:
            _, po = await _insert_done_extract(
                s, book_id=book_id, toc_entry_id=toc_id,
                provider="gemini", model_name="gemini-2.5-flash",
            )
            await s.commit()

            hit = await phase_repo.find_latest_extract(
                s, toc_entry_id=toc_id, prompt_hash=_PROMPT_HASH,
                provider="gemini", model="gemini-2.5-flash",
            )
            assert hit is not None and hit.id == po.id

            miss = await phase_repo.find_latest_extract(
                s, toc_entry_id=toc_id, prompt_hash=_PROMPT_HASH,
                provider="claude", model="claude-opus-4-7",
            )
            assert miss is None
        finally:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.toc_entry_id == toc_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_legacy_null_provider_row_is_a_safe_miss():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import phase_outputs as phase_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s)
        book_id, toc_id = book.id, toc.id
        try:
            await _insert_done_extract(
                s, book_id=book_id, toc_entry_id=toc_id,
                provider=None, model_name="gemini-2.5-flash",
            )
            await s.commit()

            miss = await phase_repo.find_latest_extract(
                s, toc_entry_id=toc_id, prompt_hash=_PROMPT_HASH,
                provider="gemini", model="gemini-2.5-flash",
            )
            assert miss is None
        finally:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.toc_entry_id == toc_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
