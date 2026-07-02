"""Real-DB: launch endpoints stamp concrete solver_* onto job/batch rows.

Mirrors `tests/api/test_launch_stamps_defaults.py` (judge) and
`tests/api/test_batch_inherit_resolves_default.py` (transport-inherit bite),
adapted for the CQ-C solver role (Task 5).

Covers:
  (a) solver Auto (request omits solver_*) -> job row stamped with the
      migration-0043-seeded singleton default (gemini/gemini-3.1-pro-preview,
      transport inherit) -> NOT NULL, not silently dropped.
  (b) explicit solver_provider=claude (model Auto) -> job row stamped
      claude/claude-sonnet-4-6 (that provider's own default), proving
      resolve_role_selection is wired for the solver role exactly like judge.
  (c) BATCH launch with solver_transport="inherit" resolves to the GLOBAL
      DEFAULT, not the job's own transport (bite: default set to "cli",
      job transport is "api" -- if inherit followed job transport instead
      of the global default, the stamped value would be "api", not "cli").

RUN_DB_INTEGRATION=1 + DATABASE_URL required (real Postgres, migrated to
head 0043 -- see CLAUDE.md scratch-DB recipe).
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

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

    sha_char must be a SINGLE character -- repeated 64x to fill the
    content_sha256 VARCHAR(64) column (same pattern as the judge test).
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
async def test_solver_auto_stamps_seeded_singleton_default():
    """(a) Launch with solver=Auto -> job row stamped with the seeded
    migration-0043 singleton default (gemini/gemini-3.1-pro-preview)."""
    book_id, sid = await _seed_book("Q")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude"},  # solver_provider omitted = Auto
            )
        assert r.status_code in (200, 201), r.text
        job = await _get_job(book_id)
        assert job is not None
        assert job.solver_provider == "gemini", f"expected gemini, got {job.solver_provider!r}"
        assert job.solver_model == "gemini-3.1-pro-preview", (
            f"expected gemini-3.1-pro-preview, got {job.solver_model!r}"
        )
        assert job.solver_transport is not None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_explicit_solver_provider_uses_that_providers_default_model():
    """(b) solver_provider=claude, model Auto -> job row stamped
    claude/claude-sonnet-4-6 (claude's own default), not the global default's
    model."""
    book_id, sid = await _seed_book("R")
    try:
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude", "solver_provider": "claude"},  # solver model omitted = Auto
            )
        assert r.status_code in (200, 201), r.text
        job = await _get_job(book_id)
        assert job is not None
        assert job.solver_provider == "claude", f"expected claude, got {job.solver_provider!r}"
        assert job.solver_model == "claude-sonnet-4-6", (
            f"expected claude-sonnet-4-6 (claude's default), got {job.solver_model!r}"
        )
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_solver_transport_inherit_resolves_to_global_default():
    """(c) Batch launch with solver_transport="inherit" stamps the GLOBAL
    DEFAULT, not the job's own transport.

    BITE: global solver_transport default is set to "cli" (via a raw UPDATE --
    solver_transport is not yet in launch_defaults_repo._MUTABLE, that's a
    separate settings-endpoint task). Job transport is "api". If
    resolve_role_transport_default were broken (returning "inherit" verbatim,
    which then follows job transport "api" downstream), the stamped value
    would be "api". The assertion "cli" catches it.
    """
    from app.db import SessionLocal
    from app.models.launch_defaults import LaunchDefaults

    book_id, _sid = await _seed_book("S")
    try:
        async with SessionLocal() as s:
            await s.execute(
                update(LaunchDefaults).where(LaunchDefaults.id == 1)
                .values(solver_transport="cli")
            )
            await s.commit()

        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "transport": "api",
                    "extract_transport": "inherit",
                    "judge_transport": "inherit",
                    "solver_transport": "inherit",  # launcher sends this
                },
            )
        assert r.status_code in (200, 201), f"batch launch returned {r.status_code}: {r.text}"

        job = await _get_job(book_id)
        assert job is not None
        assert job.solver_transport == "cli", (
            f"expected solver_transport='cli' (global default), got {job.solver_transport!r}. "
            "Bug: 'inherit' was NOT resolved to the global default at launch time."
        )
        assert job.solver_provider == "gemini", f"expected gemini, got {job.solver_provider!r}"
    finally:
        async with SessionLocal() as s:
            await s.execute(
                update(LaunchDefaults).where(LaunchDefaults.id == 1)
                .values(solver_transport="inherit")
            )
            await s.commit()
        await _cleanup(book_id)
