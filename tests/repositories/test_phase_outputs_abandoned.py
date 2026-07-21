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
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from sqlalchemy import delete, func as sa_func, update

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

    # Capture ids BEFORE rollback: rollback() expires every ORM object in
    # the session, so a post-rollback `job.id`/`book.id` attribute access
    # would trigger a synchronous lazy-refresh outside a greenlet context
    # (sqlalchemy.exc.MissingGreenlet) instead of a clean expired-object
    # reload.
    job_id = job.id
    book_id = book.id

    # Cleanup MUST run through db_session itself, not a second session.
    # Tests call reset_abandoned_phases without committing, so db_session
    # still holds uncommitted UPDATE row locks when this finalizer runs
    # (dependent-fixture finalizers run BEFORE db_session's own teardown
    # closes it) — a second `SessionLocal()` trying to DELETE those same
    # rows blocks on the lock for minutes. Roll back first (releases the
    # locks), then clean up on the same session and commit.
    await db_session.rollback()
    await db_session.execute(delete(PhaseOutput).where(PhaseOutput.job_id == job_id))
    await db_session.execute(delete(HomeworkJob).where(HomeworkJob.id == job_id))
    await db_session.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
    await db_session.execute(delete(Book).where(Book.id == book_id))
    await db_session.commit()


async def test_reset_abandoned_touches_pending_and_running_only(db_session, seeded_job):
    """Seed 4 phase rows: pending, running, done, failed. RED-proof: predicate
    must flip exactly pending+running → target status and freeze done."""
    n = await phase_repo.reset_abandoned_phases(
        db_session, [seeded_job.id],
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
        db_session, [seeded_job.id],
        phase_names=["boss-arena"], status="pending",
    )
    assert n == 1
    rows = {r.phase_name: r for r in await phase_repo.list_for_job(db_session, seeded_job.id)}
    assert rows["boss-arena"].status == "pending"
    assert rows["boss-arena"].error_message is None


def test_empty_job_ids_is_noop_without_touching_session():
    """Contract: empty job_ids returns 0 before any session use."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(None, [], status="pending")
    ) == 0


def test_empty_phase_names_list_is_still_noop():
    """phase_names=[] keeps the #109 no-op contract (None means ALL)."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(
            None, [uuid.uuid4()], phase_names=[], status="pending"
        )
    ) == 0


def test_source_statuses_done_is_rejected():
    """Structural guard: 'done' must never be a narrowable source_status —
    the preservation contract is enforced, not conventional."""
    import asyncio
    with pytest.raises(AssertionError):
        asyncio.run(
            phase_repo.reset_abandoned_phases(
                None, [uuid.uuid4()], status="pending",
                source_statuses=("done",),
            )
        )


def test_source_statuses_failed_is_rejected():
    """Structural guard: 'failed' is reachable ONLY via include_orphan_failed's
    marker equality, never wholesale through source_statuses."""
    import asyncio
    with pytest.raises(AssertionError):
        asyncio.run(
            phase_repo.reset_abandoned_phases(
                None, [uuid.uuid4()], status="pending",
                source_statuses=("failed",),
            )
        )


async def test_orphan_marker_failed_rows_reconcile_but_genuine_failures_never(
    db_session, seeded_job
):
    """The load-bearing predicate (RED-proof: without the marker clause the
    orphan-failed row is untouched; without the equality guard the genuine
    failure would be rewritten).

    seeded_job rows: flashcards=pending, boss-arena=running, reading=done,
    reflection=failed(error=None). Re-point reflection to a GENUINE error and
    add the orphan marker to boss-arena as main.py's boot sweep would."""
    from app.repositories.phase_outputs import ORPHANED_RESTART_MESSAGE
    await db_session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == seeded_job.id,
               PhaseOutput.phase_name == "boss-arena")
        .values(
            status="failed",
            error_message=ORPHANED_RESTART_MESSAGE,
            completed_at=sa_func.now(),
        )
    )
    await db_session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == seeded_job.id,
               PhaseOutput.phase_name == "reflection")
        .values(error_message="judge crashed: real evidence")
    )
    n = await phase_repo.reset_abandoned_phases(
        db_session, [seeded_job.id],
        status="pending",
        source_statuses=("running",),
        include_orphan_failed=True,
    )
    # flashcards is 'pending' but source_statuses=('running',) excludes it;
    # boss-arena matches ONLY via the marker clause.
    assert n == 1
    rows = {r.phase_name: r for r in await phase_repo.list_for_job(db_session, seeded_job.id)}
    assert rows["boss-arena"].status == "pending"
    assert rows["boss-arena"].error_message is None
    assert rows["boss-arena"].completed_at is None
    assert rows["flashcards"].status == "pending"          # untouched
    assert rows["reading"].status == "done"                # frozen
    assert rows["reflection"].status == "failed"           # genuine evidence kept
    assert rows["reflection"].error_message == "judge crashed: real evidence"
