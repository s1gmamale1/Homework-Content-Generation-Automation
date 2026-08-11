"""Real-DB: Task 8 — `kind` discriminator threaded through the launch API.

Covers:
  (a) default (no `kind`) still creates `kind='homework'` jobs/batch.
  (b) `kind='teacher_material'` creates a batch DISTINCT from a same-book/
      transport/language homework batch, and its jobs are stamped
      `kind='teacher_material'`.
  (c) a teacher_material launch does NOT adopt/resume a section's existing
      *homework* job (find_active_for_section / latest_for_section / reset_for_retry
      are kind-scoped) — it creates a brand-new teacher_material job instead,
      leaving the homework job's failed status untouched.
  (d) `custom_prompts` or `selected_phases` with `kind='teacher_material'` -> 400.
  (e) the rollup payload (`GET /jobs/batches/{id}`) carries `kind`.
  (f) `GET /jobs/{id}/deck` returns `content_json` for a teacher-deck job with
      a persisted deck, and 404 when absent / job not found / not teacher_material.

RUN_DB_INTEGRATION=1 + DATABASE_URL required (real Postgres; scratch DB recipe
per CLAUDE.md — `edu_scratch_teacherdeck`, NEVER production `edu_copy`).
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


async def _seed_book(sha_char: str):
    """Seed a toc_ready book with one lesson; return (book_id, toc_entry_id).

    sha_char must be a SINGLE character — repeated 64x to fill content_sha256
    VARCHAR(64), same pattern as test_launch_stamps_defaults.py.
    """
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


async def _get_jobs(book_id):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        result = await s.execute(select(HomeworkJob).where(HomeworkJob.book_id == book_id))
        return result.scalars().all()


# ─── (a) default kind -> homework, unchanged behavior ────────────────────────

@pytest.mark.asyncio
async def test_default_launch_creates_homework_kind():
    book_id, sid = await _seed_book("D")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={"book_id": str(book_id), "provider": "claude"},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["kind"] == "homework"

        jobs = await _get_jobs(book_id)
        assert len(jobs) == 1
        assert jobs[0].kind == "homework"
    finally:
        await _cleanup(book_id)


# ─── (b) teacher_material forks a DISTINCT batch, jobs stamped teacher_material ─

@pytest.mark.asyncio
async def test_teacher_material_launch_creates_distinct_batch():
    book_id, sid = await _seed_book("E")
    try:
        async with _client() as c:
            r_hw = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={"book_id": str(book_id), "provider": "claude"},
            )
            assert r_hw.status_code == 201, r_hw.text
            hw_batch_id = r_hw.json()["batch_id"]

            r_tm = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "kind": "teacher_material",
                },
            )
            assert r_tm.status_code == 201, r_tm.text
            tm_body = r_tm.json()
            assert tm_body["kind"] == "teacher_material"
            assert tm_body["batch_id"] != hw_batch_id, (
                "teacher_material launch must fork its own batch, not reuse the "
                "homework batch for the same (book, transport, output_language)"
            )

        jobs = await _get_jobs(book_id)
        kinds = sorted(j.kind for j in jobs)
        assert kinds == ["homework", "teacher_material"], kinds
    finally:
        await _cleanup(book_id)


# ─── (c) teacher_material never adopts/resumes a section's homework job ──────

@pytest.mark.asyncio
async def test_teacher_material_does_not_adopt_or_resume_homework_job():
    """Seed a FAILED homework job for a section, then launch teacher_material
    for the same section. Assert: a brand-new teacher_material job is created
    (not the homework job reset), and the homework job's failed status/kind
    are untouched (reset_for_retry was never called on it)."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    book_id, sid = await _seed_book("F")
    try:
        async with SessionLocal() as s:
            hw_job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                output_language="uz", status="failed", provider="claude",
                transport="cli", kind="homework",
            )
            await s.commit()
            hw_job_id = hw_job.id

        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "kind": "teacher_material",
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["jobs_created"] == 1
        assert body["jobs_resumed"] == 0
        assert body["jobs_adopted"] == 0

        jobs = await _get_jobs(book_id)
        assert len(jobs) == 2

        hw_after = next(j for j in jobs if j.id == hw_job_id)
        assert hw_after.status == "failed", (
            f"homework job status changed from 'failed' to {hw_after.status!r} — "
            "teacher_material launch must not touch a homework job's row"
        )
        assert hw_after.kind == "homework"

        tm_job = next(j for j in jobs if j.id != hw_job_id)
        assert tm_job.kind == "teacher_material"
        assert tm_job.status == "pending"
    finally:
        await _cleanup(book_id)


