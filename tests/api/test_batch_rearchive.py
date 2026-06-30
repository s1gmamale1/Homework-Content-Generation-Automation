import os
from datetime import datetime, timezone
import pytest
from uuid import uuid4

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from app.db import SessionLocal
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo


async def _seed_batch_with_two_done_jobs(s):
    """Book + 2 TOC entries + a batch + one done+archived job and one done+unarchived job."""
    from app.models.toc_entry import TOCEntry

    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8.pdf", content_sha256="0" * 64,
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
async def test_archive_rollup_splits_done_by_archived_state():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1}


@pytest.mark.asyncio
async def test_done_unarchived_job_ids_returns_only_unarchived():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        ids = await batches_repo.done_unarchived_job_ids(s, batch.id)
        assert ids == [j2.id]
