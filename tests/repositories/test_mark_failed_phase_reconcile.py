"""Real-Postgres queue/phase atomicity for retry, park, and exhaustion.

Each worker queue transition owns two pieces of state: the parent job and the
unfinished phase rows from that claim.  These tests pin the invariant that a
successful transition reconciles both in one caller-owned transaction, while a
lost lease changes no phase row and a concurrent cancellation stays terminal.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)


@pytest.fixture
async def db_session():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def reconcile_job_factory(db_session):
    """Create committed, row-owned jobs; remove only those rows afterward."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    book_ids: list[uuid.UUID] = []

    async def make(*, attempts: int = 1, fenced: bool = True, status: str = "running"):
        token = uuid.uuid4() if fenced else None
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            book = Book(
                subject="math-algebra",
                original_filename=f"phase-reconcile-{uuid.uuid4()}.pdf",
                content_sha256=uuid.uuid4().hex.ljust(64, "a"),
                file_size_bytes=1,
                status="toc_ready",
            )
            session.add(book)
            await session.flush()
            toc = TOCEntry(book_id=book.id, section_title="Owned lesson", order_index=0)
            session.add(toc)
            await session.flush()
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math-algebra",
                output_language="uz",
            )
            await session.execute(
                text(
                    "UPDATE homework_jobs SET status=:status, attempts=:attempts, "
                    "claimed_by='phase-reconcile-test', claim_token=:token, "
                    "claimed_at=now(), started_at=now(), current_phase='memory-check' "
                    "WHERE id=:job_id"
                ),
                {
                    "status": status,
                    "attempts": attempts,
                    "token": token,
                    "job_id": job.id,
                },
            )
            session.add_all(
                [
                    PhaseOutput(
                        job_id=job.id,
                        phase_name="extract",
                        phase_order=0,
                        prompt_hash="extract-hash",
                        model_name="test-model",
                        status="done",
                        output_md="frozen extract",
                        error_message="frozen evidence",
                        completed_at=now,
                        claim_token=token,
                    ),
                    PhaseOutput(
                        job_id=job.id,
                        phase_name="memory-check",
                        phase_order=1,
                        prompt_hash="memory-hash",
                        model_name="test-model",
                        status="running",
                        error_message="old running error",
                        completed_at=now,
                        claim_token=token,
                    ),
                    PhaseOutput(
                        job_id=job.id,
                        phase_name="boss-arena",
                        phase_order=2,
                        prompt_hash="boss-hash",
                        model_name="test-model",
                        status="pending",
                        error_message="old pending error",
                        completed_at=now,
                        claim_token=token,
                    ),
                ]
            )
            await session.commit()
            book_ids.append(book.id)
            return SimpleNamespace(job_id=job.id, book_id=book.id, claim_token=token)

    yield make

    # A RED assertion can leave the test session holding UPDATE row locks.
    # Release those before cleanup opens a second connection, or dependent
    # fixture teardown would wait on its own still-open transaction.
    await db_session.rollback()
    async with SessionLocal() as session:
        for book_id in book_ids:
            job_ids = select(HomeworkJob.id).where(HomeworkJob.book_id == book_id)
            await session.execute(
                delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_(job_ids))
            )
            await session.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
            await session.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await session.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await session.execute(delete(Book).where(Book.id == book_id))
        await session.commit()