# ─── (d) custom_prompts / selected_phases rejected for teacher_material ──────

@pytest.mark.asyncio
async def test_custom_prompts_rejected_for_teacher_material():
    book_id, sid = await _seed_book("G")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "kind": "teacher_material",
                    "custom_prompts": {"teacher-deck": "some override"},
                },
            )
        assert r.status_code == 400, r.text
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_selected_phases_rejected_for_teacher_material():
    book_id, sid = await _seed_book("H")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "kind": "teacher_material",
                    "selected_phases": ["teacher-deck"],
                },
            )
        assert r.status_code == 400, r.text
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_invalid_kind_rejected():
    book_id, sid = await _seed_book("I")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={"book_id": str(book_id), "provider": "claude", "kind": "bogus"},
            )
        assert r.status_code == 400, r.text
    finally:
        await _cleanup(book_id)


# ─── (e) rollup payload carries kind ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollup_payload_carries_kind():
    book_id, sid = await _seed_book("J")
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "kind": "teacher_material",
                },
            )
            assert r.status_code == 201, r.text
            batch_id = r.json()["batch_id"]

            r2 = await c.get(f"/api/v1/jobs/batches/{batch_id}", headers=_HDR)
        assert r2.status_code == 200, r2.text
        assert r2.json()["kind"] == "teacher_material"
    finally:
        await _cleanup(book_id)


# ─── (f) GET /jobs/{id}/deck ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deck_endpoint_returns_content_json_when_present():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo

    book_id, sid = await _seed_book("K")
    try:
        deck_json = {"title": "Lesson Plan", "sections": [{"heading": "Intro", "body": "..."}]}
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                output_language="uz", status="done", provider="claude",
                transport="cli", kind="teacher_material",
            )
            await s.flush()
            po = await phase_repo.create(
                s, job_id=job.id, phase_name="teacher-deck", phase_order=0,
                prompt_hash="x" * 16, model_name="claude-sonnet-4-6", status="done",
            )
            po.content_json = deck_json
            await s.commit()
            job_id = job.id

        async with _client() as c:
            r = await c.get(f"/api/v1/jobs/{job_id}/deck", headers=_HDR)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == str(job_id)
        assert body["content_json"] == deck_json
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_deck_endpoint_404_when_no_content_json_yet():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo

    book_id, sid = await _seed_book("L")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                output_language="uz", status="running", provider="claude",
                transport="cli", kind="teacher_material",
            )
            await s.flush()
            await phase_repo.create(
                s, job_id=job.id, phase_name="teacher-deck", phase_order=0,
                prompt_hash="x" * 16, model_name="claude-sonnet-4-6", status="running",
            )
            await s.commit()
            job_id = job.id

        async with _client() as c:
            r = await c.get(f"/api/v1/jobs/{job_id}/deck", headers=_HDR)
        assert r.status_code == 404, r.text
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_deck_endpoint_404_when_job_missing():
    from uuid import uuid4

    async with _client() as c:
        r = await c.get(f"/api/v1/jobs/{uuid4()}/deck", headers=_HDR)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_deck_endpoint_404_when_job_is_homework_kind():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    book_id, sid = await _seed_book("M")
    try:
        async with SessionLocal() as s:
            job = await jobs_repo.create(
                s, book_id=book_id, toc_entry_id=sid, subject="math-algebra",
                output_language="uz", status="done", provider="claude",
                transport="cli", kind="homework",
            )
            await s.commit()
            job_id = job.id

        async with _client() as c:
            r = await c.get(f"/api/v1/jobs/{job_id}/deck", headers=_HDR)
        assert r.status_code == 404, r.text
    finally:
        await _cleanup(book_id)
