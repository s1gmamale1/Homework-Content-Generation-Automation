"""Unit tests for the batch-pause primitive in batches.py and the
claim-gate predicate in jobs.py (no real DB needed).

These tests operate at two levels:
  1. Source/SQL-shape inspection — verifies the correct SQL predicates are
     present in the compiled statements (same pattern as test_cancel_repo.py,
     test_fail_exhausted_pending.py).
  2. Async-session mock tests — drives the real async function bodies against a
     minimal fake session, confirming return values and that the right SQL is
     issued.

The NULL-arm regression test (test_4) is the most critical: it verifies that
the `IS NULL` arm is present in claim_next_job's compiled SQL, which guards
against batchless jobs becoming unclaimbable when any batch is paused.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Fake session helpers
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return None  # claim finds no job — default for pause tests


class _CapturingSession:
    """Minimal async session that captures ALL execute calls in order."""

    def __init__(self, rowcount: int = 1):
        self._rowcount = rowcount
        self.calls: list = []  # list of compiled SQL strings

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
        return _FakeResult(self._rowcount)

    async def get(self, model, pk):
        return None  # not needed for these tests


# ---------------------------------------------------------------------------
# Test 1 — pause_batch sets paused_at and paused_reason (source inspection)
# ---------------------------------------------------------------------------

def test_pause_batch_source_sets_paused_at_and_reason():
    """pause_batch must UPDATE batches setting paused_at and paused_reason."""
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.pause_batch)
    assert "paused_at" in src, "pause_batch must set paused_at"
    assert "paused_reason" in src, "pause_batch must set paused_reason"
    assert "func.now()" in src or "now()" in src, "pause_batch must use now() for paused_at"


def test_unpause_batch_source_clears_both_columns():
    """unpause_batch must clear paused_at and paused_reason (set to None)."""
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.unpause_batch)
    assert "paused_at" in src, "unpause_batch must clear paused_at"
    assert "paused_reason" in src, "unpause_batch must clear paused_reason"


def test_unpause_by_reason_source_filters_on_reason():
    """unpause_by_reason must filter on paused_reason and clear both columns."""
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.unpause_by_reason)
    assert "paused_reason" in src, "unpause_by_reason must filter on paused_reason"
    assert "paused_at" in src, "unpause_by_reason must clear paused_at"
    assert "rowcount" in src, "unpause_by_reason must return rowcount"


def test_active_batch_ids_source_filters_paused_at_is_none():
    """active_batch_ids must filter on paused_at IS NULL."""
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.active_batch_ids)
    assert "paused_at" in src, "active_batch_ids must filter on paused_at"


# ---------------------------------------------------------------------------
# Test 2 — SQL shape: pause_batch issues correct UPDATE statement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_batch_sql_shape():
    """pause_batch executes an UPDATE on 'batches' that references paused_at."""
    from app.repositories.batches import pause_batch
    import uuid

    session = _CapturingSession()
    await pause_batch(session, uuid.uuid4(), "fleet-gate")

    assert len(session.calls) == 1, "pause_batch must call execute exactly once"
    sql = session.calls[0]
    assert "batches" in sql, f"UPDATE must target 'batches', got:\n{sql}"
    assert "paused_at" in sql, f"SQL must set paused_at, got:\n{sql}"
    assert "paused_reason" in sql, f"SQL must set paused_reason, got:\n{sql}"
    assert "NOW()" in sql.upper(), f"SQL must set paused_at via now(), got:\n{sql}"


@pytest.mark.asyncio
async def test_unpause_batch_sql_shape():
    """unpause_batch executes an UPDATE on 'batches' clearing paused_at."""
    from app.repositories.batches import unpause_batch
    import uuid

    session = _CapturingSession()
    await unpause_batch(session, uuid.uuid4())

    assert len(session.calls) == 1
    sql = session.calls[0]
    assert "batches" in sql
    assert "paused_at" in sql


@pytest.mark.asyncio
async def test_unpause_by_reason_returns_rowcount():
    """unpause_by_reason returns the integer rowcount from session.execute."""
    from app.repositories.batches import unpause_by_reason

    session = _CapturingSession(rowcount=3)
    result = await unpause_by_reason(session, "fleet-gate")
    assert result == 3, f"expected 3, got {result}"


# ---------------------------------------------------------------------------
# Test 3 — NULL-arm regression (source + SQL shape)
# The IS NULL arm must be present in claim_next_job's pick statement.
# ---------------------------------------------------------------------------

def test_claim_next_job_null_arm_in_source():
    """claim_next_job must contain the `batch_id.is_(None)` arm in source.

    This is the crux: without IS NULL, every batchless job becomes
    unclaimable the instant any batch is paused (SQL NULL NOT IN
    non-empty-set = NULL = excluded row).
    """
    from app.repositories import jobs as jobs_repo

    src = inspect.getsource(jobs_repo.claim_next_job)
    # Both the IS NULL arm and the NOT IN paused subquery must appear
    assert "batch_id.is_(None)" in src or "batch_id IS NULL" in src.upper(), (
        "MISSING IS NULL arm — batchless jobs would be unclaimable when a batch is paused"
    )
    assert "paused_at" in src, (
        "claim_next_job must filter on Batch.paused_at to skip paused-batch jobs"
    )
    assert "not_in" in src or "NOT IN" in src.upper(), (
        "claim_next_job must use NOT IN to exclude jobs in paused batches"
    )


@pytest.mark.asyncio
async def test_claim_next_job_sql_contains_null_arm():
    """The compiled pick SELECT must include the IS NULL arm for batch_id.

    Drives the real claim_next_job body against a fake session that captures
    the pick statement before returning None (no claimable job). If the IS NULL
    arm is removed from jobs.py, this test fails immediately.
    """
    from app.repositories.jobs import claim_next_job

    class _SelectCapturingSession(_CapturingSession):
        def __init__(self):
            super().__init__(rowcount=0)
            self.select_sql: str = ""

        async def execute(self, stmt, *args, **kwargs):
            result = await super().execute(stmt, *args, **kwargs)
            # The FIRST execute is the SELECT pick_stmt; capture it separately.
            if not self.select_sql and self.calls:
                self.select_sql = self.calls[-1]
            return result

        def scalar_one_or_none(self):
            return None

    session = _SelectCapturingSession()
    # claim_next_job calls session.execute(pick_stmt); result.scalar_one_or_none() returns None.
    # We override the FakeResult to return None from scalar_one_or_none so the function exits early.
    class _NoneResult(_FakeResult):
        def scalar_one_or_none(self):
            return None

    original_execute = session.execute

    async def _patched_execute(stmt, *args, **kwargs):
        try:
            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        except Exception:
            sql = str(stmt)
        session.calls.append(sql)
        if not session.select_sql:
            session.select_sql = sql
        return _NoneResult()

    session.execute = _patched_execute
    result = await claim_next_job(session, worker_id="W", max_attempts=3)

    assert result is None, "claim with no claimable job should return None"
    assert session.select_sql, "session.execute was never called"

    sql = session.select_sql
    # The IS NULL arm must appear in the compiled SELECT
    # PostgreSQL compiles batch_id IS NULL as "batch_id IS NULL"
    assert "BATCH_ID IS NULL" in sql.upper(), (
        f"IS NULL arm must appear in pick SELECT, got:\n{sql}"
    )
    assert "paused_at" in sql, (
        f"paused_at predicate must appear in pick SELECT, got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# Test 4 — pause_batch does NOT alter any job row (pause-claim guarantee)
# Source-level: pause_batch must only UPDATE batches, never homework_jobs
# ---------------------------------------------------------------------------

def test_pause_batch_never_touches_homework_jobs():
    """pause_batch's source must not reference homework_jobs or HomeworkJob.

    This enforces the 'never hard-cancel paid work' contract: pausing gates
    *claiming* only; in-flight work runs to completion unaffected.
    """
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.pause_batch)
    assert "HomeworkJob" not in src, (
        "pause_batch must not reference HomeworkJob — it only sets paused_at on Batch"
    )
    assert "homework_jobs" not in src.lower(), (
        "pause_batch must not touch homework_jobs table"
    )
    # Must not UPDATE homework_jobs (the docstring may mention "cancel" as a noun
    # for context — guard against SQL cancel operations, not the word itself).
    assert "HomeworkJob.status" not in src, (
        "pause_batch must not write HomeworkJob.status — pause gates claiming, never cancels"
    )


# ---------------------------------------------------------------------------
# Test 5 — Batch model has the new columns
# ---------------------------------------------------------------------------

def test_batch_model_has_pause_columns():
    """Batch ORM model must declare paused_at and paused_reason columns."""
    from app.models.batch import Batch

    assert hasattr(Batch, "paused_at"), "Batch must have paused_at column"
    assert hasattr(Batch, "paused_reason"), "Batch must have paused_reason column"


# ---------------------------------------------------------------------------
# Test 6 — claim_next_job AND-composes the pause predicate (does not replace)
# ---------------------------------------------------------------------------

def test_claim_next_job_retains_existing_predicates():
    """The pause predicate is AND-composed after extract_ok; existing predicates
    (content_ok, judge_ok, extract_ok, scheduled_at, attempts) must still be
    present in source."""
    from app.repositories import jobs as jobs_repo

    src = inspect.getsource(jobs_repo.claim_next_job)
    for predicate in ("content_ok", "judge_ok", "extract_ok", "scheduled_at", "attempts"):
        assert predicate in src, (
            f"claim_next_job source must still contain '{predicate}' after the pause patch"
        )


# ---------------------------------------------------------------------------
# Note: the reason-scoping regression (constraint #2) is covered by the
# REAL-DB integration test in
# tests/integration/test_batch_pause_reason_scope.py
# which proves the WHERE clause BITES against actual Postgres (not a mock).
# The vacuous mock-based version that lived here was removed because a mock
# with a pre-set rowcount cannot falsify a deleted WHERE clause.
# ---------------------------------------------------------------------------
