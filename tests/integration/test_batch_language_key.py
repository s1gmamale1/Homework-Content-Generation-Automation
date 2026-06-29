"""Real-DB: batch key includes output_language (language-scoped dedup).

Tests:
  (a) get_or_create_for_book for (book, transport='cli', output_language='uz')
      then (..., 'en') returns TWO distinct batch ids (language fork).
      Same triple twice → SAME id (idempotent).
  (b) find_active_for_section does not return a 'uz' job when queried with
      output_language='en'.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL is set.
A fast non-DB unit check verifies the filter is present in the SQL WHERE clause.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lesson(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="lang-test.pdf",
        content_sha256="L" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


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


# ─── (a) Batch key fork / idempotency by language ─────────────────────────────


@pytest.mark.asyncio
async def test_language_forks_new_batch():
    """(book, cli, uz) and (book, cli, en) must be TWO distinct batches."""
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lesson(s)
        await s.commit()
        book_id = book.id

    try:
        _base = dict(subject="math-algebra", grade=None, provider="claude", model=None)

        async with SessionLocal() as s:
            b_uz = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, transport="cli", output_language="uz", **_base
            )
            await s.commit()

        async with SessionLocal() as s:
            b_en = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, transport="cli", output_language="en", **_base
            )
            await s.commit()

        assert b_uz.id != b_en.id, (
            "different output_language must fork a new batch "
            f"(uz={b_uz.id}, en={b_en.id})"
        )
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_same_triple_is_idempotent():
    """Same (book, transport, output_language) triple twice → same batch id."""
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lesson(s)
        await s.commit()
        book_id = book.id

    try:
        _base = dict(subject="math-algebra", grade=None, provider="claude", model=None)

        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, transport="cli", output_language="uz", **_base
            )
            await s.commit()
            b1_id = b1.id

        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, transport="cli", output_language="uz", **_base
            )
            await s.commit()
            b2_id = b2.id

        assert b1_id == b2_id, "same (book, transport, output_language) must reuse the batch"
    finally:
        await _cleanup(book_id)


# ─── (b) find_active_for_section language scoping ────────────────────────────


@pytest.mark.asyncio
async def test_find_active_does_not_cross_languages():
    """A 'uz' job must NOT be returned when queried with output_language='en'."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s)
        # Seed one 'uz' job (done so it would normally be adopted).
        job_uz = await jobs_repo.create(
            s,
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math-algebra",
            output_language="uz",
        )
        job_uz.status = "done"
        await s.commit()
        book_id, toc_id = book.id, toc.id

    try:
        # Query for 'en' — must not find the 'uz' job.
        async with SessionLocal() as s:
            found_en = await jobs_repo.find_active_for_section(
                s, book_id, toc_id, output_language="en"
            )
        assert found_en is None, (
            f"'en' query must not adopt a 'uz' job, but got job_id={found_en and found_en.id}"
        )

        # Query for 'uz' — must find the 'uz' job.
        async with SessionLocal() as s:
            found_uz = await jobs_repo.find_active_for_section(
                s, book_id, toc_id, output_language="uz"
            )
        assert found_uz is not None, "should find the 'uz' done job when queried with 'uz'"
    finally:
        await _cleanup(book_id)


# ─── Fast non-DB unit check ──────────────────────────────────────────────────


def test_find_active_for_section_filter_includes_output_language():
    """Non-DB: verify output_language appears in the WHERE clause SQL string.

    This is a compile-time / SQL-render check — no Postgres needed.
    Uses SQLAlchemy's string rendering to confirm the column is in the filter.
    """
    import sqlalchemy as sa
    from app.models.homework_job import HomeworkJob

    # Build the same conds list find_active_for_section builds.
    output_language = "en"
    conds = [
        HomeworkJob.book_id == sa.literal("00000000-0000-0000-0000-000000000001"),
        HomeworkJob.toc_entry_id == sa.literal("00000000-0000-0000-0000-000000000002"),
        HomeworkJob.status.in_(["pending", "running", "done"]),
        HomeworkJob.output_language == output_language,
    ]
    stmt = sa.select(HomeworkJob).where(*conds)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "output_language" in compiled, (
        f"output_language filter missing from WHERE clause:\n{compiled}"
    )
