"""Transport validation + persistence at the two creation entry points.

The 400-rejection cases fail validation BEFORE any DB access (the book lookup
happens first, but we monkeypatch it to a ready stub so validation is reached
without Postgres). The success / persistence cases need a real seeded book in
`toc_ready`, so they sit behind RUN_DB_INTEGRATION like the other integration
tests. The `/agent/models` shape check is pure-Python and always runs.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

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


# ─── /agent/models exposes api_supported (shape check, no real DB) ───────────

@pytest.mark.asyncio
async def test_agent_models_exposes_api_supported():
    # The endpoint became DB-touching (it now appends a `fleet` block via
    # workers_repo.aggregate_fleet_capability). Override get_session with a
    # no-workers stub so this offline shape check stays offline — the fleet
    # block resolves to the fail-open online=False shape without a real DB.
    from main import app
    from app.db import get_session

    async def _no_workers_session():
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_session] = _no_workers_session
    try:
        async with _client() as c:
            r = await c.get("/api/v1/agent/models", headers=_HDR)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "api_supported" in body
        sup = body["api_supported"]
        assert sup["claude"] is True
        assert sup["gemini"] is True
        assert sup["kimi"] is False
        assert sup["codex"] is False
        assert sup["opencode"] is False
        # backwards-compat: providers manifest still present
        assert "providers" in body
        # Task 4: additive fleet block present, offline with no workers
        assert body["fleet"]["online"] is False
    finally:
        app.dependency_overrides.pop(get_session, None)


# ─── /generate validation rejections (no real DB needed) ─────────────────────

def _ready_book_patch(monkeypatch):
    """Patch books_repo.get + toc_repo.get in the jobs router so validation is
    reached without touching Postgres. The validator raises 400 before any DB
    write, so we never need a session."""
    from app.api.v1 import jobs as jobs_mod
    from uuid import UUID

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"

        def __init__(self, book_id):
            self.id = book_id

    class _Section:
        def __init__(self, book_id, sid):
            self.id = sid
            self.book_id = book_id

    async def _fake_book(session, book_id):
        return _Book(book_id)

    async def _fake_toc(session, toc_entry_id):
        # Section.book_id must equal the UUID path param the handler resolved.
        return _Section(UUID(_BOOK_ID[0]), toc_entry_id)

    monkeypatch.setattr(jobs_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(jobs_mod.toc_repo, "get", _fake_toc)


_BOOK_ID = ["00000000-0000-0000-0000-000000000001"]
_SECTION_ID = "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_generate_api_kimi_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            f"/api/v1/books/{_BOOK_ID[0]}/sections/{_SECTION_ID}/generate",
            headers=_HDR,
            json={"transport": "api", "provider": "kimi", "model": "kimi-code/kimi-for-coding"},
        )
    assert r.status_code == 400, r.text
    assert "api" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_api_gemini_no_model_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            f"/api/v1/books/{_BOOK_ID[0]}/sections/{_SECTION_ID}/generate",
            headers=_HDR,
            json={"transport": "api", "provider": "gemini"},  # model omitted
        )
    assert r.status_code == 400, r.text
    assert "model" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_bogus_judge_transport_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            f"/api/v1/books/{_BOOK_ID[0]}/sections/{_SECTION_ID}/generate",
            headers=_HDR,
            json={"provider": "claude", "judge_transport": "bogus"},
        )
    assert r.status_code == 400, r.text
    assert "judge_transport" in r.json()["detail"]


@pytest.mark.asyncio
async def test_generate_bogus_extract_transport_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            f"/api/v1/books/{_BOOK_ID[0]}/sections/{_SECTION_ID}/generate",
            headers=_HDR,
            json={"provider": "claude", "extract_transport": "nope"},
        )
    assert r.status_code == 400, r.text
    assert "extract_transport" in r.json()["detail"]


# ─── Pure resolution helper (no DB) ──────────────────────────────────────────

def test_resolve_role_transport():
    from app.services.agent_models import resolve_role_transport
    assert resolve_role_transport("inherit", "api") == "api"
    assert resolve_role_transport("inherit", "cli") == "cli"
    assert resolve_role_transport("cli", "api") == "cli"
    assert resolve_role_transport("api", "cli") == "api"


# ─── /generate success / persistence (real DB) ───────────────────────────────

async def _seed_book(sha: str, *, status: str = "toc_ready"):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status=status)
        s.add(book)
        await s.flush()
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()
        await s.commit()
        return book.id, t.id


async def _seed_book_lessons(sha: str, *, n: int = 3):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        toc_ids = []
        for i in range(n):
            t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(t)
            await s.flush()
            toc_ids.append(t.id)
        await s.commit()
        return book.id, toc_ids


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
async def test_generate_api_claude_persists_transport():
    book_id, sid = await _seed_book("Q")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"transport": "api", "provider": "claude", "model": "claude-opus-4-8"},
            )
        assert r.status_code in (200, 201), r.text
        assert r.json()["transport"] == "api"
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_generate_default_transport_is_cli():
    book_id, sid = await _seed_book("R")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude"},  # no transport field
            )
        assert r.status_code in (200, 201), r.text
        assert r.json()["transport"] == "cli"
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_generate_persists_role_transports():
    book_id, sid = await _seed_book("W")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude", "model": "claude-opus-4-8",
                      "extract_transport": "api", "judge_transport": "cli"},
            )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["extract_transport"] == "api"
        assert body["judge_transport"] == "cli"
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_generate_role_transports_default_inherit():
    book_id, sid = await _seed_book("X")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude"},  # role transports omitted
            )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["extract_transport"] == "inherit"
        assert body["judge_transport"] == "inherit"
    finally:
        await _cleanup(book_id)


# ─── /jobs/batch validation rejections (no real DB needed) ───────────────────

def _ready_batch_patch(monkeypatch):
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
async def test_batch_api_kimi_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            "/api/v1/jobs/batch", headers=_HDR,
            json={"book_id": "00000000-0000-0000-0000-000000000001",
                  "transport": "api", "provider": "kimi",
                  "model": "kimi-code/kimi-for-coding"},
        )
    assert r.status_code == 400, r.text
    assert "api" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_batch_bogus_judge_transport_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            "/api/v1/jobs/batch", headers=_HDR,
            json={"book_id": "00000000-0000-0000-0000-000000000001",
                  "judge_transport": "bogus"},
        )
    assert r.status_code == 400, r.text
    assert "judge_transport" in r.json()["detail"]


@pytest.mark.asyncio
async def test_batch_api_gemini_no_model_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post(
            "/api/v1/jobs/batch", headers=_HDR,
            json={"book_id": "00000000-0000-0000-0000-000000000001",
                  "transport": "api", "provider": "gemini"},  # model omitted
        )
    assert r.status_code == 400, r.text
    assert "model" in r.json()["detail"].lower()


# ─── /jobs/batch success / persistence (real DB) ─────────────────────────────

@_DB
@pytest.mark.asyncio
async def test_batch_api_persists_transport_on_batch_and_jobs():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from sqlalchemy import select
    book_id, _ = await _seed_book_lessons("S", n=3)
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch", headers=_HDR,
                json={"book_id": str(book_id), "transport": "api",
                      "provider": "claude", "model": "claude-opus-4-8"},
            )
        assert r.status_code == 201, r.text
        assert r.json()["jobs_created"] == 3
        async with SessionLocal() as s:
            batch = (await s.execute(
                select(Batch).where(Batch.book_id == book_id))).scalar_one()
            assert batch.transport == "api"
            jobs = (await s.execute(
                select(HomeworkJob).where(HomeworkJob.book_id == book_id))).scalars().all()
            assert len(jobs) == 3
            assert all(j.transport == "api" for j in jobs)
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_batch_default_transport_is_cli():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from sqlalchemy import select
    book_id, _ = await _seed_book_lessons("T", n=2)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                             json={"book_id": str(book_id)})  # no transport
        assert r.status_code == 201, r.text
        async with SessionLocal() as s:
            batch = (await s.execute(
                select(Batch).where(Batch.book_id == book_id))).scalar_one()
            assert batch.transport == "cli"
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_batch_persists_role_transports_on_batch_and_jobs():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from sqlalchemy import select
    book_id, _ = await _seed_book_lessons("Y", n=3)
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch", headers=_HDR,
                json={"book_id": str(book_id), "judge_transport": "cli"},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["jobs_created"] == 3
        assert body["extract_transport"] == "inherit"
        assert body["judge_transport"] == "cli"
        async with SessionLocal() as s:
            batch = (await s.execute(
                select(Batch).where(Batch.book_id == book_id))).scalar_one()
            assert batch.judge_transport == "cli"
            assert batch.extract_transport == "inherit"
            jobs = (await s.execute(
                select(HomeworkJob).where(HomeworkJob.book_id == book_id))).scalars().all()
            assert len(jobs) == 3
            assert all(j.judge_transport == "cli" for j in jobs)
            assert all(j.extract_transport == "inherit" for j in jobs)
    finally:
        await _cleanup(book_id)


# ─── Transport-scoped dedup: api batch over cli-generated book (BLOCKER §9a) ──

@_DB
@pytest.mark.asyncio
async def test_api_batch_over_cli_done_creates_fresh_jobs():
    """N done CLI jobs already exist for the book; an api batch must create N
    fresh api jobs (0 adopted, 0 skipped) because dedup is transport-scoped."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import select
    book_id, toc_ids = await _seed_book_lessons("U", n=3)
    try:
        # Seed N done CLI jobs (default transport=cli), each in its own cli batch
        # would be more realistic, but batch_id NULL is fine for the dedup test.
        async with SessionLocal() as s:
            for tid in toc_ids:
                j = await jobs_repo.create(s, book_id=book_id, toc_entry_id=tid,
                                           subject="math-algebra", output_language="uz")
                j.status = "done"
            await s.commit()
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch", headers=_HDR,
                json={"book_id": str(book_id), "transport": "api",
                      "provider": "claude", "model": "claude-opus-4-8"},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["jobs_created"] == 3, f"expected 3 fresh api jobs, got {body}"
        assert body["jobs_adopted"] == 0, body
        assert body["jobs_skipped"] == 0, body
        async with SessionLocal() as s:
            api_jobs = (await s.execute(
                select(HomeworkJob).where(
                    HomeworkJob.book_id == book_id,
                    HomeworkJob.transport == "api"))).scalars().all()
            assert len(api_jobs) == 3
            assert all(j.transport == "api" for j in api_jobs)
            # the original cli done jobs are untouched
            cli_jobs = (await s.execute(
                select(HomeworkJob).where(
                    HomeworkJob.book_id == book_id,
                    HomeworkJob.transport == "cli"))).scalars().all()
            assert len(cli_jobs) == 3
            assert all(j.status == "done" and j.batch_id is None for j in cli_jobs)
    finally:
        await _cleanup(book_id)


@_DB
@pytest.mark.asyncio
async def test_cli_orphan_not_adopted_into_api_batch():
    """An orphan (batch_id NULL) CLI job must NOT be adopted into an api batch."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import select
    book_id, toc_ids = await _seed_book_lessons("V", n=2)
    try:
        async with SessionLocal() as s:
            j = await jobs_repo.create(s, book_id=book_id, toc_entry_id=toc_ids[0],
                                       subject="math-algebra", output_language="uz")  # transport=cli, batch_id NULL
            j.status = "done"
            await s.commit()
            orphan_id = j.id
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch", headers=_HDR,
                json={"book_id": str(book_id), "transport": "api",
                      "provider": "claude", "model": "claude-opus-4-8"},
            )
        assert r.status_code == 201, r.text
        assert r.json()["jobs_adopted"] == 0, r.json()
        assert r.json()["jobs_created"] == 2, r.json()
        async with SessionLocal() as s:
            orphan = await s.get(HomeworkJob, orphan_id)
            assert orphan.batch_id is None, "cli orphan must NOT be adopted into api batch"
            assert orphan.transport == "cli"
    finally:
        await _cleanup(book_id)
