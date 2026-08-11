"""Real-DB: Task 11 fix (round 2) — `GET /books/{id}?kind=` scopes the
per-section `latest_job_status` to that job kind.

Before this fix `_enriched_toc_entries` always called `jobs_repo.latest_by_section`
without `kind`, defaulting to "homework" — so the teacher-material launcher card
(which reuses the same `detail` query) showed HOMEWORK's status for every row,
including "complete", which disabled the Launch button and every row checkbox
for a book whose homework was already done.

Covers:
  (a) default (no `kind` query param) still reflects the HOMEWORK job's status
      — byte-identical to pre-fix behavior.
  (b) `kind=homework` explicitly reflects the homework job's status.
  (c) `kind=teacher_material` reflects the TEACHER job's status, not homework's,
      even when homework is `done` (the launch-blocker scenario from review).
  (d) `kind=bogus` -> 400.

RUN_DB_INTEGRATION=1 + DATABASE_URL required (real Postgres; scratch DB recipe
per CLAUDE.md — never production `edu_copy`).
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


async def _seed_book_with_jobs(sha_char: str):
    """Seed a toc_ready book with one lesson, a DONE homework job for that
    section, and a RUNNING teacher_material job for the same section. Return
    (book_id, toc_entry_id)."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

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
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()

        await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=t.id, subject="math-algebra",
            output_language="uz", status="done", provider="claude",
            transport="cli", kind="homework",
        )
        await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=t.id, subject="math-algebra",
            output_language="uz", status="running", provider="claude",
            transport="cli", kind="teacher_material",
        )
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


def _row_status(body: dict) -> str:
    toc = body["toc"]
    assert len(toc) == 1, toc
    return toc[0]["latest_job_status"]


@pytest.mark.asyncio
async def test_default_kind_reflects_homework_status():
    book_id, sid = await _seed_book_with_jobs("N")
    try:
        async with _client() as c:
            r = await c.get(f"/api/v1/books/{book_id}", headers=_HDR)
        assert r.status_code == 200, r.text
        assert _row_status(r.json()) == "done"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_explicit_kind_homework_reflects_homework_status():
    book_id, sid = await _seed_book_with_jobs("O")
    try:
        async with _client() as c:
            r = await c.get(f"/api/v1/books/{book_id}?kind=homework", headers=_HDR)
        assert r.status_code == 200, r.text
        assert _row_status(r.json()) == "done"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_kind_teacher_material_reflects_teacher_status_not_homework():
    """The launch-blocker scenario: homework is done, but teacher_material must
    show its OWN (running) status — not homework's 'done' — so the teacher
    launcher card doesn't disable Launch / grey out every row."""
    book_id, sid = await _seed_book_with_jobs("P")
    try:
        async with _client() as c:
            r = await c.get(
                f"/api/v1/books/{book_id}?kind=teacher_material", headers=_HDR
            )
        assert r.status_code == 200, r.text
        assert _row_status(r.json()) == "running", (
            "teacher_material row status leaked from the homework job — "
            "GET /books/{id}?kind=teacher_material must scope latest_by_section "
            "by kind"
        )
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_invalid_kind_rejected():
    book_id, sid = await _seed_book_with_jobs("Q")
    try:
        async with _client() as c:
            r = await c.get(f"/api/v1/books/{book_id}?kind=bogus", headers=_HDR)
        assert r.status_code == 400, r.text
    finally:
        await _cleanup(book_id)
