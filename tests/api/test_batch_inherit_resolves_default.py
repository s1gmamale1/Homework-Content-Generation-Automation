"""Real-DB: batch launch with extract_transport="inherit" resolves to the global default.

Guards the `launcher-role-transport-default-1` bug fix: when the Fleet launcher
sends `extract_transport="inherit"` (and `judge_transport="inherit"`), the batch
handler must stamp the CONCRETE global default onto the created job row —
NOT "inherit" verbatim, and NOT the job-level transport (which is what "inherit"
would follow if resolved naively).

BITE DESIGN (comment for auditors):
  - Global default: `extract_transport="cli"` (set in the test).
  - Job transport:   `transport="api"` (gemini/gemini-2.5-flash).
  - Launcher sends:  `extract_transport="inherit"`.

  If `resolve_role_transport_default` were broken (returning "inherit" verbatim,
  which then follows job transport "api"), the stamped value would be "api".
  The assertion `job.extract_transport == "cli"` would FAIL, catching the regression.
  The two values ("cli" vs "api") are DIFFERENT, so the test cannot vacuously pass.

RUN_DB_INTEGRATION=1 + DATABASE_URL required (real Postgres; scratch DB recipe).
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

    sha_char must be a SINGLE character — repeated 64× to fill content_sha256
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
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _get_jobs(book_id):
    """Fetch all jobs for a book from DB."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        result = await s.execute(
            select(HomeworkJob).where(HomeworkJob.book_id == book_id)
        )
        return result.scalars().all()


@pytest.mark.asyncio
async def test_batch_inherit_extract_transport_resolves_to_global_default():
    """Batch launch with inherit role transports stamps the global default, not job transport.

    BITE: global default = "cli", job transport = "api". If inherit follows job
    transport (the bug), the stamp would be "api". The assertion "cli" catches it.
    """
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    book_id, _sid = await _seed_book("B")
    try:
        # Step 1: Set global extract_transport default to "cli".
        # This is deliberately DIFFERENT from the job transport ("api") so that
        # the assertion bites: "cli" vs "api" are distinguishable.
        async with SessionLocal() as s:
            await launch_defaults_repo.update(s, {"extract_transport": "cli"})
            await s.commit()

        # Step 2: POST the BATCH launch endpoint, mirroring the new launcher shape.
        # provider/model must be api-capable (gemini/gemini-2.5-flash + transport=api).
        # extract_transport and judge_transport are sent as "inherit" — the launcher default.
        async with _client() as c:
            r = await c.put(
                "/api/v1/settings/launch-defaults",
                headers=_HDR,
                json={"extract_transport": "cli"},
            )
            assert r.status_code == 200, f"PUT launch-defaults failed: {r.text}"

            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "transport": "api",
                    "extract_transport": "inherit",  # launcher sends this
                    "judge_transport": "inherit",    # launcher sends this
                    "extract_provider": None,
                    "extract_model": None,
                    "judge_provider": None,
                    "judge_model": None,
                },
            )

        assert r.status_code in (200, 201), (
            f"batch launch returned {r.status_code}: {r.text}"
        )

        # Step 3: Fetch the created job row and assert the stamped transport.
        jobs = await _get_jobs(book_id)
        assert jobs, "no jobs created for the batch launch"
        job = jobs[0]

        # Core assertion: the stamped extract_transport must be the GLOBAL DEFAULT
        # ("cli"), NOT "inherit" verbatim and NOT the job transport ("api").
        # If resolve_role_transport_default returned "inherit" and it was later
        # resolved to the job transport, the stamp would be "api" — the test would FAIL.
        assert job.extract_transport == "cli", (
            f"expected extract_transport='cli' (global default), "
            f"got {job.extract_transport!r}. "
            "Bug: 'inherit' was NOT resolved to the global default at launch time."
        )

        # Note on judge_transport: the seeded singleton has judge_transport="inherit"
        # (from the migration seed). resolve_role_transport_default("inherit", "inherit")
        # correctly returns "inherit" — so the job row stores "inherit" legitimately.
        # We don't add a separate assertion for judge here because "inherit" is itself
        # the global default value; only the extract role has a concrete non-"inherit"
        # default set by this test ("cli"), making the extract assertion the biting one.

    finally:
        # Restore the singleton extract_transport back to "inherit" (seeded default).
        async with SessionLocal() as s:
            await launch_defaults_repo.update(s, {"extract_transport": "inherit"})
            await s.commit()
        await _cleanup(book_id)
