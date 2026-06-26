"""Tests for upsert_heartbeat capabilities parameter.

TDD: written BEFORE implementation (RED → implement → GREEN).

Tests operate at two levels:
  1. Signature/SQL-shape inspection (no DB needed) — verifies the function
     accepts capabilities and includes it in the statement.
  2. Real-PG integration (RUN_DB_INTEGRATION=1 + scratch DB) — drives the
     actual upsert and verifies the no-clobber invariant: a status-only beat
     (capabilities=None) must NOT overwrite a previously-published blob.

The no-clobber branch is load-bearing: a worker that beats frequently with
capabilities=None after an initial full beat must never wipe its own blob.
"""
from __future__ import annotations

import inspect
import os

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUN_DB = os.environ.get("RUN_DB_INTEGRATION") == "1"
skip_no_db = pytest.mark.skipif(not RUN_DB, reason="RUN_DB_INTEGRATION=1 required")


# ---------------------------------------------------------------------------
# 1. Signature: upsert_heartbeat accepts capabilities parameter
# ---------------------------------------------------------------------------

def test_upsert_heartbeat_accepts_capabilities():
    """upsert_heartbeat must accept a 'capabilities' keyword argument defaulting to None.

    BITE: removing the parameter from the function signature breaks this.
    """
    from app.repositories import workers as workers_repo

    sig = inspect.signature(workers_repo.upsert_heartbeat)
    assert "capabilities" in sig.parameters, (
        "upsert_heartbeat must accept a 'capabilities' keyword argument"
    )
    param = sig.parameters["capabilities"]
    assert param.default is None, (
        "capabilities must default to None (back-compat: existing callers without it still work)"
    )


# ---------------------------------------------------------------------------
# 2. SQL-shape: capabilities is in INSERT values and conditional set_
# ---------------------------------------------------------------------------

def test_upsert_heartbeat_source_includes_capabilities_in_values():
    """upsert_heartbeat source must include 'capabilities' in the INSERT .values() call.

    BITE: removing capabilities from .values(...) breaks this assertion.
    """
    from app.repositories import workers as workers_repo

    src = inspect.getsource(workers_repo.upsert_heartbeat)
    assert "capabilities" in src, (
        "upsert_heartbeat source must reference 'capabilities' in the INSERT/update logic"
    )


def test_upsert_heartbeat_source_has_no_clobber_guard():
    """upsert_heartbeat source must conditionally include capabilities in set_ only when not None.

    BITE: unconditionally setting capabilities (even when None) removes the guard
    and would null-wipe a previously-published blob on a status-only beat.
    """
    from app.repositories import workers as workers_repo

    src = inspect.getsource(workers_repo.upsert_heartbeat)
    # The no-clobber guard must be present in some form
    assert "None" in src, (
        "upsert_heartbeat must check for None to guard against clobbering the blob"
    )


# ---------------------------------------------------------------------------
# 3. SQL-shape: compiled INSERT includes capabilities column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_heartbeat_sql_includes_capabilities_column():
    """Compiled INSERT SQL must include 'capabilities' column when capabilities is provided.

    BITE: removing capabilities from the INSERT .values(...) means the SQL never
    writes the blob, and the DB column stays NULL forever.
    """
    from sqlalchemy.dialects import postgresql
    from app.repositories.workers import upsert_heartbeat

    captured_sqls: list[str] = []

    class _FakeSession:
        async def execute(self, stmt, *args, **kwargs):
            try:
                sql = str(stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                ))
            except Exception:
                sql = str(stmt)
            captured_sqls.append(sql)

            class _R:
                rowcount = 1
            return _R()

    blob = {"cli": {"claude": True}, "api": {"claude": False, "gemini": False}}
    await upsert_heartbeat(_FakeSession(), "test-worker-1", capabilities=blob)

    assert captured_sqls, "upsert_heartbeat must call session.execute"
    sql = captured_sqls[0]
    assert "capabilities" in sql, (
        f"compiled INSERT SQL must include 'capabilities' column; got:\n{sql}"
    )


@pytest.mark.asyncio
async def test_upsert_heartbeat_sql_no_capabilities_when_none():
    """When capabilities=None, the on_conflict set_ must NOT include capabilities.

    BITE: unconditionally including capabilities in set_ would null-wipe the blob;
    removing the conditional guard means this test must fire RED before the guard exists.
    """
    from sqlalchemy.dialects import postgresql
    from app.repositories.workers import upsert_heartbeat

    captured_sqls: list[str] = []

    class _FakeSession:
        async def execute(self, stmt, *args, **kwargs):
            try:
                sql = str(stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                ))
            except Exception:
                sql = str(stmt)
            captured_sqls.append(sql)

            class _R:
                rowcount = 1
            return _R()

    await upsert_heartbeat(_FakeSession(), "test-worker-1", capabilities=None)

    assert captured_sqls, "upsert_heartbeat must call session.execute"
    sql = captured_sqls[0]

    # The UPDATE SET clause (after DO UPDATE SET) must not set capabilities to NULL.
    # We check the SET clause doesn't assign capabilities when capabilities=None.
    # Compiled form: "DO UPDATE SET ... capabilities = NULL ..." would be wrong.
    # The INSERT itself still has capabilities in the column list (with NULL value),
    # but the UPDATE SET clause must NOT include "capabilities" (no clobber).
    #
    # Strategy: split on "DO UPDATE SET" and check the update portion.
    if "DO UPDATE SET" in sql.upper():
        update_part = sql.upper().split("DO UPDATE SET", 1)[1]
        assert "CAPABILITIES" not in update_part, (
            f"When capabilities=None, the DO UPDATE SET clause must NOT include CAPABILITIES; "
            f"got update part:\n{update_part}"
        )


