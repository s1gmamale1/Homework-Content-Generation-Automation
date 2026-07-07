"""TDD: per-batch session_limit_strategy exposed at launch.

Offline rejection test (no DB needed):
  - bad value → 400

Real-PG integration tests (RUN_DB_INTEGRATION=1 + DATABASE_URL):
  - session_limit_strategy="switch" → batch row carries "switch"
  - omitted → batch row carries "inherit" (default)
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"Authorization": "Bearer 123"}

_DB = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ── offline: bogus value → 400 (no DB needed) ────────────────────────────────

def _ready_batch_patch(monkeypatch):
    """Monkeypatch books_repo.get + toc_repo.list_for_book to avoid DB."""
    from app.api.v1 import batch as batch_mod

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"
        grade = None
        error_message = None

    class _TOC:
        def __init__(self, i):
            self.id = i
            self.section_title = f"L{i}"
            self.order_index = i
            self.page_start = None
            self.page_end = None

    async def _fake_book(session, book_id):
        return _Book()

    async def _fake_list(session, book_id):
        return [_TOC(0), _TOC(1)]

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _fake_list)


@pytest.mark.asyncio
async def test_bogus_session_limit_strategy_rejected(monkeypatch):
    """A bad session_limit_strategy string must yield HTTP 400."""
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            "/api/v1/jobs/batch",
            headers=_HDR,
            json={
                "book_id": "00000000-0000-0000-0000-000000000001",
                "session_limit_strategy": "bogus",
            },
        )
    assert r.status_code == 400, r.text
    assert "session_limit_strategy" in r.json()["detail"]


# ── real-PG: strategy persisted on the batch row ─────────────────────────────

async def _seed_book(sha_char: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="x.pdf",
            content_sha256=sha_char * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        for i in range(2):
            t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(t)
        await s.flush()
        await s.commit()
        return book.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import delete

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@_DB
@pytest.mark.asyncio
async def test_session_limit_strategy_switch_persisted():
    """Launch with session_limit_strategy='switch' → batch row carries 'switch'."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from sqlalchemy import select

    book_id = await _seed_book("A")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "session_limit_strategy": "switch",
                },
            )
        assert r.status_code == 201, r.text
        async with SessionLocal() as s:
            batch = (
                await s.execute(select(Batch).where(Batch.book_id == book_id))
            ).scalar_one()
            assert batch.session_limit_strategy == "switch"
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_session_limit_strategy_default_is_inherit():
    """Omitting session_limit_strategy → batch row carries 'inherit'."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from sqlalchemy import select

    book_id = await _seed_book("B")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={"book_id": str(book_id)},
            )
        assert r.status_code == 201, r.text
        async with SessionLocal() as s:
            batch = (
                await s.execute(select(Batch).where(Batch.book_id == book_id))
            ).scalar_one()
            assert batch.session_limit_strategy == "inherit"
    finally:
        await _cleanup(book_id)
