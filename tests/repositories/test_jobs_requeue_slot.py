"""Tests for jobs_repo.requeue_slot_saturated and the cancel-wins guard shared
with mark_failed_with_retry (gate correction 6 / round-3 correction 2).

DB integration only (RUN_DB_INTEGRATION=1) — these exercise real Postgres
transactions, including a genuine two-session race for the stale
identity-map regression.

RED-proofs:
  - parked case: without the make_interval push, scheduled_at would not land
    > now()+60s in SQL.
  - cancel-wins: without the `status == "running"` WHERE guard on both
    functions, a `cancelling` job would resurrect to `pending` and burn/
    refund an attempt instead of finalizing cancelled.
  - stale-identity-map: without `_finalize_if_cancelling` re-reading status
    as a FRESH COLUMN SCALAR (never `session.get`), session A's stale
    identity-map copy of the job (loaded before session B's concurrent
    cancel committed) would still read status='running' after the guarded
    UPDATE matches 0 rows, misreporting outcome='skipped' and leaving the
    job stuck at 'cancelling' forever.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
_SKIP_REASON = "set RUN_DB_INTEGRATION=1 with a live DATABASE_URL to run"


async def _seed_book_and_toc(session):
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo

    book = await books_repo.create(
        session,
        subject="math",
        original_filename="test_requeue_slot.pdf",
        content_sha256=uuid.uuid4().hex.ljust(64, "0"),
        file_size_bytes=1,
    )
    toc = TOCEntry(
        book_id=book.id,
        section_title="Test Requeue Slot Section",
        order_index=0,
    )
    session.add(toc)
    await session.flush()
    return book, toc


async def _cleanup(book_id: uuid.UUID | None) -> None:
    if book_id is None:
        return
    from sqlalchemy import delete, select

    from app.db import SessionLocal
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
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_requeue_slot_saturated_parks_with_refund_and_future_schedule():
    """Seed status='running', attempts=2, claimed_by='w'; call
    requeue_slot_saturated(cooldown_seconds=90) -> 'parked'; status='pending',
    attempts==1 (refund), claimed_by is None, current_phase is None, and
    scheduled_at > now()+60s (DB clock) — RED-proof the interval lands in SQL.
    """
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as session:
            book, toc = await _seed_book_and_toc(session)
            book_id = book.id
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="api",
                output_language="uz",
            )
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="running",
                    attempts=2,
                    claimed_by="w",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.requeue_slot_saturated(
                session, job_id, error="429 fleet credential slot wait exhausted",
                cooldown_seconds=90,
            )
            await session.commit()

        assert outcome == "parked", f"expected 'parked', got {outcome!r}"

        async with SessionLocal() as session:
            result = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()

            assert result.status == "pending", f"expected status='pending', got {result.status!r}"
            assert result.attempts == 1, (
                f"expected attempts=1 (refunded from 2), got {result.attempts}"
            )
            assert result.claimed_by is None, f"claimed_by must be cleared, got {result.claimed_by!r}"
            assert result.claimed_at is None, f"claimed_at must be cleared, got {result.claimed_at!r}"
            assert result.current_phase is None, (
                f"current_phase must be cleared, got {result.current_phase!r}"
            )

            now = datetime.now(timezone.utc)
            scheduled = result.scheduled_at
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            assert scheduled > now + timedelta(seconds=60), (
                f"scheduled_at must be pushed well into the future by the "
                f"90s cooldown; scheduled={scheduled}, now={now}"
            )
    finally:
        await _cleanup(book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_requeue_slot_saturated_cancel_wins():
    """Seed status='cancelling' plus one running phase row; requeue_slot_saturated
    -> 'cancelled', job finalized status='cancelled' with completed_at set and
    the phase row 'failed' (mark_cancelled semantics) — NEVER 'pending'."""
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_outputs_repo

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as session:
            book, toc = await _seed_book_and_toc(session)
            book_id = book.id
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="api",
                output_language="uz",
            )
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="cancelling",
                    attempts=1,
                    claimed_by="w",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            phase = await phase_outputs_repo.create_or_reset(
                session,
                job_id=job_id,
                phase_name="flashcards",
                phase_order=0,
                prompt_hash="test-hash",
                model_name="test-model",
                status="running",
            )
            phase_id = phase.id
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.requeue_slot_saturated(
                session, job_id, error="429 fleet credential slot wait exhausted",
                cooldown_seconds=90,
            )
            await session.commit()

        assert outcome == "cancelled", f"expected 'cancelled', got {outcome!r}"

        async with SessionLocal() as session:
            result = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
            assert result.status == "cancelled", (
                f"expected status='cancelled' (never resurrected to 'pending'), "
                f"got {result.status!r}"
            )
            assert result.completed_at is not None, "completed_at must be set on finalize"

            phase_row = (
                await session.execute(select(PhaseOutput).where(PhaseOutput.id == phase_id))
            ).scalar_one()
            assert phase_row.status == "failed", (
                f"non-done phase row must be failed by mark_cancelled semantics, "
                f"got {phase_row.status!r}"
            )
    finally:
        await _cleanup(book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_mark_failed_with_retry_cancel_wins():
    """Same cancel-wins guard on mark_failed_with_retry: seed status='cancelling'
    plus one running phase row; mark_failed_with_retry -> 'cancelled', never
    'pending'/attempt-burn."""
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_outputs_repo

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as session:
            book, toc = await _seed_book_and_toc(session)
            book_id = book.id
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="api",
                output_language="uz",
            )
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="cancelling",
                    attempts=1,
                    claimed_by="w",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            phase = await phase_outputs_repo.create_or_reset(
                session,
                job_id=job_id,
                phase_name="flashcards",
                phase_order=0,
                prompt_hash="test-hash",
                model_name="test-model",
                status="running",
            )
            phase_id = phase.id
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.mark_failed_with_retry(
                session, job_id, error_message="transient blip", max_attempts=5,
            )
            await session.commit()

        assert outcome == "cancelled", f"expected 'cancelled', got {outcome!r}"

        async with SessionLocal() as session:
            result = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
            assert result.status == "cancelled", (
                f"expected status='cancelled' (never resurrected to 'pending'), "
                f"got {result.status!r}"
            )
            assert result.completed_at is not None

            phase_row = (
                await session.execute(select(PhaseOutput).where(PhaseOutput.id == phase_id))
            ).scalar_one()
            assert phase_row.status == "failed"
    finally:
        await _cleanup(book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_mark_failed_with_retry_stale_identity_map_interleaving():
    """The REAL race, two sessions on the scratch DB:
      - session A: session_a.get(HomeworkJob, job_id) loads 'running' into A's
        identity map (exactly what mark_failed_with_retry does at entry).
      - session B: UPDATE ... SET status='cancelling' + commit.
      - session A: mark_failed_with_retry(session_a, ...) -> its guarded UPDATE
        matches 0 rows (status is no longer 'running') and the helper's FRESH
        column read must see 'cancelling' -> returns 'cancelled'.

    RED-proof: with `session.get` in the helper instead of a fresh column
    scalar, A's stale identity-map copy still reads 'running' -> outcome
    'skipped' and the job is left stuck at 'cancelling'.
    """
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as setup_session:
            book, toc = await _seed_book_and_toc(setup_session)
            book_id = book.id
            job = await jobs_repo.create(
                setup_session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="api",
                output_language="uz",
            )
            job_id = job.id
            await setup_session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="running",
                    attempts=1,
                    claimed_by="w",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            await setup_session.commit()

        async with SessionLocal() as session_a:
            # Session A loads the job into ITS identity map while it is
            # still 'running' — exactly what mark_failed_with_retry does
            # at entry via session.get(HomeworkJob, job_id).
            job_a = await session_a.get(HomeworkJob, job_id)
            assert job_a.status == "running"

            # Session B (a fully separate connection) flips the job to
            # 'cancelling' and commits, independent of session A.
            async with SessionLocal() as session_b:
                await session_b.execute(
                    update(HomeworkJob)
                    .where(HomeworkJob.id == job_id)
                    .values(status="cancelling")
                )
                await session_b.commit()

            # Session A now calls mark_failed_with_retry — its identity map
            # still holds the stale 'running' copy of job_a.
            outcome = await jobs_repo.mark_failed_with_retry(
                session_a, job_id, error_message="transient blip", max_attempts=5,
            )
            await session_a.commit()

        assert outcome == "cancelled", (
            f"expected 'cancelled' via fresh column read; got {outcome!r} — "
            f"if this is 'skipped' the helper is using a stale session.get() "
            f"identity-map read instead of a fresh column scalar"
        )

        async with SessionLocal() as session:
            result = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
            assert result.status == "cancelled", (
                f"job must be finalized 'cancelled', never left stuck at "
                f"'cancelling'; got {result.status!r}"
            )
    finally:
        await _cleanup(book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_requeue_session_limited_cancel_wins():
    """Same cancel-wins guard, applied to requeue_session_limited (review
    finding: it updated by ID with NO status guard, so a concurrent user
    cancel — status='cancelling' — got resurrected to 'pending'). Seed
    status='cancelling' plus one running phase row; requeue_session_limited
    -> 'cancelled', job finalized status='cancelled' with completed_at set
    and the phase row 'failed' (mark_cancelled semantics) — NEVER 'pending'."""
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_outputs_repo

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as session:
            book, toc = await _seed_book_and_toc(session)
            book_id = book.id
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="api",
                output_language="uz",
            )
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="cancelling",
                    attempts=1,
                    claimed_by="w",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            phase = await phase_outputs_repo.create_or_reset(
                session,
                job_id=job_id,
                phase_name="flashcards",
                phase_order=0,
                prompt_hash="test-hash",
                model_name="test-model",
                status="running",
            )
            phase_id = phase.id
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.requeue_session_limited(
                session, job_id, error="session-limit pause — resets at unknown",
            )
            await session.commit()

        assert outcome == "cancelled", f"expected 'cancelled', got {outcome!r}"

        async with SessionLocal() as session:
            result = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
            assert result.status == "cancelled", (
                f"expected status='cancelled' (never resurrected to 'pending'), "
                f"got {result.status!r}"
            )
            assert result.completed_at is not None, "completed_at must be set on finalize"

            phase_row = (
                await session.execute(select(PhaseOutput).where(PhaseOutput.id == phase_id))
            ).scalar_one()
            assert phase_row.status == "failed", (
                f"non-done phase row must be failed by mark_cancelled semantics, "
                f"got {phase_row.status!r}"
            )
    finally:
        await _cleanup(book_id)
