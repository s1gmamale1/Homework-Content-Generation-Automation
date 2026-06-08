"""Real-DB integration for POST /jobs/batch + reads (Phase 2). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func as safunc, select, update

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}


async def _seed_book(sha: str, *, n: int = 5, status: str = "toc_ready",
                     error_message=None):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha * 64, file_size_bytes=1, status=status,
                    error_message=error_message)
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
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_happy_fanout():
    book_id, _ = await _seed_book("a", n=5)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["jobs_created"] == 5
            assert body["lessons_covered"] == 5
            assert body["rollup"].get("pending") == 5
            bid = body["batch_id"]
            g = await c.get(f"/api/v1/jobs/batches/{bid}", headers=_HDR)
            assert g.status_code == 200
            assert g.json()["lessons_covered"] == 5
            assert g.json()["rollup"].get("pending") == 5
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_readiness_guard():
    extracting, _ = await _seed_book("b", n=3, status="toc_extracting")
    failed, _ = await _seed_book("c", n=3, status="failed", error_message="boom-extract")
    empty, _ = await _seed_book("d", n=0, status="toc_ready")
    try:
        async with _client() as c:
            r1 = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(extracting)})
            assert r1.status_code == 409
            r2 = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(failed)})
            assert r2.status_code == 409
            assert "boom-extract" in r2.json()["detail"]
            r3 = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(empty)})
            assert r3.status_code == 422
    finally:
        await _cleanup(extracting)
        await _cleanup(failed)
        await _cleanup(empty)


@pytest.mark.asyncio
async def test_subset_and_foreign_toc():
    book_id, toc_ids = await _seed_book("e", n=5)
    other_id, other_tocs = await _seed_book("f", n=2)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                             json={"book_id": str(book_id),
                                   "toc_entry_ids": [str(toc_ids[0]), str(toc_ids[1])]})
            assert r.status_code == 201, r.text
            assert r.json()["jobs_created"] == 2
            assert r.json()["lessons_covered"] == 2
            r2 = await c.post("/api/v1/jobs/batch", headers=_HDR,
                              json={"book_id": str(book_id), "toc_entry_ids": [str(other_tocs[0])]})
            assert r2.status_code == 422
    finally:
        await _cleanup(book_id)
        await _cleanup(other_id)


@pytest.mark.asyncio
async def test_relaunch_reconciles():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, _ = await _seed_book("g", n=5)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            assert r.json()["jobs_created"] == 5
            bid = r.json()["batch_id"]
            async with SessionLocal() as s:
                jids = (await s.execute(
                    select(HomeworkJob.id).where(HomeworkJob.book_id == book_id).limit(2)
                )).scalars().all()
                for jid in jids:
                    j = await s.get(HomeworkJob, jid)
                    j.status = "failed"
                await s.commit()
            r2 = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            assert r2.status_code == 201, r2.text
            assert r2.json()["batch_id"] == bid, "re-launch must reuse the same batch"
            assert r2.json()["jobs_created"] == 2, "only the 2 failed lessons get fresh jobs"
            assert r2.json()["lessons_covered"] == 5, "denominator stays 5, not 7"
            assert r2.json()["rollup"].get("pending") == 5
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_adopt_orphan_job():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    book_id, toc_ids = await _seed_book("h", n=5)
    try:
        async with SessionLocal() as s:
            j = await jobs_repo.create(s, book_id=book_id, toc_entry_id=toc_ids[0],
                                       subject="math-algebra")
            j.status = "done"
            await s.commit()
            orphan_id = j.id
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            assert r.status_code == 201, r.text
            assert r.json()["jobs_adopted"] >= 1
            assert r.json()["jobs_created"] == 4
            assert r.json()["lessons_covered"] == 5
            assert r.json()["rollup"].get("done") == 1
            assert r.json()["rollup"].get("pending") == 4
            bid = r.json()["batch_id"]
        async with SessionLocal() as s:
            adopted = await s.get(HomeworkJob, orphan_id)
            assert str(adopted.batch_id) == bid, "orphan job adopted into the batch"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_concurrent_launch_one_batch():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    book_id, _ = await _seed_book("i", n=5)
    try:
        async with _client() as c:
            payload = {"book_id": str(book_id)}
            r1, r2 = await asyncio.gather(
                c.post("/api/v1/jobs/batch", headers=_HDR, json=payload),
                c.post("/api/v1/jobs/batch", headers=_HDR, json=payload),
            )
        assert {r1.status_code, r2.status_code} <= {201}, (r1.text, r2.text)
        async with SessionLocal() as s:
            nbatch = (await s.execute(
                select(safunc.count()).select_from(Batch).where(Batch.book_id == book_id)
            )).scalar_one()
            npending = (await s.execute(
                select(safunc.count()).select_from(HomeworkJob).where(
                    HomeworkJob.book_id == book_id, HomeworkJob.status == "pending")
            )).scalar_one()
        assert nbatch == 1, f"expected exactly one batch for the book, got {nbatch}"
        assert npending == 5, f"expected exactly 5 jobs (one per lesson), got {npending}"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_force_regenerates():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, _ = await _seed_book("j", n=5)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            assert r.json()["jobs_created"] == 5
            async with SessionLocal() as s:
                await s.execute(update(HomeworkJob).where(
                    HomeworkJob.book_id == book_id).values(status="done"))
                await s.commit()
            r2 = await c.post("/api/v1/jobs/batch", headers=_HDR,
                              json={"book_id": str(book_id), "force": True})
            assert r2.status_code == 201, r2.text
            assert r2.json()["jobs_created"] == 5, "force creates fresh jobs for all lessons"
            assert r2.json()["lessons_covered"] == 5, "per-lesson-latest still 5 rows"
            assert r2.json()["rollup"].get("pending") == 5
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_jobs_drilldown_is_per_lesson_latest():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, toc_ids = await _seed_book("k", n=4)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            bid = r.json()["batch_id"]
            # fail lesson 0's job, then re-launch -> a NEW (newer) job for lesson 0
            async with SessionLocal() as s:
                jid = (await s.execute(
                    select(HomeworkJob.id).where(HomeworkJob.toc_entry_id == toc_ids[0]))
                ).scalar_one()
                (await s.get(HomeworkJob, jid)).status = "failed"
                await s.commit()
            await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            g = await c.get(f"/api/v1/jobs/batches/{bid}/jobs", headers=_HDR)
        assert g.status_code == 200
        rows = g.json()["jobs"]
        assert len(rows) == 4, f"one row per lesson, got {len(rows)}"
        assert [row["order_index"] for row in rows] == [0, 1, 2, 3], "ordered by order_index"
        lesson0 = next(r for r in rows if r["order_index"] == 0)
        assert lesson0["status"] == "pending", "shows the NEWEST job (the retry), not the failed one"
        assert all("section_title" in r and "job_id" in r and "attempts" in r for r in rows)
        # 404 for an unknown batch
        async with _client() as c:
            nf = await c.get("/api/v1/jobs/batches/00000000-0000-0000-0000-000000000099/jobs", headers=_HDR)
        assert nf.status_code == 404
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batches_list_not_shadowed_by_job_route():
    """Regression: the static `/jobs/batches` list must win over jobs' dynamic
    `/jobs/{job_id}`. Before the router-include reorder it parsed "batches" as a
    job_id and 422'd — which broke the /fleet funnel (its first consumer)."""
    book_id, _ = await _seed_book("z", n=2)
    try:
        async with _client() as c:
            await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            r = await c.get("/api/v1/jobs/batches", headers=_HDR)
        assert r.status_code == 200, f"shadowed by /jobs/{{job_id}}? got {r.status_code}: {r.text}"
        assert any(b["book_id"] == str(book_id) for b in r.json()["batches"])
    finally:
        await _cleanup(book_id)
