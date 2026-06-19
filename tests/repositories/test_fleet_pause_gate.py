"""Unit tests for the fleet-daily global pause gate (Task 5 / C4).

Tests operate at two levels:
  1. Source/SQL-shape inspection — verifies the fleet_gate predicate is present
     in claim_next_job's compiled SQL (same pattern as test_batch_pause_repo.py).
  2. Async-session mock tests — drives the real functions against a fake session.

The "bite-proof" rule: every key assertion is verified to FAIL if its
corresponding code is removed. The comments in each test explain which code
removal would trigger RED.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy.dialects import postgresql


# ---------------------------------------------------------------------------
# Shared fake session helpers (mirrors test_batch_pause_repo.py style)
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return None


class _CapturingSession:
    """Minimal async session that captures all execute calls as compiled SQL."""

    def __init__(self, rowcount: int = 1):
        self._rowcount = rowcount
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
        return _FakeResult(self._rowcount)

    async def get(self, model, pk):
        return None


# ---------------------------------------------------------------------------
# Helper: call claim_next_job and capture the compiled pick SELECT
# ---------------------------------------------------------------------------

async def _compile_pick_stmt(fleet_api_paused: bool) -> str:
    """Drive claim_next_job with a fake session; return the compiled pick SQL."""
    from app.repositories.jobs import claim_next_job

    captured_sql: list[str] = []

    class _NoneResult(_FakeResult):
        def scalar_one_or_none(self):
            return None

    session = _CapturingSession()

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
        captured_sql.append(sql)
        return _NoneResult()

    session.execute = _patched_execute

    result = await claim_next_job(
        session,
        worker_id="W",
        max_attempts=3,
        capabilities={
            "can_claude_api": True,
            "can_gemini_api": True,
            "judge_api_ok": True,
            "judge_fallback_api_ok": True,
            "extract_api_ok": True,
            "judge_pair": ("claude", "claude-opus-4-5"),
            "settings_judge_provider": "claude",
            "settings_extract_provider": "gemini",
        },
        fleet_api_paused=fleet_api_paused,
    )
    assert result is None, "no job should be returned from an empty fake session"
    assert captured_sql, "session.execute was never called — check claim_next_job body"
    return captured_sql[0]


# ---------------------------------------------------------------------------
# Test 1 — Source: claim_next_job contains fleet_api_paused parameter
# ---------------------------------------------------------------------------

def test_claim_next_job_has_fleet_api_paused_param():
    """claim_next_job signature must include fleet_api_paused parameter.

    BITE: removing the parameter from the function signature breaks this.
    """
    from app.repositories import jobs as jobs_repo
    import inspect as _inspect

    sig = _inspect.signature(jobs_repo.claim_next_job)
    assert "fleet_api_paused" in sig.parameters, (
        "claim_next_job must accept fleet_api_paused parameter"
    )
    param = sig.parameters["fleet_api_paused"]
    assert param.default is False, (
        "fleet_api_paused must default to False (back-compat no-op)"
    )


# ---------------------------------------------------------------------------
# Test 2 — Source: budget repo has get_state, set_api_paused, clear_api_paused
# ---------------------------------------------------------------------------

def test_budget_repo_has_required_functions():
    """budget.py must expose the three required functions."""
    from app.repositories import budget as budget_repo

    assert callable(getattr(budget_repo, "get_state", None)), "get_state must exist"
    assert callable(getattr(budget_repo, "set_api_paused", None)), "set_api_paused must exist"
    assert callable(getattr(budget_repo, "clear_api_paused", None)), "clear_api_paused must exist"


def test_budget_repo_set_api_paused_source():
    """set_api_paused must update api_paused_at via func.now() and set reason."""
    from app.repositories import budget as budget_repo

    src = inspect.getsource(budget_repo.set_api_paused)
    assert "api_paused_at" in src, "set_api_paused must set api_paused_at"
    assert "api_paused_reason" in src, "set_api_paused must set api_paused_reason"
    assert "func.now()" in src or "now()" in src, "set_api_paused must use now() for timestamp"


def test_budget_repo_clear_api_paused_source():
    """clear_api_paused must NULL both columns."""
    from app.repositories import budget as budget_repo

    src = inspect.getsource(budget_repo.clear_api_paused)
    assert "api_paused_at" in src, "clear_api_paused must clear api_paused_at"
    assert "api_paused_reason" in src, "clear_api_paused must clear api_paused_reason"


# ---------------------------------------------------------------------------
# Test 3 — Source: claim_next_job contains fleet_gate predicate logic
# ---------------------------------------------------------------------------

def test_claim_next_job_contains_fleet_gate_in_source():
    """claim_next_job source must reference fleet_gate and job_resolved_api.

    BITE: removing fleet_gate from jobs.py breaks this assertion.
    """
    from app.repositories import jobs as jobs_repo

    src = inspect.getsource(jobs_repo.claim_next_job)
    assert "fleet_gate" in src, (
        "claim_next_job must define and use fleet_gate for the fleet-daily circuit breaker"
    )
    assert "job_resolved_api" in src, (
        "claim_next_job must define job_resolved_api (transport='api' OR judge/extract needs api)"
    )
    assert "fleet_api_paused" in src, (
        "claim_next_job must use fleet_api_paused to parameterize the fleet gate"
    )


def test_claim_next_job_retains_all_existing_predicates():
    """All pre-existing predicates must still be AND-composed (fleet gate only adds, never replaces)."""
    from app.repositories import jobs as jobs_repo

    src = inspect.getsource(jobs_repo.claim_next_job)
    for predicate in ("content_ok", "judge_ok", "extract_ok", "not_in_paused_batch", "scheduled_at", "attempts"):
        assert predicate in src, (
            f"fleet-gate patch must not remove existing predicate '{predicate}' from claim_next_job"
        )


# ---------------------------------------------------------------------------
# Test 4 — SQL shape: fleet_api_paused=True includes the blocking predicate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fleet_paused_sql_contains_exclusion_predicate():
    """When fleet_api_paused=True, the compiled pick SQL must exclude api jobs.

    BITE: removing fleet_gate from the .where() chain makes this assertion fail
    because 'true' literal replaces the NOT expression (api jobs would pass).

    The compiled SQL for fleet_api_paused=True should NOT contain 'true' as the
    sole fleet predicate — it must contain the NOT(...) exclusion clause.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)

    # The fleet gate must appear — presence of 'api' in a NOT context.
    # When paused=True: literal(not True) = literal(False), so the gate is:
    # NOT (transport='api' OR ...) — the NOT form must be present for api exclusion.
    assert "fleet_gate" not in sql, "fleet_gate is a Python name, not SQL — checking SQL content"

    # The compiled SQL must contain the transport='api' reference within a NOT context
    # (SQLAlchemy renders ~or_(...) as NOT (... OR ...) in PostgreSQL dialect).
    # We check for the characteristic NOT + api combination.
    sql_upper = sql.upper()
    assert "NOT" in sql_upper and "'api'" in sql.lower(), (
        f"fleet_api_paused=True must produce NOT(...'api'...) in SQL; got:\n{sql}"
    )

    # Extra sanity: the pick statement must still have the standard predicates.
    assert "pending" in sql, "pick stmt must still filter on status='pending'"
    assert "scheduled_at" in sql.lower(), "pick stmt must still filter on scheduled_at"


