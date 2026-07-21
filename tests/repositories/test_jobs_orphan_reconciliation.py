"""Tests for jobs.reclaim_stuck_jobs / fail_exhausted_pending_jobs reconciling
phase rows in the same transaction (orphan-phase-reconciliation-1, Task 2).

Before this fix, a job reclaimed/exhausted at the JOB level left its
in-flight phase rows untouched — a reclaimed job showed a stale 'running'
phase row, and an exhausted-pending job could fail with ZERO failed phase
rows (invisible to phase-level watchers). Both sites now call
phase_outputs.reset_abandoned_phases in the same transaction as the job
UPDATE (via RETURNING id).

Gated like the repo's other real-DB tests (RUN_DB_INTEGRATION=1 + a live
DATABASE_URL). Recipe:
  createdb -h 127.0.0.1 -p 5432 -U edu -O edu edu_scratch_qc
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc \
    uv run alembic upgrade head
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc \
    uv run python -m pytest tests/repositories/test_jobs_orphan_reconciliation.py -q
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import delete, func, select, update

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


async def _job(session, job_id):
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def _phases(session, job_id):
    session.expire_all()
    rows = await phase_repo.list_for_job(session, job_id)
    return {r.phase_name: r for r in rows}


@pytest.fixture
async def seed(db_session):
    """Factory fixture: seed(status, attempts, phases, claimed_at_age_seconds=0)
    -> job. Creates one book + toc entry + a job with parametrizable
    status/attempts/claimed_at, and phase rows given as
    (name, status, error_message) tuples."""
    created_job_ids: list = []
    created_book_ids: list = []

    async def _seed(
        *,
        status: str,
        attempts: int,
        phases: list,
        claimed_at_age_seconds: int = 0,
    ):
        book = await books_repo.create(
            db_session,
            subject="math",
            original_filename="test_orphan_recon.pdf",
            content_sha256=uuid.uuid4().hex.ljust(64, "f"),
            file_size_bytes=1,
        )
        toc = TOCEntry(book_id=book.id, section_title="Orphan Recon Section", order_index=0)
        db_session.add(toc)
        await db_session.flush()

        job = await jobs_repo.create(
            db_session,
            book_id=book.id,
            toc_entry_id=toc.id,
            subject="math",
            transport="api",
            output_language="uz",
            status=status,
            provider="gemini",
            model="gemini-2.5-flash",
        )
        await db_session.execute(
            update(HomeworkJob)
            .where(HomeworkJob.id == job.id)
            .values(
                attempts=attempts,
                claimed_at=func.now() - func.make_interval(
                    0, 0, 0, 0, 0, 0, claimed_at_age_seconds
                ),
            )
        )

        for order_index, (name, phase_status, error_message) in enumerate(phases):
            await phase_repo.create(
                db_session,
                job_id=job.id,
                phase_name=name,
                phase_order=order_index,
                prompt_hash=f"{order_index:08d}",
                model_name="gemini-2.5-flash",
                status="pending",
            )
            if phase_status != "pending" or error_message is not None:
                await db_session.execute(
                    update(PhaseOutput)
                    .where(PhaseOutput.job_id == job.id, PhaseOutput.phase_name == name)
                    .values(status=phase_status, error_message=error_message)
                )

        await db_session.commit()
        created_job_ids.append(job.id)
        created_book_ids.append(book.id)
        return job

    yield _seed

    # Capture ids BEFORE rollback: rollback() expires every ORM object in the
    # session, so post-rollback attribute access would trigger a synchronous
    # lazy-refresh outside a greenlet context. Ids are already captured above
    # (plain UUIDs appended to the lists), so nothing further to pin here.
    await db_session.rollback()
    for job_id in created_job_ids:
        await db_session.execute(delete(PhaseOutput).where(PhaseOutput.job_id == job_id))
        await db_session.execute(delete(HomeworkJob).where(HomeworkJob.id == job_id))
    for book_id in created_book_ids:
        await db_session.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await db_session.execute(delete(Book).where(Book.id == book_id))
    await db_session.commit()


async def test_reclaim_resets_running_phase_rows(db_session, seed):
    """Spec test 1. RED: today the running row survives the reclaim."""
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=9999,
                    phases=[("a", "done", None), ("b", "running", None),
                            ("c", "pending", None)])
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 1
    row = await _job(db_session, job.id)
    assert row.status == "pending"
    phases = await _phases(db_session, job.id)
    assert phases["b"].status == "pending" and phases["b"].error_message is None
    assert phases["a"].status == "done"
    assert phases["c"].status == "pending"


async def test_exhausted_sweep_fails_unfinished_phase_rows(db_session, seed):
    """Spec test 2 — the field-case pin (10 done + 1 running, invisible
    failure). RED: today the job fails with ZERO failed phase rows."""
    phases = [(f"p{i}", "done", None) for i in range(10)] + [("stuck", "running", None)]
    job = await seed(status="pending", attempts=3, phases=phases)
    n = await jobs_repo.fail_exhausted_pending_jobs(db_session, max_attempts=3)
    assert n == 1
    row = await _job(db_session, job.id)
    assert row.status == "failed"
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "failed"
    assert ph["stuck"].error_message == "attempts exhausted while pending (stale-pending sweep)"
    assert all(ph[f"p{i}"].status == "done" for i in range(10))


async def test_fresh_claims_and_their_rows_untouched(db_session, seed):
    """Spec test 3: a non-stale running job is not reclaimed, rows unchanged."""
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=0,
                    phases=[("b", "running", None)])
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 0
    assert (await _phases(db_session, job.id))["b"].status == "running"


async def test_startup_chain_reclaim_then_exhausted(db_session, seed):
    """Spec test 4: the exact main.py order in ONE transaction — stale
    running parent already at max attempts. Reclaim flips it pending (rows
    -> pending), then the exhausted sweep terminal-fails it and the
    unfinished phase carries the sweep message."""
    phases = [(f"p{i}", "done", None) for i in range(10)] + [("stuck", "running", None)]
    job = await seed(status="running", attempts=3,
                    claimed_at_age_seconds=9999, phases=phases)
    await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    await jobs_repo.fail_exhausted_pending_jobs(db_session, max_attempts=3)
    row = await _job(db_session, job.id)
    assert row.status == "failed"
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "failed"
    assert ph["stuck"].error_message == "attempts exhausted while pending (stale-pending sweep)"


async def test_startup_marker_rows_reconcile_but_genuine_failures_kept(db_session, seed):
    """Spec test 5: main.py's boot sweep runs FIRST and pre-marks the
    unfinished row failed/ORPHANED_RESTART_MESSAGE — reclaim must still
    reconcile it; a genuinely-failed sibling keeps its evidence."""
    from app.repositories.phase_outputs import ORPHANED_RESTART_MESSAGE
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=9999,
                    phases=[("a", "done", None),
                            ("stuck", "failed", ORPHANED_RESTART_MESSAGE),
                            ("real", "failed", "judge crashed: real evidence")])
    # main.py's boot sweep stamps completed_at on the marker row too
    # (set_status(..., completed_at=...)) — the reclaim's pending-reset
    # must clear it, or the row resurfaces as pending-with-a-completion-
    # timestamp (inconsistent state).
    await db_session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == job.id, PhaseOutput.phase_name == "stuck")
        .values(completed_at=func.now())
    )
    await db_session.commit()
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 1
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "pending" and ph["stuck"].error_message is None
    assert ph["stuck"].completed_at is None
    assert ph["real"].status == "failed"
    assert ph["real"].error_message == "judge crashed: real evidence"
