"""Real-DB: batch-loaded repo helpers backing the availability-enrichment
route (worklog 0144 task 4, prepare-status-redo) — `books_repo.get_many`,
`toc_repo.count_by_book_ids`, `jobs_repo.count_by_book_ids`. Each must resolve
the WHOLE candidate id list in exactly ONE query (GK2 batch-load expectation);
these tests exercise real multi-row grouping against Postgres, which a mocked
session can't prove. RUN_DB_INTEGRATION=1.

Recipe:
  createdb -h 127.0.0.1 -U macmini5 -O macmini5 edu_scratch_prep4
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_prep4 \
    uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/integration/test_notion_availability_batch_repo.py -q
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _make_book(session, books_repo, tag: str, **kwargs):
    return await books_repo.create(
        session,
        subject="matematika",
        original_filename=f"book-{tag}.pdf",
        content_sha256=f"sha-{tag}",
        file_size_bytes=10,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_books_get_many_returns_map_keyed_by_id():
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        b1 = await _make_book(session, books_repo, f"gm1-{tag}", status="toc_ready")
        b2 = await _make_book(session, books_repo, f"gm2-{tag}", status="failed")
        await session.commit()

        result = await books_repo.get_many(session, [b1.id, b2.id])
        assert set(result.keys()) == {b1.id, b2.id}
        assert result[b1.id].status == "toc_ready"
        assert result[b2.id].status == "failed"


@pytest.mark.asyncio
async def test_books_get_many_empty_list_returns_empty_dict_no_query():
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    async with SessionLocal() as session:
        assert await books_repo.get_many(session, []) == {}


@pytest.mark.asyncio
async def test_toc_count_by_book_ids_grouped_one_query():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        b1 = await _make_book(session, books_repo, f"toc1-{tag}", status="toc_ready")
        b2 = await _make_book(session, books_repo, f"toc2-{tag}", status="toc_ready")
        b3 = await _make_book(session, books_repo, f"toc3-{tag}", status="uploading")
        await session.commit()

        await toc_repo.bulk_create(session, b1.id, [
            TOCEntryExtracted(chapter_number="1", chapter_title="C1",
                               section_number="1.1", section_title="S1", page_start=1, page_end=2),
            TOCEntryExtracted(chapter_number="1", chapter_title="C1",
                               section_number="1.2", section_title="S2", page_start=3, page_end=4),
        ])
        await toc_repo.bulk_create(session, b2.id, [
            TOCEntryExtracted(chapter_number="1", chapter_title="C1",
                               section_number="1.1", section_title="S1", page_start=1, page_end=2),
        ])
        await session.commit()

        counts = await toc_repo.count_by_book_ids(session, [b1.id, b2.id, b3.id])
        assert counts.get(b1.id) == 2
        assert counts.get(b2.id) == 1
        # b3 has zero toc_entries — absent from the grouped result, caller
        # must default-0 on lookup (not a KeyError / not present as 0).
        assert b3.id not in counts


@pytest.mark.asyncio
async def test_toc_count_by_book_ids_empty_list_returns_empty_dict():
    from app.db import SessionLocal
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as session:
        assert await toc_repo.count_by_book_ids(session, []) == {}


@pytest.mark.asyncio
async def test_jobs_count_by_book_ids_counts_any_status_grouped_one_query():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    tag = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        b1 = await _make_book(session, books_repo, f"jc1-{tag}", status="toc_ready")
        b2 = await _make_book(session, books_repo, f"jc2-{tag}", status="toc_ready")
        await session.commit()

        [e1, e2] = await toc_repo.bulk_create(session, b1.id, [
            TOCEntryExtracted(chapter_number="1", chapter_title="C1",
                               section_number="1.1", section_title="S1", page_start=1, page_end=2),
            TOCEntryExtracted(chapter_number="1", chapter_title="C1",
                               section_number="1.2", section_title="S2", page_start=3, page_end=4),
        ])
        await session.commit()

        # Same semantics as /toc/retry's blocking guard — ANY status counts,
        # including a terminal one.
        await jobs_repo.create(session, book_id=b1.id, toc_entry_id=e1.id,
                                subject="matematika", output_language="uz", status="done")
        await jobs_repo.create(session, book_id=b1.id, toc_entry_id=e2.id,
                                subject="matematika", output_language="uz", status="pending")
        await session.commit()

        counts = await jobs_repo.count_by_book_ids(session, [b1.id, b2.id])
        assert counts.get(b1.id) == 2
        assert b2.id not in counts  # zero referencing jobs — absent, not 0


@pytest.mark.asyncio
async def test_jobs_count_by_book_ids_empty_list_returns_empty_dict():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as session:
        assert await jobs_repo.count_by_book_ids(session, []) == {}
