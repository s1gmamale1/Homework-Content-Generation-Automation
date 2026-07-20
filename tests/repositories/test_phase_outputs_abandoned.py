"""Tests for phase_outputs.reset_abandoned_phases (queue-correctness-1, Task 5).

When the parallel scheduler cancels in-flight sibling phases (peer hard-failure,
pause/park, or external job-cancel), the peers' rows are left 'running' —
CancelledError is a BaseException, so per-phase except-Exception cleanup never
ran. This reset sweeps those orphaned rows back to a terminal-for-the-moment
state: 'failed' (with a reason) on hard failure/user cancel, or 'pending'
(cleared) when the JOB itself is being requeued/parked (gate correction 4).

Gated like the repo's other real-DB tests (RUN_DB_INTEGRATION=1 + a live
DATABASE_URL). Recipe:
  createdb -h 127.0.0.1 -p 5432 -U edu -O edu edu_scratch_qc
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc \
    uv run alembic upgrade head
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc \
    uv run python -m pytest tests/repositories/test_phase_outputs_abandoned.py -q
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import delete

from app.db import SessionLocal
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.models.toc_entry import TOCEntry
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo


@pytest.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def seeded_job(db_session):
    """Book + TOC + job with 4 phase rows: pending, running, done, failed."""
    book = await books_repo.create(
        db_session,
        subject="math",
        original_filename="test_abandoned.pdf",
        content_sha256="e" * 64,
        file_size_bytes=1,
    )
    toc = TOCEntry(book_id=book.id, section_title="Abandoned Section", order_index=0)
    db_session.add(toc)
    await db_session.flush()

    job = await jobs_repo.create(
        db_session,
        book_id=book.id,
        toc_entry_id=toc.id,
        subject="math",
        transport="cli",
        output_language="uz",
    )

    await phase_repo.create(
        db_session, job_id=job.id, phase_name="flashcards",
        phase_order=0, prompt_hash="a" * 8, model_name="m", status="pending",
    )
    await phase_repo.create(
        db_session, job_id=job.id, phase_name="boss-arena",
        phase_order=1, prompt_hash="b" * 8, model_name="m", status="running",
    )
    await phase_repo.create(
        db_session, job_id=job.id, phase_name="reading",
        phase_order=2, prompt_hash="c" * 8, model_name="m", status="done",
    )
    await phase_repo.create(
        db_session, job_id=job.id, phase_name="reflection",
        phase_order=3, prompt_hash="d" * 8, model_name="m", status="failed",
    )
    await db_session.commit()

    yield job

    async with SessionLocal() as s:
        await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id == job.id))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.id == job.id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book.id))
        await s.execute(delete(Book).where(Book.id == book.id))
        await s.commit()


async def test_reset_abandoned_touches_pending_and_running_only(db_session, seeded_job):
    """Seed 4 phase rows: pending, running, done, failed. RED-proof: predicate
    must flip exactly pending+running → target status and freeze done."""
    n = await phase_repo.reset_abandoned_phases(
        db_session, seeded_job.id,
        phase_names=["flashcards", "boss-arena", "reading", "reflection"],
        status="failed", error_message="abandoned: sibling phase failed",
    )
    assert n == 2
    rows = {r.phase_name: r for r in await phase_repo.list_for_job(db_session, seeded_job.id)}
    assert rows["flashcards"].status == "failed"       # was pending
    assert rows["boss-arena"].status == "failed"       # was running
    assert rows["boss-arena"].error_message == "abandoned: sibling phase failed"
    assert rows["reading"].status == "done"            # frozen
    assert rows["reflection"].status == "failed"       # untouched, no message overwrite
    assert rows["reflection"].error_message is None


async def test_reset_abandoned_to_pending_for_requeued_job(db_session, seeded_job):
    """Gate correction 4: a parked/requeued job's siblings go back to PENDING
    (they are waiting, not failed) and carry no error message."""
    n = await phase_repo.reset_abandoned_phases(
        db_session, seeded_job.id,
        phase_names=["boss-arena"], status="pending",
    )
    assert n == 1
    rows = {r.phase_name: r for r in await phase_repo.list_for_job(db_session, seeded_job.id)}
    assert rows["boss-arena"].status == "pending"
    assert rows["boss-arena"].error_message is None
