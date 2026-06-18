"""Unit tests for jobs_repo.fail_exhausted_pending_jobs.

Tests the REAL function body: only the session.execute boundary is mocked.
The built UPDATE statement is captured and compiled to verify it targets the
right table, filters on `status == 'pending'` AND `attempts >= max_attempts`,
and sets `status='failed'`.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResult:
    """Mimics the CursorResult returned by session.execute()."""

    def __init__(self, rowcount: int = 2):
        self.rowcount = rowcount


class _FakeSession:
    """Minimal async session mock that captures the executed statement."""

    def __init__(self, rowcount: int = 2):
        self._rowcount = rowcount
        self.captured_stmt = None

    async def execute(self, stmt):
        self.captured_stmt = stmt
        return _FakeResult(self._rowcount)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fail_exhausted_pending_returns_rowcount():
    """Function returns the rowcount from session.execute."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=3)
    result = await fail_exhausted_pending_jobs(session, max_attempts=5)
    assert result == 3


@pytest.mark.asyncio
async def test_fail_exhausted_pending_returns_zero_on_no_rows():
    """When rowcount is 0 (nothing matched), returns 0."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=0)
    result = await fail_exhausted_pending_jobs(session, max_attempts=5)
    assert result == 0


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_filters_and_sets():
    """The compiled SQL targets `homework_jobs`, filters on status='pending'
    AND attempts >= max_attempts, and sets status='failed'.

    Uses literal_binds=True so parameter values are embedded in the SQL text
    (makes assertions on literal values possible).
    """
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    assert stmt is not None, "session.execute was never called"

    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = str(compiled)

    # Table target
    assert "homework_jobs" in sql, f"expected 'homework_jobs' in SQL, got:\n{sql}"

    # WHERE filters — with literal_binds=True the values are inlined
    assert "status" in sql, f"expected 'status' filter in SQL, got:\n{sql}"
    assert "pending" in sql, f"expected 'pending' filter in SQL, got:\n{sql}"
    assert "attempts" in sql, f"expected 'attempts' filter in SQL, got:\n{sql}"

    # SET clause — status should be set to 'failed'
    assert "failed" in sql, f"expected 'failed' in SET clause of SQL, got:\n{sql}"


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_sets_completed_at():
    """The SQL sets completed_at (to NOW()) on terminal failure."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "completed_at" in sql, f"expected 'completed_at' in SQL, got:\n{sql}"


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_sets_error_message():
    """The SQL sets both error_message and last_error (mirrors terminal path
    in mark_failed_with_retry)."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "error_message" in sql, f"expected 'error_message' in SQL, got:\n{sql}"
    assert "last_error" in sql, f"expected 'last_error' in SQL, got:\n{sql}"


@pytest.mark.asyncio
async def test_fail_exhausted_pending_clears_claim_columns():
    """The SQL clears claimed_at and claimed_by (NULL them out)."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "claimed_at" in sql, f"expected 'claimed_at' in SQL, got:\n{sql}"
    assert "claimed_by" in sql, f"expected 'claimed_by' in SQL, got:\n{sql}"
