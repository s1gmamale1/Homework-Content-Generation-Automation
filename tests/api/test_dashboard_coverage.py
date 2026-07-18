"""Real-DB proof: GET /api/v1/dashboard/coverage returns per-book coverage.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL (same guard as every other
api integration test). Seeds a book + TOC rows + jobs, hits the endpoint via
AsyncClient, asserts the aggregate shape and the lesson-class denominator.
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def test_coverage_lesson_scoped_denominator_and_counts(monkeypatch):
    from app import config
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from main import app

    # House pattern (tests/api/test_sa_keys_assign_api.py:23): neutralize auth.
    # Required in a worktree — the outer /Users/macmini5/Documents/.env sets
    # AUTH_TOKEN, which defeats tests/conftest.py's os.environ.setdefault, so
    # without this the auth-protected dashboard router 401s.
    monkeypatch.setattr(config.settings, "auth_token", "")

    book_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(Book(
            id=book_id, subject="biology", grade="9", original_filename="cov.pdf",
            content_sha256=uuid.uuid4().hex, file_size_bytes=1, status="toc_ready",
            source_language="uz",
        ))
        await s.flush()
        toc_ids = []
        for i in range(1, 4):  # 3 plain rows -> lesson
            t = TOCEntry(book_id=book_id, section_title=f"Mavzu {i}",
                         page_start=i, page_end=i, order_index=i)
            s.add(t)
            await s.flush()
            toc_ids.append(t.id)
        test_row = TOCEntry(book_id=book_id, section_title="Nazorat ishi",
                            page_start=9, page_end=9, order_index=9)  # -> test class
        s.add(test_row)
        await s.flush()

        def job(toc_entry_id, status):
            return HomeworkJob(book_id=book_id, toc_entry_id=toc_entry_id,
                               subject="biology", status=status, provider="gemini",
                               transport="api", output_language="uz")

        s.add(job(toc_ids[0], "done"))
        s.add(job(toc_ids[1], "failed"))
        # gate-1: a legacy unfiltered launch left a DONE job on the test row.
        # It must not count — otherwise done would reach lessons_total and the
        # failed lesson above would be masked as "Finished".
        s.add(job(test_row.id, "done"))
        await s.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/dashboard/coverage?output_language=uz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["output_language"] == "uz"
        mine = [e for e in body["entries"] if e["book_id"] == str(book_id)]
        assert len(mine) == 1
        e = mine[0]
        assert e["grade"] == "9" and e["subject"] == "biology"
        assert e["lessons_total"] == 3          # the "Nazorat ishi" test row is excluded
        assert e["done"] == 1                   # NOT 2 — the test-row job is excluded
        assert e["failed"] == 1
        assert e["running"] == 0 and e["pending"] == 0
        assert e["done"] < e["lessons_total"]   # the failed lesson cannot be masked
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


async def test_coverage_language_filter_excludes_other_language_jobs(monkeypatch):
    from app import config
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from main import app

    monkeypatch.setattr(config.settings, "auth_token", "")  # see note above

    book_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(Book(id=book_id, subject="physics", grade="8", original_filename="lang.pdf",
                   content_sha256=uuid.uuid4().hex, file_size_bytes=1,
                   status="toc_ready", source_language="uz"))
        await s.flush()
        t = TOCEntry(book_id=book_id, section_title="Mavzu 1", page_start=1,
                     page_end=1, order_index=1)
        s.add(t)
        await s.flush()
        s.add(HomeworkJob(book_id=book_id, toc_entry_id=t.id, subject="physics",
                          status="done", provider="gemini", transport="api",
                          output_language="ru"))
        await s.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            uz = (await c.get("/api/v1/dashboard/coverage?output_language=uz")).json()
            ru = (await c.get("/api/v1/dashboard/coverage?output_language=ru")).json()
        uz_e = [e for e in uz["entries"] if e["book_id"] == str(book_id)][0]
        ru_e = [e for e in ru["entries"] if e["book_id"] == str(book_id)][0]
        assert uz_e["done"] == 0   # the ru job must not leak into the uz view
        assert ru_e["done"] == 1
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
