"""Tests for aggregate_fleet_capability — union of caps across online workers.

TDD: written BEFORE implementation (RED → implement → GREEN).

All DB tests are real-PG integration (RUN_DB_INTEGRATION=1 + scratch DB).
The scratch-DB recipe:
    createdb -U macmini5 edu_cap_test
    export DATABASE_URL="postgresql+asyncpg://macmini5@localhost:5432/edu_cap_test"
    export RUN_DB_INTEGRATION=1
    uv run --extra dev alembic upgrade head
    uv run --extra dev python -m pytest tests/repositories/test_aggregate_fleet_capability.py -q
    dropdb -U macmini5 edu_cap_test

Four cases covered:
  a) 0 workers  → online=False, workers_online=0, cli={}, api={}
  b) 2 online workers with disjoint caps (one cli-claude only, one api-gemini only)
     → union has BOTH true, workers_online=2
  c) stale worker excluded from union (old heartbeat outside staleness window)
  d) NULL-capabilities online worker counts toward workers_online but adds no true flags
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUN_DB = os.environ.get("RUN_DB_INTEGRATION") == "1"
skip_no_db = pytest.mark.skipif(not RUN_DB, reason="RUN_DB_INTEGRATION=1 required")

STALE_AFTER = 60  # seconds used in all tests


# ---------------------------------------------------------------------------
# Shared fixture: unique pc_id prefix per test to avoid cross-test pollution
# ---------------------------------------------------------------------------


def _pc(label: str, test_name: str) -> str:
    """Build a deterministic, test-scoped pc_id that's easy to clean up."""
    return f"agg-cap-test-{test_name}-{label}"