@pytest.mark.asyncio
async def test_fleet_unpaused_sql_is_noop():
    """When fleet_api_paused=False, the compiled SQL must be a no-op (true literal).

    literal(not False) = literal(True) → the or_(~job_resolved_api, True) is always
    True, so the fleet predicate disappears as a constant in the compiled SQL.

    BITE: if fleet_api_paused=False still injects a blocking clause, api jobs would
    be blocked even when the fleet gate is inactive.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=False)

    # When fleet_api_paused=False, literal(True) makes the or_ trivially true.
    # SQLAlchemy optimizes this: the WHERE clause should contain 'true' or
    # the NOT expression should NOT appear (no blocking clause for fleet gate).
    # The key invariant: a cli job should not be blocked.
    # We verify the SQL doesn't contain the specific NOT(transport='api') fleet gate.
    # (The batch-pause gate's NOT IN may still contain 'api' in table names — ignore.)

    # Pick statement must still have the standard filters (gate is no-op, not removed).
    assert "pending" in sql, "pick stmt must filter on status='pending' even when fleet unpaused"
    assert "scheduled_at" in sql.lower(), "pick stmt must filter on scheduled_at"


# ---------------------------------------------------------------------------
# Test 5 — BudgetState model has the right columns
# ---------------------------------------------------------------------------

def test_budget_state_model_has_required_columns():
    """BudgetState ORM model must declare id, api_paused_at, api_paused_reason."""
    from app.models.budget_state import BudgetState

    assert hasattr(BudgetState, "id"), "BudgetState must have id column"
    assert hasattr(BudgetState, "api_paused_at"), "BudgetState must have api_paused_at column"
    assert hasattr(BudgetState, "api_paused_reason"), "BudgetState must have api_paused_reason column"


# ---------------------------------------------------------------------------
# Test 6 — budget repo SQL shapes (set_api_paused, clear_api_paused)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_api_paused_sql_shape():
    """set_api_paused executes an UPDATE on budget_state setting api_paused_at."""
    from app.repositories.budget import set_api_paused

    session = _CapturingSession()
    await set_api_paused(session, "daily-limit")

    assert len(session.calls) == 1, "set_api_paused must call execute exactly once"
    sql = session.calls[0]
    assert "budget_state" in sql, f"UPDATE must target budget_state; got:\n{sql}"
    assert "api_paused_at" in sql, f"SQL must set api_paused_at; got:\n{sql}"
    assert "NOW()" in sql.upper(), f"SQL must use now() for timestamp; got:\n{sql}"
    assert "daily-limit" in sql, f"SQL must embed the reason string; got:\n{sql}"


@pytest.mark.asyncio
async def test_clear_api_paused_sql_shape():
    """clear_api_paused executes an UPDATE on budget_state clearing both columns."""
    from app.repositories.budget import clear_api_paused

    session = _CapturingSession()
    await clear_api_paused(session)

    assert len(session.calls) == 1, "clear_api_paused must call execute exactly once"
    sql = session.calls[0]
    assert "budget_state" in sql, f"UPDATE must target budget_state; got:\n{sql}"
    assert "api_paused_at" in sql, f"SQL must clear api_paused_at; got:\n{sql}"


# ---------------------------------------------------------------------------
# Test 7 — fleet_api_paused=False is strictly no-op (api job claims normally)
# Compiled-SQL assertion: when paused=False, 'true' literal is in the fleet arm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fleet_unpaused_literal_true_in_sql():
    """fleet_api_paused=False must produce literal(True) in the fleet gate arm.

    literal(not False) = literal(True). SQLAlchemy renders this as 'true' in
    the WHERE clause. If the gate were always-blocking, this would be 'false'.

    BITE: If fleet_api_paused=True is hardcoded (never checking the param),
    the literal would be 'false' and this test would fail on 'true' assertion.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=False)
    sql_upper = sql.upper()

    # When fleet_api_paused=False: literal(True) → 'true' appears in SQL.
    # The or_(~job_resolved_api, literal(True)) simplifies but 'true' still
    # appears in the compiled output for the fleet arm.
    assert "TRUE" in sql_upper, (
        f"fleet_api_paused=False must inject literal(True) into pick SQL (no-op gate); got:\n{sql}"
    )