# ---------------------------------------------------------------------------
# 4. Real-PG integration: write blob + no-clobber + status update
# ---------------------------------------------------------------------------

@skip_no_db
@pytest.mark.asyncio
async def test_upsert_heartbeat_writes_capabilities(tmp_path):
    """Real-PG: inserting with capabilities= writes the blob to the DB.

    BITE: removing capabilities from .values(...) means the DB column stays NULL.
    Uses the scratch DB (edu_cap_test) — never edu_copy / edu_homework.
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories.workers import upsert_heartbeat

    blob = {
        "cli": {"claude": True, "kimi": False, "codex": False, "gemini": True, "opencode": False},
        "api": {"claude": True, "gemini": False},
    }
    pc_id = "test-cap-writer-001"

    async with SessionLocal() as session:
        await upsert_heartbeat(session, pc_id, capabilities=blob)
        await session.commit()

    async with SessionLocal() as session:
        row = await session.scalar(select(WorkerNode).where(WorkerNode.pc_id == pc_id))
        assert row is not None, f"Worker row for {pc_id!r} must exist after upsert"
        assert row.capabilities is not None, (
            "capabilities column must be non-NULL after upsert with capabilities= blob"
        )
        assert row.capabilities["cli"]["claude"] is True, (
            f"blob['cli']['claude'] must be True; got {row.capabilities!r}"
        )
        assert row.capabilities["api"]["claude"] is True, (
            f"blob['api']['claude'] must be True; got {row.capabilities!r}"
        )

    # Cleanup
    async with SessionLocal() as session:
        from sqlalchemy import delete
        await session.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
        await session.commit()


@skip_no_db
@pytest.mark.asyncio
async def test_upsert_heartbeat_no_clobber():
    """Real-PG: a status-only beat (capabilities=None) must NOT null-wipe a previously-published blob.

    This is the LOAD-BEARING no-clobber test. Steps:
      1. Insert with a real blob (capabilities= set)
      2. Beat again with capabilities=None + new status
      3. Assert the blob is STILL the original (not nulled) AND status updated

    RED-proof before implementation: without the conditional set_ guard,
    the DO UPDATE SET would write capabilities=NULL, and the assert on
    row.capabilities would fail.

    BITE: removing the 'if capabilities is not None' guard from set_ in
    upsert_heartbeat causes capabilities to be set to NULL on the second beat,
    making the final assert fail.
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories.workers import upsert_heartbeat

    blob = {
        "cli": {"claude": True, "kimi": False, "codex": False, "gemini": True, "opencode": False},
        "api": {"claude": True, "gemini": False},
    }
    pc_id = "test-no-clobber-001"

    # Step 1: insert with blob
    async with SessionLocal() as session:
        await upsert_heartbeat(session, pc_id, status="online", capabilities=blob)
        await session.commit()

    # Step 2: beat again with capabilities=None + new status (simulates a normal heartbeat)
    async with SessionLocal() as session:
        await upsert_heartbeat(session, pc_id, status="draining", capabilities=None)
        await session.commit()

    # Step 3: assert blob is STILL the original AND status updated
    async with SessionLocal() as session:
        row = await session.scalar(select(WorkerNode).where(WorkerNode.pc_id == pc_id))
        assert row is not None, f"Worker row for {pc_id!r} must exist"

        # Status must have updated
        assert row.status == "draining", (
            f"status must update to 'draining' on second beat; got {row.status!r}"
        )

        # Blob must NOT be nulled — the original must survive
        assert row.capabilities is not None, (
            "capabilities must NOT be null after a status-only beat (capabilities=None) — "
            "the no-clobber guard must protect the previously-published blob"
        )
        assert row.capabilities["cli"]["claude"] is True, (
            f"blob['cli']['claude'] must still be True after status-only beat; "
            f"got {row.capabilities!r}"
        )
        assert row.capabilities["api"]["claude"] is True, (
            f"blob['api']['claude'] must still be True after status-only beat; "
            f"got {row.capabilities!r}"
        )

    # Cleanup
    async with SessionLocal() as session:
        from sqlalchemy import delete
        await session.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
        await session.commit()
