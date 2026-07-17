"""Real-DB proof of jobs_repo.count_running_for_host — the HOST-WIDE busy
signal the SA-key scrub path uses so an idle process does not clear shared
credential files while a SIBLING process on the same host is mid-job (gate
review finding 1, worklog 0147 follow-up).

`claimed_by` is `hostname:pid`, so two distinct pids on one hostname are two
processes on one host. The count must include a sibling pid's running job (the
whole point) and must exclude other hosts / non-running statuses.

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_scrub \\
    uv run python -m pytest tests/integration/test_count_running_for_host.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="scrub-hostidle.pdf",
        content_sha256="c" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _make_job(s, book, toc, *, status: str, claimed_by: str | None):
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
        output_language="uz",
    )
    await s.execute(
        text("UPDATE homework_jobs SET status=:st, claimed_by=:cb WHERE id=:id"),
        {"st": status, "cb": claimed_by, "id": job.id},
    )
    return job


@pytest.mark.asyncio
async def test_count_running_for_host_counts_sibling_pid_excludes_others():
    from app.db import SessionLocal
    from app.models import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        # A SIBLING process on this host (same hostname, different pid) running a job.
        await _make_job(s, book, toc, status="running", claimed_by="hostA:1111")
        # Same host, a second running sibling — should add to the count.
        await _make_job(s, book, toc, status="running", claimed_by="hostA:2222")
        # Same host but the job is DONE — must not count.
        await _make_job(s, book, toc, status="done", claimed_by="hostA:3333")
        # A DIFFERENT host running — must not count for hostA.
        await _make_job(s, book, toc, status="running", claimed_by="hostB:4444")
        # A host whose NAME is a prefix of hostA — the ':' boundary must exclude it.
        await _make_job(s, book, toc, status="running", claimed_by="host:5555")
        await s.commit()

        try:
            n_hostA = await jobs_repo.count_running_for_host(s, "hostA")
            n_hostB = await jobs_repo.count_running_for_host(s, "hostB")
            n_prefix = await jobs_repo.count_running_for_host(s, "host")
            n_absent = await jobs_repo.count_running_for_host(s, "nope")

            assert n_hostA == 2, "two running sibling pids on hostA must both count"
            assert n_hostB == 1, "hostB's single running job counts for hostB only"
            assert n_prefix == 1, "'host' must match only host:5555, never hostA:*"
            assert n_absent == 0, "no jobs for an unknown host"
        finally:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book.id))
            from app.models.toc_entry import TOCEntry
            from app.models.book import Book
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book.id))
            await s.execute(delete(Book).where(Book.id == book.id))
            await s.commit()
