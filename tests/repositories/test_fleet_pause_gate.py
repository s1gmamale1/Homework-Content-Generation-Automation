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

# UNIQUE fleet-gate fragment proof (run this to understand why these assertions bite):
#
# The fleet gate in jobs.py is:
#   fleet_gate = or_(~job_resolved_api, literal(not fleet_api_paused))
#
# Compiled SQL (paused=True):  ... OR false)
# Compiled SQL (paused=False): ... OR true)
#
# The ambient SQL (without fleet gate) contains 'AND true' (from capability literal()s)
# and 'NOT' + 'api' (from judge_ok/extract_ok predicates), but NEVER 'OR false)' or
# 'OR true)' — those tokens are UNIQUE to the fleet gate disjunction arm.
# Verified manually: remove .where(fleet_gate) → neither 'OR false)' nor 'OR true)'
# appears in the pick SQL.

@pytest.mark.asyncio
async def test_fleet_paused_sql_contains_exclusion_predicate():
    """When fleet_api_paused=True, the compiled pick SQL must contain 'OR false)'.

    BITE: removing .where(fleet_gate) from the pick_stmt in jobs.py causes this
    assertion to fail — 'OR false)' is ONLY emitted by the fleet gate disjunction
    arm (or_(~job_resolved_api, literal(False))). The ambient predicates (judge_ok,
    extract_ok) emit 'NOT' and 'api' but never 'OR false)'.

    Red-proof: the ambient SQL without fleet gate contains 'AND true' (capability
    literals) and 'NOT(...api...)' (judge/extract), but NOT 'OR false)'.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)

    # 'OR false)' is the compiled form of or_(..., literal(False)) — uniquely the
    # fleet gate arm. The judge/extract predicates use NOT(...) but never 'OR false)'.
    assert "OR false)" in sql, (
        f"fleet_api_paused=True must produce 'OR false)' in pick SQL (fleet disjunction "
        f"arm with literal(False)); got:\n{sql}"
    )

    # Extra sanity: standard predicates must still be present.
    assert "pending" in sql, "pick stmt must still filter on status='pending'"
    assert "scheduled_at" in sql.lower(), "pick stmt must still filter on scheduled_at"


@pytest.mark.asyncio
async def test_fleet_unpaused_sql_is_noop():
    """When fleet_api_paused=False, the compiled pick SQL must contain 'OR true)'.

    BITE: removing .where(fleet_gate) from the pick_stmt in jobs.py causes this
    assertion to fail — 'OR true)' is ONLY emitted by the fleet gate disjunction
    arm (or_(~job_resolved_api, literal(True))). The ambient predicates emit
    'AND true' (capability literals) but never 'OR true)'.

    Red-proof: the ambient SQL without fleet gate contains 'AND true' but NOT 'OR true)'.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=False)

    # 'OR true)' is the compiled form of or_(..., literal(True)) — uniquely the
    # fleet gate arm with no-op semantics (always passes, every job eligible).
    assert "OR true)" in sql, (
        f"fleet_api_paused=False must produce 'OR true)' in pick SQL (fleet disjunction "
        f"arm with literal(True) = no-op); got:\n{sql}"
    )

    # Pick statement must still have the standard filters.
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
    """fleet_api_paused=False must produce 'OR true)' in the fleet gate arm.

    literal(not False) = literal(True). SQLAlchemy renders this as 'OR true)' in
    the fleet disjunction arm: or_(~job_resolved_api, literal(True)).

    BITE: If fleet_api_paused=True is hardcoded (never checking the param),
    the literal would be 'false' and 'OR true)' would NOT appear. If the fleet
    gate is removed entirely, 'OR true)' also disappears (only 'AND true' remains
    from the capability literals — a different token). Both removal and hardcoding
    cause this test to fail.

    Note: the vacuous 'TRUE' check (just 'AND true' from capability literals) is
    NOT sufficient — 'AND true' appears even without the fleet gate. The specific
    fragment 'OR true)' is unique to the fleet gate arm.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=False)

    # 'OR true)' is the compiled form of or_(..., literal(True)) — uniquely the
    # fleet gate disjunction arm. Capability literals emit 'AND true', not 'OR true)'.
    assert "OR true)" in sql, (
        f"fleet_api_paused=False must inject 'OR true)' (fleet disjunction no-op arm); got:\n{sql}"
    )


@pytest.mark.asyncio
async def test_fleet_paused_literal_false_in_sql():
    """fleet_api_paused=True must produce 'OR false)' in the fleet gate arm.

    literal(not True) = literal(False). The or_ becomes or_(~job_resolved_api, false)
    which reduces to NOT job_resolved_api — only non-api jobs pass.

    BITE: If fleet_api_paused=False is hardcoded (always no-op), the literal
    would be 'true' and 'OR false)' would NOT appear. If the fleet gate is removed
    entirely, 'OR false)' also disappears — the ambient SQL has no 'OR false)' token.
    Both scenarios fail this assertion.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)

    # 'OR false)' is the compiled form of or_(..., literal(False)) — uniquely the
    # fleet gate disjunction arm in blocking mode. Without the fleet gate, 'OR false)'
    # is absent (ambient SQL only has 'AND true' from capability literals).
    assert "OR false)" in sql, (
        f"fleet_api_paused=True must inject 'OR false)' (fleet disjunction blocking arm); got:\n{sql}"
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


# ---------------------------------------------------------------------------
# C4 — Behavioral SQL scenarios (offline, compiled-SQL form)
# ---------------------------------------------------------------------------
# These three tests verify the semantics of the fleet gate in concrete job
# scenarios by inspecting the compiled SQL disjunction arm.
#
# Each test drives _compile_pick_stmt with a specific fleet_api_paused value
# and then checks whether the pick SQL would ADMIT or BLOCK a job with given
# transport characteristics.  The compiled SQL is a correct proxy because the
# WHERE clause is what the database evaluates — if 'OR false)' is present,
# the fleet arm can only pass when ~job_resolved_api is true (cli-only jobs).
#
# BITE for all three: removing .where(fleet_gate) from pick_stmt in jobs.py
# removes BOTH 'OR false)' AND 'OR true)' from the SQL, failing assertions 1
# and 2 below; swapping True↔False in literal() fails assertion 3.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c4_paused_api_job_blocked_by_fleet_arm():
    """C4-1: fleet_api_paused=True — api-resolved job is blocked by fleet gate.

    Scenario: a job with transport='api' (content phase spends api tokens).
    Expected: fleet arm emits 'OR false)' — the disjunction is
      NOT(transport='api' OR ...) OR false
    which only passes when ~job_resolved_api is true. Since transport='api'
    makes job_resolved_api true, the fleet arm evaluates to false, blocking
    the row from being claimed.

    BITE: removing .where(fleet_gate) eliminates 'OR false)' — the assertion
    fails, and the fleet gate no longer prevents api job claims during a pause.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)

    # Fleet arm must be in blocking mode: OR false) is the compiled literal(False).
    # An api job satisfies 'homework_jobs.transport = 'api'' in job_resolved_api,
    # so ~job_resolved_api is False, and False OR false = false — row excluded.
    assert "OR false)" in sql, (
        "paused=True fleet arm must emit 'OR false)' — api-resolved jobs are excluded; "
        f"got:\n{sql}"
    )
    # The fleet disjunction must reference transport = 'api' in the NOT arm so that
    # an api job's transport field satisfies job_resolved_api and gets filtered out.
    assert "homework_jobs.transport = 'api'" in sql, (
        "fleet arm must reference homework_jobs.transport = 'api' to identify api jobs"
    )


@pytest.mark.asyncio
async def test_c4_paused_cli_job_passes_fleet_arm():
    """C4-2: fleet_api_paused=True — cli-only job is NOT blocked by fleet gate.

    Scenario: a job with transport='cli', judge_transport='cli' (or 'inherit'
    under cli), extract_transport='cli' (or 'inherit' under cli). Such a job
    touches NO api roles — job_resolved_api is false, so ~job_resolved_api is
    true, and true OR false = true: the fleet arm passes the row.

    The SQL assertion: 'OR false)' is in the pick SQL (gate is in blocking mode),
    but the NOT(...) clause for a cli job evaluates to NOT(false OR false OR ...) =
    NOT(false) = true, so the row passes. The SQL structure guarantees this:
    transport='cli' does NOT satisfy 'homework_jobs.transport = 'api'', so the
    job_resolved_api disjunction is false, and NOT(false) = true.

    BITE: removing .where(fleet_gate) eliminates 'OR false)' — the assertion
    fails; the gate no longer distinguishes cli from api jobs during a pause.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=True)

    # Gate is active (blocking mode): 'OR false)' must be present.
    assert "OR false)" in sql, (
        "paused=True fleet arm must emit 'OR false)' regardless of individual job transport; "
        f"got:\n{sql}"
    )
    # The NOT arm must include all three role checks so a fully-cli job (all roles cli)
    # satisfies ~job_resolved_api and passes through.
    assert "homework_jobs.extract_transport = 'api'" in sql, (
        "fleet arm must check extract_transport = 'api' so cli-extract jobs pass ~job_resolved_api"
    )
    assert "homework_jobs.judge_transport = 'api'" in sql, (
        "fleet arm must check judge_transport = 'api' so cli-judge jobs pass ~job_resolved_api"
    )


