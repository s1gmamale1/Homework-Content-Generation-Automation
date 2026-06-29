"""Tests for jobs_repo.requeue_session_limited.

Two test levels:
  1. SQL-shape (offline, no DB): verifies the compiled UPDATE sets the right columns.
  2. DB integration (RUN_DB_INTEGRATION=1): seeds a running job with attempts=2,
     calls requeue_session_limited, then asserts status/attempts/claimed columns.

RED-proof for the attempt-decrement assertion: WITHOUT `attempts = GREATEST(attempts - 1, 0)`
the attempts value stays at 2 (unchanged), so `assert result.attempts == 1` fails.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Minimal fake session for SQL-shape tests
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _CapturingSession:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        try:
            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        except Exception:
            sql = str(stmt)
        self.calls.append(sql)
        return _FakeResult(1)


# ---------------------------------------------------------------------------
# SQL-shape tests (offline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requeue_sets_status_pending():
    """The UPDATE must set status='pending'."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="session-limit test")

    assert session.calls, "session.execute was never called"
    sql = session.calls[0]
    assert "pending" in sql, f"expected 'pending' in SQL; got:\n{sql}"


@pytest.mark.asyncio
async def test_requeue_decrements_attempts():
    """The UPDATE must contain GREATEST(attempts - 1, 0) — the decrement guard."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="session-limit test")

    sql = session.calls[0]
    # GREATEST and the decrement must appear (any casing; SQLAlchemy emits GREATEST)
    assert "GREATEST" in sql.upper(), (
        f"requeue_session_limited must use GREATEST(...) to decrement attempts; got:\n{sql}"
    )


@pytest.mark.asyncio
async def test_requeue_clears_claimed_columns():
    """The UPDATE must NULL claimed_at and claimed_by."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="session-limit test")

    sql = session.calls[0]
    assert "claimed_at" in sql, f"SQL must clear claimed_at; got:\n{sql}"
    assert "claimed_by" in sql, f"SQL must clear claimed_by; got:\n{sql}"


@pytest.mark.asyncio
async def test_requeue_clears_current_phase():
    """The UPDATE must NULL current_phase."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="session-limit test")

    sql = session.calls[0]
    assert "current_phase" in sql, f"SQL must clear current_phase; got:\n{sql}"


@pytest.mark.asyncio
async def test_requeue_sets_last_error():
    """The UPDATE must record the error string in last_error."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="my-error-string")

    sql = session.calls[0]
    assert "last_error" in sql, f"SQL must set last_error; got:\n{sql}"


@pytest.mark.asyncio
async def test_requeue_uses_func_now_for_scheduled_at():
    """scheduled_at must be set to NOW() (DB clock, claimable immediately)."""
    from app.repositories.jobs import requeue_session_limited

    session = _CapturingSession()
    await requeue_session_limited(session, uuid.uuid4(), error="session-limit test")

    sql = session.calls[0]
    assert "scheduled_at" in sql, f"SQL must set scheduled_at; got:\n{sql}"
    assert "now()" in sql.lower(), (
        f"scheduled_at must use DB now() for immediate claimability; got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# DB integration tests (RUN_DB_INTEGRATION=1 required)
# ---------------------------------------------------------------------------

_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
_SKIP_REASON = "set RUN_DB_INTEGRATION=1 with a live DATABASE_URL to run"


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_requeue_session_limited_integration():
    """Integration: seed a running job with attempts=2; after requeue_session_limited:
      - status  == 'pending'
      - attempts == 1  (decremented — RED-proof: without GREATEST(attempts-1,0) stays 2)
      - claimed_by is None
      - claimed_at is None
      - current_phase is None
      - scheduled_at <= now (claimable immediately)
    """
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    book_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None

    try:
        async with SessionLocal() as session:
            # Seed a Book and TOCEntry (FK requirements for HomeworkJob)
            book = await books_repo.create(
                session,
                subject="math",
                original_filename="test_requeue.pdf",
                content_sha256="b" * 64,
                file_size_bytes=1,
            )
            book_id = book.id

            toc = TOCEntry(
                book_id=book.id,
                section_title="Test Requeue Section",
                order_index=0,
            )
            session.add(toc)
            await session.flush()

            # Create job then manually set it to running with attempts=2 and claimed
            job = await jobs_repo.create(
                session,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math",
                transport="cli",
                output_language="uz",
            )
            job_id = job.id

            # Directly UPDATE to the "claimed/running" state with attempts=2
            from sqlalchemy import update
            from app.models.homework_job import HomeworkJob as HJ
            await session.execute(
                update(HJ)
                .where(HJ.id == job_id)
                .values(
                    status="running",
                    attempts=2,
                    claimed_by="test-worker:9999",
                    claimed_at=datetime.now(timezone.utc),
                    current_phase="flashcards",
                )
            )
            await session.commit()

        # Now call requeue_session_limited
        async with SessionLocal() as session:
            await jobs_repo.requeue_session_limited(
                session, job_id, error="session-limit pause — resets at unknown"
            )
            await session.commit()

        # Verify the result
        async with SessionLocal() as session:
            from sqlalchemy import select
            result = (await session.execute(
                select(HomeworkJob).where(HomeworkJob.id == job_id)
            )).scalar_one()

            assert result.status == "pending", (
                f"expected status='pending', got {result.status!r}"
            )
            # RED-proof: without GREATEST(attempts - 1, 0) this remains 2, failing here
            assert result.attempts == 1, (
                f"expected attempts=1 (decremented from 2), got {result.attempts} "
                f"— RED-proof: without decrement it stays 2"
            )
            assert result.claimed_by is None, (
                f"claimed_by must be cleared, got {result.claimed_by!r}"
            )
            assert result.claimed_at is None, (
                f"claimed_at must be cleared, got {result.claimed_at!r}"
            )
            assert result.current_phase is None, (
                f"current_phase must be cleared, got {result.current_phase!r}"
            )
            assert result.last_error is not None and "session-limit" in result.last_error, (
                f"last_error must contain error text, got {result.last_error!r}"
            )
            now = datetime.now(timezone.utc)
            # scheduled_at should be <= now (immediately claimable)
            scheduled = result.scheduled_at
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            assert scheduled <= now, (
                f"scheduled_at should be <= now for immediate claimability; "
                f"scheduled={scheduled}, now={now}"
            )

    finally:
        if book_id is not None:
            async with SessionLocal() as s:
                await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
                await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
                await s.execute(delete(Book).where(Book.id == book_id))
                await s.commit()
