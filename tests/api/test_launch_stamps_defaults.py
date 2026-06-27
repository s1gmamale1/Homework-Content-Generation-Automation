"""Real-DB: launch endpoints stamp concrete judge/extract provider+model onto job rows.

Covers:
  (a) judge Auto → job row stamped gemini/gemini-2.5-flash (from seeded singleton).
  (b) judge_provider=claude (model Auto) → job row stamped claude/claude-sonnet-4-6.
  (c) After updating the singleton, an EARLIER job's row is UNCHANGED
      (future-launches-only). RED-proven: the old job's stamped value must NOT change.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _seed_book(sha_char: str):
    """Seed a toc_ready book with one lesson; return (book_id, toc_entry_id).

    sha_char must be a SINGLE character — it's repeated 64 times to fill the
    content_sha256 VARCHAR(64) column (same pattern as test_transport_validation.py).
    """
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha_char * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()
        await s.commit()
        return book.id, t.id


async def _cleanup(book_id):
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


async def _get_job(book_id):
    """Fetch the first job for a book from DB."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        result = await s.execute(
            select(HomeworkJob).where(HomeworkJob.book_id == book_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_judge_auto_stamps_gemini_default():
    """(a) Launch with judge=Auto → job row stamped gemini/gemini-2.5-flash."""
    book_id, sid = await _seed_book("P")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude"},  # judge_provider omitted = Auto
            )
        assert r.status_code in (200, 201), r.text
        job = await _get_job(book_id)
        assert job is not None
        assert job.judge_provider == "gemini", f"expected gemini, got {job.judge_provider!r}"
        assert job.judge_model == "gemini-2.5-flash", f"expected gemini-2.5-flash, got {job.judge_model!r}"
        assert job.extract_provider == "gemini", f"expected gemini, got {job.extract_provider!r}"
        assert job.extract_model == "gemini-2.5-flash", f"expected gemini-2.5-flash, got {job.extract_model!r}"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_explicit_judge_provider_uses_that_providers_default_model():
    """(b) judge_provider=claude, model Auto → job row stamped claude/claude-sonnet-4-6."""
    book_id, sid = await _seed_book("F")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude", "judge_provider": "claude"},  # judge model omitted = Auto
            )
        assert r.status_code in (200, 201), r.text
        job = await _get_job(book_id)
        assert job is not None
        assert job.judge_provider == "claude", f"expected claude, got {job.judge_provider!r}"
        assert job.judge_model == "claude-sonnet-4-6", (
            f"expected claude-sonnet-4-6 (claude's default), got {job.judge_model!r}"
        )
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_earlier_job_unchanged_after_singleton_update():
    """(c) After updating the singleton, an EARLIER job's stamped values are UNCHANGED.

    RED-proof: if the implementation re-reads the singleton at pipeline-run time
    (instead of stamping at launch), the earlier job would pick up the new value
    and this assertion would FAIL. The stamp is an immutable launch-time snapshot.
    """
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    book_id_before, sid_before = await _seed_book("G")
    try:
        # Launch the first job under the seeded default (gemini/gemini-2.5-flash).
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id_before}/sections/{sid_before}/generate",
                headers=_HDR,
                json={"provider": "claude"},
            )
        assert r.status_code in (200, 201), r.text

        # Capture the stamp on the first job.
        job_before = await _get_job(book_id_before)
        assert job_before is not None
        stamped_provider = job_before.judge_provider
        stamped_model = job_before.judge_model

        # Now update the singleton to a DIFFERENT default.
        async with SessionLocal() as s:
            await launch_defaults_repo.update(
                s, {"judge_provider": "claude", "judge_model": "claude-opus-4-7"})
            await s.commit()

        # Re-fetch the OLDER job — its stamp must be UNCHANGED.
        job_after = await _get_job(book_id_before)
        assert job_after is not None
        assert job_after.judge_provider == stamped_provider, (
            f"earlier job's judge_provider changed from {stamped_provider!r} "
            f"to {job_after.judge_provider!r} after singleton update — "
            "stamp is NOT future-launches-only!"
        )
        assert job_after.judge_model == stamped_model, (
            f"earlier job's judge_model changed from {stamped_model!r} "
            f"to {job_after.judge_model!r} after singleton update — "
            "stamp is NOT future-launches-only!"
        )

        # The stamped values must be from the OLD default (gemini/gemini-2.5-flash).
        assert stamped_provider == "gemini", (
            f"first job should have been stamped with gemini default, got {stamped_provider!r}"
        )
        assert stamped_model == "gemini-2.5-flash", (
            f"first job should have been stamped with gemini-2.5-flash, got {stamped_model!r}"
        )
    finally:
        # Restore the singleton default so other tests aren't affected.
        async with SessionLocal() as s:
            await launch_defaults_repo.update(
                s, {"judge_provider": "gemini", "judge_model": "gemini-2.5-flash"})
            await s.commit()
        await _cleanup(book_id_before)