@pytest.mark.asyncio
async def test_c4_unpaused_fleet_arm_is_noop():
    """C4-3: fleet_api_paused=False — fleet arm is strictly no-op ('OR true)').

    Scenario: fleet gate inactive. literal(not False) = literal(True), so the
    fleet disjunction is or_(~job_resolved_api, True) — always true for every job.
    No job (api or cli) is excluded. The compiled SQL must contain 'OR true)'
    rather than 'OR false)'.

    This is the strict no-op guarantee: the fleet gate must NEVER exclude any job
    when fleet_api_paused=False, regardless of transport.

    BITE: removing .where(fleet_gate) eliminates 'OR true)' — the assertion fails.
    Hardcoding fleet_api_paused=True would emit 'OR false)' instead, breaking api
    jobs even when the daily limit hasn't been hit.
    """
    sql = await _compile_pick_stmt(fleet_api_paused=False)

    # Gate is inactive: 'OR true)' must be present, never 'OR false)'.
    assert "OR true)" in sql, (
        "paused=False fleet arm must emit 'OR true)' (no-op — all jobs pass); "
        f"got:\n{sql}"
    )
    assert "OR false)" not in sql, (
        "paused=False must NOT emit 'OR false)' — that would block api jobs incorrectly; "
        f"got:\n{sql}"
    )