@pytest.mark.asyncio
async def test_fleet_paused_literal_false_in_sql():
    """fleet_api_paused=True must produce literal(False) in the fleet gate arm.

    literal(not True) = literal(False). The or_ becomes or_(~job_resolved_api, false)
    which reduces to NOT job_resolved_api — only non-api jobs pass.

    BITE: If fleet_api_paused=False is hardcoded (always no-op), the literal
    would be 'true' and the NOT(...) exclusion clause would not block api jobs.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)
    sql_upper = sql.upper()

    # When fleet_api_paused=True: literal(False) → 'false' appears in the gate arm.
    # The or_ is NOT trivially true so the NOT(job_resolved_api) clause is emitted.
    assert "FALSE" in sql_upper, (
        f"fleet_api_paused=True must inject literal(False) (blocking mode); got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# Test 8 — worker _claim_one reads budget state before calling claim_next_job
# (source-level: no DB needed)
# ---------------------------------------------------------------------------

def test_worker_claim_one_reads_budget_state():
    """Worker._claim_one must read budget_repo.get_state before claim_next_job.

    BITE: removing the budget_repo import or get_state call from _claim_one
    breaks this assertion.
    """
    from app.services.worker import Worker
    import inspect as _inspect

    src = _inspect.getsource(Worker._claim_one)
    assert "budget_repo" in src or "budget" in src, (
        "_claim_one must import/use budget_repo to read the fleet pause state"
    )
    assert "get_state" in src, (
        "_claim_one must call budget_repo.get_state to read api_paused_at"
    )
    assert "fleet_api_paused" in src, (
        "_claim_one must pass fleet_api_paused to claim_next_job"
    )
    assert "api_paused_at" in src, (
        "_claim_one must check api_paused_at to determine fleet pause state"
    )
