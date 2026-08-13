"""The cap-pause guard must live in the SQL, not only in the caller.

`batches.paused_at` / `budget_state.api_paused_at` are fleet-wide flags decided
from a per-host env cap. The budget monitor decides and logs; these repo calls
ENFORCE — so a second caller (or two workers reconciling in the same tick)
cannot slip a looser cap past the rule.

Compiled-SQL shape only; the behavioural proof against real Postgres is
tests/integration/test_cap_pause_guard.py.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy.dialects import postgresql


class _FakeResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount


class _CapturingSession:
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


# ---------------------------------------------------------------------------
# The pause writes its provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_batch_persists_cap_and_deciding_worker():
    from app.repositories.batches import pause_batch

    session = _CapturingSession()
    await pause_batch(
        session, uuid.uuid4(), "batch-cap",
        cap_usd=50.0, paused_by="host-a:4242@abc1234",
    )

    sql = session.calls[0]
    assert "paused_cap_usd" in sql, "the pause must persist the cap that tripped it"
    assert "50.0" in sql, f"the effective cap value must be written, got:\n{sql}"
    assert "paused_by" in sql, "the pause must persist the deciding worker"
    assert "host-a:4242@abc1234" in sql, f"worker id must be written, got:\n{sql}"


@pytest.mark.asyncio
async def test_manual_pause_leaves_provenance_null():
    """The operator pause (POST /jobs/batch/{id}/pause) has no cap behind it."""
    from app.repositories.batches import pause_batch

    session = _CapturingSession()
    await pause_batch(session, uuid.uuid4(), "manual")

    sql = session.calls[0]
    assert "paused_cap_usd=NULL" in sql.replace(" ", ""), (
        f"a manual pause must not claim a cap, got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# The clear is guarded in SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_cap_pause_sql_carries_the_entitlement_guard():
    from app.repositories.batches import clear_cap_pause

    session = _CapturingSession()
    await clear_cap_pause(
        session, uuid.uuid4(), reason="batch-cap",
        worker_cap_usd=50.0, worker_host="host-a",
    )

    sql = session.calls[0]
    assert "paused_reason = 'batch-cap'" in sql, (
        f"the clear must stay reason-scoped, got:\n{sql}"
    )
    assert "paused_cap_usd IS NULL" in sql, (
        f"pre-0062 pauses (no provenance) must stay clearable, got:\n{sql}"
    )
    assert "split_part" in sql, (
        f"the deciding HOST must be able to revise its own decision, got:\n{sql}"
    )
    assert "paused_cap_usd >= 50.0" in sql, (
        f"a clear is only allowed when this worker is at least as strict, got:\n{sql}"
    )


@pytest.mark.asyncio
async def test_clear_cap_pause_with_the_cap_disabled_has_no_cap_arm():
    """A worker with the cap turned off holds the LOOSEST setting there is; it
    must not qualify to clear somebody else's pause by cap comparison (only as
    the deciding host, or for a provenance-less row)."""
    from app.repositories.batches import clear_cap_pause

    session = _CapturingSession()
    await clear_cap_pause(
        session, uuid.uuid4(), reason="batch-cap",
        worker_cap_usd=0.0, worker_host="host-a",
    )

    sql = session.calls[0]
    assert "paused_cap_usd >=" not in sql, (
        f"cap=0 means no ceiling — it must never satisfy the strictness arm, got:\n{sql}"
    )
    assert "split_part" in sql


@pytest.mark.asyncio
async def test_clear_cap_pause_reports_whether_it_bit():
    from app.repositories.batches import clear_cap_pause

    cleared = await clear_cap_pause(
        _CapturingSession(rowcount=1), uuid.uuid4(), reason="batch-cap",
        worker_cap_usd=50.0, worker_host="host-a",
    )
    refused = await clear_cap_pause(
        _CapturingSession(rowcount=0), uuid.uuid4(), reason="batch-cap",
        worker_cap_usd=50.0, worker_host="host-a",
    )
    assert cleared is True
    assert refused is False, (
        "a guarded clear that matched no row must report False so the caller "
        "does not log a lift that never happened"
    )


@pytest.mark.asyncio
async def test_clear_api_pause_if_entitled_sql_carries_the_same_guard():
    from app.repositories.budget import clear_api_pause_if_entitled

    session = _CapturingSession()
    await clear_api_pause_if_entitled(
        session, reason="fleet-daily-cap",
        worker_cap_usd=50.0, worker_host="host-a",
    )

    sql = session.calls[0]
    assert "api_paused_reason = 'fleet-daily-cap'" in sql
    assert "api_paused_cap_usd IS NULL" in sql
    assert "split_part" in sql
    assert "api_paused_cap_usd >= 50.0" in sql


@pytest.mark.asyncio
async def test_set_api_paused_keeps_the_stricter_claimant():
    """While the fleet pause is held under a stricter cap, a looser worker's
    re-stamp must not take ownership — that would hand it the right to clear a
    decision it never made."""
    from app.repositories.budget import set_api_paused

    session = _CapturingSession()
    await set_api_paused(
        session, "fleet-daily-cap", cap_usd=2000.0, paused_by="host-b:1@sha"
    )

    sql = session.calls[0]
    assert "CASE" in sql.upper(), (
        f"the provenance columns must be conditional, not a blind overwrite, got:\n{sql}"
    )
    assert "api_paused_cap_usd <= 2000.0" in sql, (
        f"ownership must only move when the newcomer is at least as strict, got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# The unguarded operator primitives stay unguarded (and clean up provenance)
# ---------------------------------------------------------------------------


def test_operator_unpause_stays_unconditional():
    """A human must always be able to release a batch the fleet is holding."""
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.unpause_batch)
    assert "worker_cap_usd" not in src, (
        "unpause_batch is the operator escape hatch — it must not grow a cap guard"
    )
    assert "paused_cap_usd=None" in src.replace(" ", ""), (
        "a manual unpause must also clear the stale provenance"
    )


def test_models_carry_the_provenance_columns():
    from app.models.batch import Batch
    from app.models.budget_state import BudgetState

    assert hasattr(Batch, "paused_cap_usd")
    assert hasattr(Batch, "paused_by")
    assert hasattr(BudgetState, "api_paused_cap_usd")
    assert hasattr(BudgetState, "api_paused_by")


def test_migration_0062_adds_the_provenance_columns():
    """Schema change must be a real migration, chained after the current head."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "0062_cap_pause_provenance.py"
    )
    src = path.read_text(encoding="utf-8")
    for col in ("paused_cap_usd", "paused_by", "api_paused_cap_usd", "api_paused_by"):
        assert col in src, f"migration must add {col}"
    assert 'down_revision = "0061_credential_slot_index"' in src, (
        "0062 must chain onto the migration head it was written against"
    )
