"""Real-DB: `GET /jobs/{id}` exposes `kind` and threads it into
`planned_phases`, so the FE can tell a teacher_material job apart from a
homework job and route the "View result" link to the right page
(`/deck/:id` vs `/job/:id`/`/preview/:id`).

Covers:
  - a `kind='teacher_material'` job's `GET /jobs/{id}` response returns
    `kind == "teacher_material"` and `planned_phases == ["teacher-deck"]`
    (NOT the 11-phase homework flow).
  - a default `kind='homework'` job is byte-identical to before: `kind ==
    "homework"` and `planned_phases` is the full subject flow.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL is set (mirrors
tests/api/test_teacher_material_api.py's fixture idiom). Scratch DB recipe
per CLAUDE.md — `edu_scratch_jobroute`, NEVER production `edu_copy`:
    createdb edu_scratch_jobroute
    DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_jobroute \
        uv run alembic upgrade head
    RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_jobroute \
        uv run python -m pytest tests/api/test_job_route_kind.py -q
    dropdb edu_scratch_jobroute
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _seed_book(sha_char: str):
    """Seed a toc_ready book with one lesson; return (book_id, toc_entry_id)."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="jobroute.pdf",
            content_sha256=sha_char * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
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
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import select

    async with SessionLocal() as s:
        job_ids = (
            await s.execute(select(HomeworkJob.id).where(HomeworkJob.book_id == book_id))
        ).scalars().all()
        if job_ids:
            await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_get_job_teacher_material_kind_and_planned_phases():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    book_id, toc_id = await _seed_book("J")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=toc_id, subject="math-algebra",
                output_language="uz", kind="teacher_material",
            )
            await s.commit()
            job_id = job.id

        async with _client() as c:
            r = await c.get(f"/api/v1/jobs/{job_id}", headers=_HDR)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "teacher_material"
        assert body["planned_phases"] == ["teacher-deck"], body["planned_phases"]
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_get_job_homework_kind_and_planned_phases_unchanged():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.services.flows import flow_for

    book_id, toc_id = await _seed_book("K")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=toc_id, subject="math-algebra",
                output_language="uz",
            )
            await s.commit()
            job_id = job.id

        async with _client() as c:
            r = await c.get(f"/api/v1/jobs/{job_id}", headers=_HDR)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "homework"
        assert body["planned_phases"] == flow_for("math-algebra")
    finally:
        await _cleanup(book_id)
