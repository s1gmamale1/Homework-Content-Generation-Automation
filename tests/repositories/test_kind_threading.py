"""Real-DB: `kind` ('homework' | 'teacher_material') threaded through job/batch
creation and every section read path used by the launch/resume flow, so a
future teacher-material launch never cross-adopts or cross-resumes a student
homework job (and vice versa).

Covers:
  - jobs_repo.create(..., kind=): persists; defaults to 'homework'.
  - batches_repo.get_or_create_for_book(..., kind=): distinct batch per kind
    on the SAME (book, transport, output_language) — and this also proves the
    post-0054 ON CONFLICT fix (Task 1 carried finding #1): the widened unique
    constraint is `uq_batches_book_id_transport_output_language_kind`, so
    `on_conflict_do_update`'s `index_elements` must include "kind" or every
    batch launch raises "there is no unique or exclusion constraint matching
    the ON CONFLICT specification".
  - jobs_repo.find_active_for_section / latest_for_section: kind-scoped —
    a 'homework' job is invisible to a 'teacher_material' query and vice versa.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL is set (mirrors
tests/integration/test_batch_language_key.py's fixture idiom).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_book_with_lesson(s, *, sha):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="kind-test.pdf",
        content_sha256=sha,
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


# ─── jobs_repo.create ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_create_default_kind_is_homework():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s, sha="K" * 64)
        book_id, toc_id = book.id, toc.id
        job = await jobs_repo.create(
            s, book_id=book_id, toc_entry_id=toc_id, subject="math-algebra",
            output_language="uz",
        )
        await s.commit()
        assert job.kind == "homework", f"default kind should be 'homework', got {job.kind!r}"
    await _cleanup(book_id)


@pytest.mark.asyncio
async def test_job_create_explicit_teacher_material_kind_persists():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s, sha="L" * 64)
        book_id, toc_id = book.id, toc.id
        job = await jobs_repo.create(
            s, book_id=book_id, toc_entry_id=toc_id, subject="math-algebra",
            output_language="uz", kind="teacher_material",
        )
        await s.commit()
        job_id = job.id

    async with SessionLocal() as s:
        reloaded = await s.get(HomeworkJob, job_id)
        assert reloaded.kind == "teacher_material", (
            f"explicit kind must persist, got {reloaded.kind!r}"
        )
    await _cleanup(book_id)


# ─── batches_repo.get_or_create_for_book ───────────────────────────────────


@pytest.mark.asyncio
async def test_kind_forks_new_batch_on_same_book_transport_language():
    """Also proves the carried ON CONFLICT fix: this call must not raise
    'there is no unique or exclusion constraint matching the ON CONFLICT
    specification' post-0054."""
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lesson(s, sha="M" * 64)
        await s.commit()
        book_id = book.id

    try:
        _base = dict(subject="math-algebra", grade=None, provider="claude", model=None,
                      transport="cli", output_language="uz")

        async with SessionLocal() as s:
            b_hw = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, kind="homework", **_base
            )
            await s.commit()

        async with SessionLocal() as s:
            b_tm = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, kind="teacher_material", **_base
            )
            await s.commit()

        assert b_hw.id != b_tm.id, (
            "different kind must fork a new batch on the same "
            f"(book, transport, output_language) (hw={b_hw.id}, tm={b_tm.id})"
        )
        assert b_hw.kind == "homework"
        assert b_tm.kind == "teacher_material"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_same_kind_is_idempotent():
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lesson(s, sha="N" * 64)
        await s.commit()
        book_id = book.id

    try:
        _base = dict(subject="math-algebra", grade=None, provider="claude", model=None,
                      transport="cli", output_language="uz")

        async with SessionLocal() as s:
            b1 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, kind="teacher_material", **_base
            )
            await s.commit()
            b1_id = b1.id

        async with SessionLocal() as s:
            b2 = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, kind="teacher_material", **_base
            )
            await s.commit()
            b2_id = b2.id

        assert b1_id == b2_id, "same (book, transport, output_language, kind) must reuse the batch"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_get_or_create_for_book_default_kind_is_homework():
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book_with_lesson(s, sha="O" * 64)
        await s.commit()
        book_id = book.id

    try:
        async with SessionLocal() as s:
            b = await batches_repo.get_or_create_for_book(
                s, book_id=book_id, subject="math-algebra", grade=None,
                provider="claude", model=None, transport="cli",
                output_language="uz",
            )
            await s.commit()
        assert b.kind == "homework", f"default kind should be 'homework', got {b.kind!r}"
    finally:
        await _cleanup(book_id)


# ─── jobs_repo.find_active_for_section / latest_for_section — kind scoping ─


@pytest.mark.asyncio
async def test_find_active_for_section_does_not_cross_kinds():
    """A 'homework' job must NOT be adopted by a 'teacher_material' query,
    and vice versa."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s, sha="P" * 64)
        job_hw = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", kind="homework",
        )
        job_hw.status = "done"
        await s.commit()
        book_id, toc_id = book.id, toc.id

    try:
        async with SessionLocal() as s:
            found_tm = await jobs_repo.find_active_for_section(
                s, book_id, toc_id, output_language="uz", kind="teacher_material",
            )
        assert found_tm is None, (
            "a 'teacher_material' query must not adopt a 'homework' job, "
            f"but got job_id={found_tm and found_tm.id}"
        )

        async with SessionLocal() as s:
            found_hw = await jobs_repo.find_active_for_section(
                s, book_id, toc_id, output_language="uz", kind="homework",
            )
        assert found_hw is not None, "should find the 'homework' done job when queried with kind='homework'"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_latest_for_section_does_not_cross_kinds():
    """A 'homework' job (even failed/cancelled) must NOT be resumed by a
    'teacher_material' relaunch, and vice versa."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s, sha="Q" * 64)
        job_hw = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", kind="homework",
        )
        job_hw.status = "failed"
        await s.commit()
        book_id, toc_id = book.id, toc.id

    try:
        async with SessionLocal() as s:
            latest_tm = await jobs_repo.latest_for_section(
                s, book_id, toc_id, output_language="uz", kind="teacher_material",
            )
        assert latest_tm is None, (
            "a 'teacher_material' relaunch must not find a 'homework' job to resume, "
            f"but got job_id={latest_tm and latest_tm.id}"
        )

        async with SessionLocal() as s:
            latest_hw = await jobs_repo.latest_for_section(
                s, book_id, toc_id, output_language="uz", kind="homework",
            )
        assert latest_hw is not None and latest_hw.id == job_hw.id, (
            "should find the 'homework' failed job when queried with kind='homework'"
        )
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_find_active_for_section_default_kind_is_homework():
    """Callers that don't pass kind= (every existing call site until Task 8)
    must keep matching only 'homework' jobs — behavior-identical default."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_book_with_lesson(s, sha="R" * 64)
        job_tm = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", kind="teacher_material",
        )
        job_tm.status = "done"
        await s.commit()
        book_id, toc_id = book.id, toc.id

    try:
        async with SessionLocal() as s:
            found = await jobs_repo.find_active_for_section(
                s, book_id, toc_id, output_language="uz",
            )
        assert found is None, (
            "default kind='homework' lookup must not adopt a 'teacher_material' job, "
            f"but got job_id={found and found.id}"
        )
    finally:
        await _cleanup(book_id)