async def _insert_worker(
    session,
    pc_id: str,
    *,
    heartbeat: datetime,
    capabilities: dict | None = None,
    status: str = "online",
) -> None:
    """Direct INSERT of a WorkerNode row with an explicit heartbeat timestamp.

    We bypass upsert_heartbeat (which stamps DB-now) so we can control the
    exact heartbeat value — necessary for the stale-worker test case.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.worker import WorkerNode

    stmt = pg_insert(WorkerNode).values(
        pc_id=pc_id,
        last_heartbeat=heartbeat,
        status=status,
        capabilities=capabilities,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_={
            "last_heartbeat": heartbeat,
            "status": status,
            "capabilities": capabilities,
        },
    )
    await session.execute(stmt)


async def _delete_workers(session, *pc_ids: str) -> None:
    from sqlalchemy import delete
    from app.models.worker import WorkerNode

    await session.execute(delete(WorkerNode).where(WorkerNode.pc_id.in_(pc_ids)))


# ---------------------------------------------------------------------------
# (a) Zero workers → online=False
# ---------------------------------------------------------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_agg_zero_workers_returns_offline():
    """With no worker rows at all, aggregate_fleet_capability must return the
    fail-open dict: online=False, workers_online=0, cli={}, api={}.

    BITE: returning online=True or workers_online>0 when the table is empty
    would cause the launcher to think there is capacity when there is none.
    """
    from app.db import SessionLocal
    from app.repositories.workers import aggregate_fleet_capability

    async with SessionLocal() as session:
        result = await aggregate_fleet_capability(session, stale_after_seconds=STALE_AFTER)

    assert result == {"online": False, "workers_online": 0, "cli": {}, "api": {}}, (
        f"Expected fail-open shape for 0 workers; got {result!r}"
    )


# ---------------------------------------------------------------------------
# (b) Two online workers with disjoint caps → union has BOTH
# ---------------------------------------------------------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_agg_disjoint_caps_union():
    """Two online workers with disjoint capabilities produce the correct union.

    Worker A: cli.claude=True only
    Worker B: api.gemini=True only

    Expected: cli.claude=True AND api.gemini=True, workers_online=2, online=True.

    BITE: returning only one worker's caps (last-write-wins instead of union)
    would miss the other worker's true flag.
    """
    from app.db import SessionLocal
    from app.repositories.workers import aggregate_fleet_capability

    now_utc = datetime.now(timezone.utc)
    test_name = "disjoint"
    pc_a = _pc("A", test_name)
    pc_b = _pc("B", test_name)

    async with SessionLocal() as session:
        await _insert_worker(
            session,
            pc_a,
            heartbeat=now_utc,
            capabilities={
                "cli": {"claude": True, "kimi": False, "codex": False, "gemini": False, "opencode": False},
                "api": {"claude": False, "gemini": False},
            },
        )
        await _insert_worker(
            session,
            pc_b,
            heartbeat=now_utc,
            capabilities={
                "cli": {"claude": False, "kimi": False, "codex": False, "gemini": False, "opencode": False},
                "api": {"claude": False, "gemini": True},
            },
        )
        await session.commit()

    try:
        async with SessionLocal() as session:
            result = await aggregate_fleet_capability(session, stale_after_seconds=STALE_AFTER)

        assert result["online"] is True, f"online must be True with 2 workers; got {result!r}"
        assert result["workers_online"] == 2, (
            f"workers_online must be 2; got {result['workers_online']!r}"
        )
        # Union: cli.claude from worker A
        assert result["cli"].get("claude") is True, (
            f"cli.claude must be True (from worker A); got cli={result['cli']!r}"
        )
        # Union: api.gemini from worker B
        assert result["api"].get("gemini") is True, (
            f"api.gemini must be True (from worker B); got api={result['api']!r}"
        )
        # Worker A's api.claude was False, worker B's api.claude was False → stays False
        assert result["api"].get("claude") is False, (
            f"api.claude must be False (both workers False); got api={result['api']!r}"
        )
    finally:
        async with SessionLocal() as session:
            await _delete_workers(session, pc_a, pc_b)
            await session.commit()


# ---------------------------------------------------------------------------
# (c) Stale worker excluded from union
# ---------------------------------------------------------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_agg_stale_worker_excluded():
    """A stale worker (heartbeat older than stale_after_seconds) is NOT included
    in the union.

    Setup:
      - One FRESH worker with NO capable flags (all cli/api=False)
      - One STALE worker with cli.claude=True

    Expected: cli.claude is missing or False (stale worker's True must not leak);
    workers_online=1 (only the fresh worker counts).

    BITE: if the staleness filter is absent, cli.claude would be True (stale leak).
    """
    from app.db import SessionLocal
    from app.repositories.workers import aggregate_fleet_capability

    now_utc = datetime.now(timezone.utc)
    stale_heartbeat = now_utc - timedelta(seconds=STALE_AFTER + 120)  # well outside window
    test_name = "stale"
    pc_fresh = _pc("fresh", test_name)
    pc_stale = _pc("stale", test_name)

    async with SessionLocal() as session:
        # Fresh worker: online but all-False caps
        await _insert_worker(
            session,
            pc_fresh,
            heartbeat=now_utc,
            capabilities={
                "cli": {"claude": False, "gemini": False},
                "api": {"claude": False, "gemini": False},
            },
        )
        # Stale worker: would contribute cli.claude=True but is outside the window
        await _insert_worker(
            session,
            pc_stale,
            heartbeat=stale_heartbeat,
            capabilities={
                "cli": {"claude": True},
                "api": {},
            },
        )
        await session.commit()

    try:
        async with SessionLocal() as session:
            result = await aggregate_fleet_capability(session, stale_after_seconds=STALE_AFTER)

        assert result["online"] is True, f"online must be True (fresh worker present); got {result!r}"
        assert result["workers_online"] == 1, (
            f"workers_online must be 1 (stale excluded); got {result['workers_online']!r}"
        )
        # The stale worker's cli.claude=True must NOT appear in the union
        assert result["cli"].get("claude") is not True, (
            f"cli.claude must NOT be True — stale worker must be excluded; "
            f"got cli={result['cli']!r}"
        )
    finally:
        async with SessionLocal() as session:
            await _delete_workers(session, pc_fresh, pc_stale)
            await session.commit()


# ---------------------------------------------------------------------------
# (d) NULL-capabilities online worker counts toward workers_online but no flags
# ---------------------------------------------------------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_agg_null_caps_worker_counts_but_no_flags():
    """An online worker with capabilities=NULL counts toward workers_online
    (it IS online; fail-open banner fires only at zero) but contributes no
    true flags to cli/api.

    BITE: if a NULL-caps worker causes a KeyError or is excluded from the
    workers_online count, either the crash test or the count assert fires.
    """
    from app.db import SessionLocal
    from app.repositories.workers import aggregate_fleet_capability

    now_utc = datetime.now(timezone.utc)
    test_name = "nullcaps"
    pc_null = _pc("null", test_name)
    pc_capable = _pc("capable", test_name)

    async with SessionLocal() as session:
        # NULL-capabilities worker
        await _insert_worker(
            session,
            pc_null,
            heartbeat=now_utc,
            capabilities=None,
        )
        # Second worker with one real capability
        await _insert_worker(
            session,
            pc_capable,
            heartbeat=now_utc,
            capabilities={
                "cli": {"kimi": True},
                "api": {},
            },
        )
        await session.commit()

    try:
        async with SessionLocal() as session:
            result = await aggregate_fleet_capability(session, stale_after_seconds=STALE_AFTER)

        assert result["online"] is True, f"online must be True; got {result!r}"
        assert result["workers_online"] == 2, (
            f"workers_online must be 2 (NULL-caps worker is still online); "
            f"got {result['workers_online']!r}"
        )
        # NULL-caps worker contributes nothing; capable worker's kimi=True must appear
        assert result["cli"].get("kimi") is True, (
            f"cli.kimi must be True (from the capable worker); got cli={result['cli']!r}"
        )
    finally:
        async with SessionLocal() as session:
            await _delete_workers(session, pc_null, pc_capable)
            await session.commit()