async def _state(session, job_id):
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput

    job = (
        await session.execute(
            select(HomeworkJob)
            .where(HomeworkJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    phases = {
        row.phase_name: row
        for row in (
            await session.execute(
                select(PhaseOutput)
                .where(PhaseOutput.job_id == job_id)
                .order_by(PhaseOutput.phase_order)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    return job, phases


def _assert_pending_reconciliation(job, phases, *, attempts: int):
    assert job.status == "pending"
    assert job.attempts == attempts
    assert phases["extract"].status == "done"
    assert phases["extract"].output_md == "frozen extract"
    assert phases["extract"].error_message == "frozen evidence"
    for name in ("memory-check", "boss-arena"):
        assert phases[name].status == "pending"
        assert phases[name].error_message is None
        assert phases[name].completed_at is None
        assert phases[name].claim_token is None


@pytest.mark.parametrize("fenced", [False, True], ids=["legacy", "fenced"])
async def test_retry_reconciles_owned_phases_to_pending(
    db_session, reconcile_job_factory, fenced
):
    """Removing either reconciliation call leaves memory-check running."""
    from app.repositories import jobs as jobs_repo

    row = await reconcile_job_factory(attempts=1, fenced=fenced)
    outcome = await jobs_repo.mark_failed_with_retry(
        db_session,
        row.job_id,
        error_message="memory-check: connection reset",
        max_attempts=3,
        claim_token=row.claim_token,
    )
    assert outcome == (row.job_id if fenced else "pending")
    job, phases = await _state(db_session, row.job_id)
    _assert_pending_reconciliation(job, phases, attempts=1)
    await db_session.commit()


@pytest.mark.parametrize("fenced", [False, True], ids=["legacy", "fenced"])
async def test_exhausted_retry_fails_every_non_done_phase_with_same_error(
    db_session, reconcile_job_factory, fenced
):
    """Exhaustion must be visible at phase level without rewriting done work."""
    from app.repositories import jobs as jobs_repo

    row = await reconcile_job_factory(attempts=3, fenced=fenced)
    message = "memory-check: connection reset"
    outcome = await jobs_repo.mark_failed_with_retry(
        db_session,
        row.job_id,
        error_message=message,
        max_attempts=3,
        claim_token=row.claim_token,
    )
    assert outcome == (row.job_id if fenced else "failed")
    job, phases = await _state(db_session, row.job_id)
    assert job.status == "failed"
    assert job.attempts == 3
    assert phases["extract"].status == "done"
    assert phases["extract"].error_message == "frozen evidence"
    for name in ("memory-check", "boss-arena"):
        assert phases[name].status == "failed"
        assert phases[name].error_message == message
        assert phases[name].completed_at is not None
        assert phases[name].claim_token is None
    await db_session.commit()


@pytest.mark.parametrize("fenced", [False, True], ids=["legacy", "fenced"])
@pytest.mark.parametrize("operation", ["session", "slot"])
async def test_refunded_park_reconciles_owned_phases_without_burning_attempt(
    db_session, reconcile_job_factory, fenced, operation
):
    """Session/slot parking refunds the claim attempt and resets siblings."""
    from app.repositories import jobs as jobs_repo

    row = await reconcile_job_factory(attempts=2, fenced=fenced)
    if operation == "session":
        outcome = await jobs_repo.requeue_session_limited(
            db_session,
            row.job_id,
            error="session limit",
            claim_token=row.claim_token,
        )
        expected = row.job_id if fenced else "requeued"
    else:
        outcome = await jobs_repo.requeue_slot_saturated(
            db_session,
            row.job_id,
            error="fleet credential slot wait exhausted",
            cooldown_seconds=90,
            claim_token=row.claim_token,
        )
        expected = row.job_id if fenced else "parked"
    assert outcome == expected
    job, phases = await _state(db_session, row.job_id)
    _assert_pending_reconciliation(job, phases, attempts=1)
    await db_session.commit()


@pytest.mark.parametrize("operation", ["retry", "session", "slot"])
async def test_foreign_token_changes_no_phase_row(
    db_session, reconcile_job_factory, operation
):
    """A reclaimed worker cannot reset phase rows owned by the current lease."""
    from app.repositories import jobs as jobs_repo
    from app.services import lease

    row = await reconcile_job_factory(attempts=1, fenced=True)
    foreign = uuid.uuid4()
    if operation == "retry":
        outcome = await jobs_repo.mark_failed_with_retry(
            db_session,
            row.job_id,
            error_message="obsolete retry",
            max_attempts=3,
            claim_token=foreign,
        )
    elif operation == "session":
        outcome = await jobs_repo.requeue_session_limited(
            db_session, row.job_id, error="obsolete session", claim_token=foreign
        )
    else:
        outcome = await jobs_repo.requeue_slot_saturated(
            db_session,
            row.job_id,
            error="obsolete slot",
            cooldown_seconds=90,
            claim_token=foreign,
        )
    assert outcome is lease.LeaseLost
    job, phases = await _state(db_session, row.job_id)
    assert job.status == "running"
    assert job.claim_token == row.claim_token
    assert phases["memory-check"].status == "running"
    assert phases["memory-check"].error_message == "old running error"
    assert phases["memory-check"].claim_token == row.claim_token
    assert phases["boss-arena"].status == "pending"
    assert phases["boss-arena"].error_message == "old pending error"
    assert phases["boss-arena"].claim_token == row.claim_token
    await db_session.commit()


@pytest.mark.parametrize("operation", ["retry", "session", "slot"])
async def test_cancel_wins_over_every_fenced_retry_or_park(
    db_session, reconcile_job_factory, operation
):
    """A current-token terminal write finalizes cancelling, never requeues."""
    from app.repositories import jobs as jobs_repo
    from app.services import lease

    row = await reconcile_job_factory(attempts=1, fenced=True, status="cancelling")
    if operation == "retry":
        outcome = await jobs_repo.mark_failed_with_retry(
            db_session,
            row.job_id,
            error_message="cancel won",
            max_attempts=3,
            claim_token=row.claim_token,
        )
    elif operation == "session":
        outcome = await jobs_repo.requeue_session_limited(
            db_session, row.job_id, error="cancel won", claim_token=row.claim_token
        )
    else:
        outcome = await jobs_repo.requeue_slot_saturated(
            db_session,
            row.job_id,
            error="cancel won",
            cooldown_seconds=90,
            claim_token=row.claim_token,
        )
    assert outcome is lease.CancelRequested
    job, phases = await _state(db_session, row.job_id)
    assert job.status == "cancelled"
    assert job.attempts == 1
    assert phases["extract"].status == "done"
    assert phases["memory-check"].status == "failed"
    assert phases["boss-arena"].status == "failed"
    await db_session.commit()


async def test_retry_job_and_phase_changes_share_the_callers_transaction(
    db_session, reconcile_job_factory
):
    """Rolling back the caller rolls back both halves; helpers never commit."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    row = await reconcile_job_factory(attempts=1, fenced=True)
    await jobs_repo.mark_failed_with_retry(
        db_session,
        row.job_id,
        error_message="rollback proof",
        max_attempts=3,
        claim_token=row.claim_token,
    )
    changed_job, changed_phases = await _state(db_session, row.job_id)
    assert changed_job.status == "pending"
    assert changed_phases["memory-check"].status == "pending"

    await db_session.rollback()
    async with SessionLocal() as fresh:
        original_job, original_phases = await _state(fresh, row.job_id)
        assert original_job.status == "running"
        assert original_job.claim_token == row.claim_token
        assert original_phases["memory-check"].status == "running"
        assert original_phases["memory-check"].claim_token == row.claim_token
