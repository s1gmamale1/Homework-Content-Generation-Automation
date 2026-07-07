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
        assert counts == {"archived": 1, "unarchived": 1, "stale": 0}


@pytest.mark.asyncio
async def test_done_unarchived_job_ids_returns_only_unarchived():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        ids = await batches_repo.done_unarchived_job_ids(s, batch.id)
        assert ids == [j2.id]


@pytest.mark.asyncio
async def test_retry_archive_endpoint_sweeps_unarchived(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from main import app
    from app.api.v1 import batch as batch_api
    from app.services import notion_archive

    called: list = []

    async def _fake_archive(job_id, *, force=False):
        called.append((job_id, force))

    monkeypatch.setattr(notion_archive, "archive_job", _fake_archive)

    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{batch.id}/retry-archive")
    assert r.status_code == 200
    assert r.json()["queued"] == 1

    task = batch_api._REARCHIVE_TASKS.get(batch.id)
    if task is not None:
        await task
    assert called == [(j2.id, False)]   # only the unarchived done job


@pytest.mark.asyncio
async def test_done_job_ids_includes_archived():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        ids = await batches_repo.done_job_ids(s, batch.id)
        assert set(ids) == {j1.id, j2.id}          # BOTH done jobs, incl. archived j1
        # control: the unarchived-only view still excludes the archived one
        assert await batches_repo.done_unarchived_job_ids(s, batch.id) == [j2.id]


@pytest.mark.asyncio
async def test_retry_archive_batch_force_sweeps_all_done(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from main import app
    from app.api.v1 import batch as batch_api
    from app.services import notion_archive

    called: list = []

    async def _fake_archive(job_id, *, force=False):
        called.append((job_id, force))

    monkeypatch.setattr(notion_archive, "archive_job", _fake_archive)

    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{batch.id}/retry-archive?force=true")
    assert r.status_code == 200
    assert r.json()["queued"] == 2                 # both done jobs, incl. the archived one

    task = batch_api._REARCHIVE_TASKS.get(batch.id)
    if task is not None:
        await task
    assert {jid for jid, _ in called} == {j1.id, j2.id}
    assert all(force is True for _, force in called)   # force threaded to every archive


@pytest.mark.asyncio
async def test_retry_archive_unknown_batch_404():
    from httpx import AsyncClient, ASGITransport
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{uuid4()}/retry-archive")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rollup_counts_stale_when_page_holds_older_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        # j1 is archived; stamp its lesson page with a DIFFERENT (older) job id.
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())
        await s.commit()
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 1}


@pytest.mark.asyncio
async def test_rollup_not_stale_when_stamp_matches_latest_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, j1.id)  # fresh
        await s.commit()
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1, "stale": 0}


@pytest.mark.asyncio
async def test_done_stale_job_ids_returns_only_the_stale_job():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        from app.repositories import toc_entries as toc_repo
        await toc_repo.set_notion_archived_job(s, j1.toc_entry_id, uuid4())  # j1 stale
        await s.commit()
        ids = await batches_repo.done_stale_job_ids(s, batch.id)
        assert ids == [j1.id]
