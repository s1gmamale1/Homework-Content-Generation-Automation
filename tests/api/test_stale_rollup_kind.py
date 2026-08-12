import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from app.db import SessionLocal
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.models.toc_entry import TOCEntry


async def _seed_teacher_batch_with_one_done_job(s):
    """Book + 1 TOC entry + a teacher_material batch + one done teacher job."""
    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8.pdf", content_sha256="1" * 64,
        file_size_bytes=1, source_language="uz",
    )
    e1 = TOCEntry(book_id=book.id, section_number="1", section_title="L1", order_index=0)
    s.add(e1)
    await s.flush()
    batch = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-2.5-pro", transport="api",
        output_language="uz", kind="teacher_material",
    )
    j_teacher = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=e1.id,
        subject="geometriya-g7-11", output_language="uz",
        provider="gemini", model="gemini-2.5-pro",
        transport="api", batch_id=batch.id, kind="teacher_material",
    )
    await jobs_repo.set_status(s, j_teacher.id, "done", completed_at=datetime.now(timezone.utc))
    await jobs_repo.set_notion_archived(s, j_teacher.id, datetime.now(timezone.utc))
    await s.commit()
    return batch, e1, j_teacher


@pytest.mark.asyncio
async def test_teacher_deck_current_but_homework_stamped_section_not_stale():
    async with SessionLocal() as s:
        batch, e1, j_teacher = await _seed_teacher_batch_with_one_done_job(s)
        # Section ALSO has a homework deck: notion_archived_job_id points at some
        # OTHER (homework) job, while notion_teacher_deck_job_id correctly points
        # at the latest teacher job.
        homework_job_id = uuid4()
        await toc_repo.set_notion_archived_job(s, e1.id, homework_job_id)
        await toc_repo.set_notion_teacher_deck_job(s, e1.id, j_teacher.id)
        await s.commit()

        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts["stale"] == 0

        stale_ids = await batches_repo.done_stale_job_ids(s, batch.id)
        assert stale_ids == []


@pytest.mark.asyncio
async def test_teacher_deck_actually_stale():
    async with SessionLocal() as s:
        batch, e1, j_teacher = await _seed_teacher_batch_with_one_done_job(s)
        older_teacher_job_id = uuid4()
        await toc_repo.set_notion_teacher_deck_job(s, e1.id, older_teacher_job_id)
        await s.commit()

        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts["stale"] == 1

        stale_ids = await batches_repo.done_stale_job_ids(s, batch.id)
        assert stale_ids == [j_teacher.id]


async def _seed_homework_batch_with_two_done_jobs(s):
    """Same seeding as tests/api/test_batch_rearchive.py, kept local so this
    file can assert the no-regression case standalone."""
    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8.pdf", content_sha256="2" * 64,
        file_size_bytes=1, source_language="uz",
    )
    e1 = TOCEntry(book_id=book.id, section_number="1", section_title="L1", order_index=0)
    e2 = TOCEntry(book_id=book.id, section_number="2", section_title="L2", order_index=1)
    s.add_all([e1, e2])
    await s.flush()
    batch = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-2.5-pro", transport="api",
        output_language="uz",
    )
    j1 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=e1.id,
                                subject="geometriya-g7-11", output_language="uz",
                                provider="gemini", model="gemini-2.5-pro",
                                transport="api", batch_id=batch.id)
    j2 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=e2.id,
                                subject="geometriya-g7-11", output_language="uz",
                                provider="gemini", model="gemini-2.5-pro",
                                transport="api", batch_id=batch.id)
    await jobs_repo.set_status(s, j1.id, "done", completed_at=datetime.now(timezone.utc))
    await jobs_repo.set_status(s, j2.id, "done", completed_at=datetime.now(timezone.utc))
    await jobs_repo.set_notion_archived(s, j1.id, datetime.now(timezone.utc))  # j1 archived, j2 not
    await s.commit()
    return batch, j1, j2


@pytest.mark.asyncio
async def test_homework_batch_stale_computation_unchanged():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_homework_batch_with_two_done_jobs(s)

        # current: notion_archived_job_id already matches j1 (nothing stamped
        # yet -> None, which the existing predicate treats as not-stale)
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 0}
        assert await batches_repo.done_stale_job_ids(s, batch.id) == []

        # now make it stale: stamp an older job id
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())
        await s.commit()

        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 1}
        assert await batches_repo.done_stale_job_ids(s, batch.id) == [j1.id]
