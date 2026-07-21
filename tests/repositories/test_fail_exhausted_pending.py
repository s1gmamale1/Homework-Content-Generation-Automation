"""Unit tests for jobs_repo.fail_exhausted_pending_jobs.

Tests the REAL function body: only the session.execute boundary is mocked.
The built UPDATE statement is captured and compiled to verify it targets the
right table, filters on `status == 'pending'` AND `attempts >= max_attempts`,
and sets `status='failed'`.

b9e1abc switched the sweep to `UPDATE ... RETURNING id` (the function now
calls `result.fetchall()` instead of reading `.rowcount`) and, when any ids
come back, additionally runs same-transaction phase reconciliation via
`phase_repo.reset_abandoned_phases` (imported in app/repositories/jobs.py as
`from app.repositories import phase_outputs as phase_repo`). The fakes below
model `fetchall()`, and `phase_repo.reset_abandoned_phases` is monkeypatched
with an AsyncMock so the tests can assert exactly how the reconciliation
helper is invoked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResult:
    """Mimics the CursorResult returned by session.execute() for an
    `UPDATE ... RETURNING id` statement: fetchall() yields one (id,) row per
    matched job."""

    def __init__(self, rowcount: int = 2):
        self.rowcount = rowcount
        self._ids = [uuid4() for _ in range(rowcount)]

    def fetchall(self):
        return [(job_id,) for job_id in self._ids]

    @property
    def ids(self) -> list:
        """The synthetic ids fetchall() will return, exposed for assertions."""
        return list(self._ids)


class _FakeSession:
    """Minimal async session mock that captures the executed statement."""

    def __init__(self, rowcount: int = 2):
        self._rowcount = rowcount
        self.captured_stmt = None
        self.result = _FakeResult(self._rowcount)

    async def execute(self, stmt):
        self.captured_stmt = stmt
        return self.result


@pytest.fixture()
def fake_reset_abandoned_phases(monkeypatch):
    """Patch the exact reference jobs.py reaches the helper through
    (`app.repositories.jobs.phase_repo.reset_abandoned_phases`)."""
    import app.repositories.jobs as jobs_module

    mock = AsyncMock(return_value=0)
    monkeypatch.setattr(jobs_module.phase_repo, "reset_abandoned_phases", mock)
    return mock


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fail_exhausted_pending_returns_rowcount(fake_reset_abandoned_phases):
    """Function returns the count of ids returned by RETURNING."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=3)
    result = await fail_exhausted_pending_jobs(session, max_attempts=5)
    assert result == 3

    fake_reset_abandoned_phases.assert_awaited_once_with(
        session,
        session.result.ids,
        status="failed",
        error_message="attempts exhausted while pending (stale-pending sweep)",
        source_statuses=("pending", "running"),
        include_orphan_failed=True,
    )


@pytest.mark.asyncio
async def test_fail_exhausted_pending_returns_zero_on_no_rows(fake_reset_abandoned_phases):
    """When RETURNING yields no rows (nothing matched), returns 0 and skips
    phase reconciliation entirely."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=0)
    result = await fail_exhausted_pending_jobs(session, max_attempts=5)
    assert result == 0

    fake_reset_abandoned_phases.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_filters_and_sets(fake_reset_abandoned_phases):
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

    fake_reset_abandoned_phases.assert_awaited_once_with(
        session,
        session.result.ids,
        status="failed",
        error_message="attempts exhausted while pending (stale-pending sweep)",
        source_statuses=("pending", "running"),
        include_orphan_failed=True,
    )


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_sets_completed_at(fake_reset_abandoned_phases):
    """The SQL sets completed_at (to NOW()) on terminal failure."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "completed_at" in sql, f"expected 'completed_at' in SQL, got:\n{sql}"


@pytest.mark.asyncio
async def test_fail_exhausted_pending_sql_sets_error_message(fake_reset_abandoned_phases):
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
async def test_fail_exhausted_pending_clears_claim_columns(fake_reset_abandoned_phases):
    """The SQL clears claimed_at and claimed_by (NULL them out)."""
    from app.repositories.jobs import fail_exhausted_pending_jobs

    session = _FakeSession(rowcount=1)
    await fail_exhausted_pending_jobs(session, max_attempts=4)

    stmt = session.captured_stmt
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "claimed_at" in sql, f"expected 'claimed_at' in SQL, got:\n{sql}"
    assert "claimed_by" in sql, f"expected 'claimed_by' in SQL, got:\n{sql}"

    fake_reset_abandoned_phases.assert_awaited_once_with(
        session,
        session.result.ids,
        status="failed",
        error_message="attempts exhausted while pending (stale-pending sweep)",
        source_statuses=("pending", "running"),
        include_orphan_failed=True,
    )
