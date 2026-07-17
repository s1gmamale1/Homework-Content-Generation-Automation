"""Real-DB proof (task 2, SA-key dead-host scrub/claim synchronization): the three
assignment-state-write routes in ``app/api/v1/sa_keys.py`` — ``assign_sa_key``
(PUT), ``unassign_sa_key`` (DELETE), ``scrub_sa_key`` (POST .../scrub) — take
the EXCLUSIVE host-scoped advisory lock (``workers_repo.lock_host_exclusive``,
key ``host:{hostname}``) before their first mutation. Without this, a
tombstone write (scrub) could interleave with a claim that already re-read
"no tombstone" under its SHARED lock (``lock_host_shared``, held for the
duration of ``claim_next_job``'s transaction, task 1) — the claim would then
proceed on a host whose credential is being revoked underneath it.

Two proofs:
  1. Two-connection contention oracle (same primitives task 1 already proved
     in ``tests/integration/test_host_scrub_sync.py``, reprised here from the
     writer's point of view): conn A holds ``lock_host_shared('H')``
     uncommitted (simulating an in-flight claim transaction); conn B's
     EXCLUSIVE try-lock on the same key must return False while A holds it,
     True once A commits — proving the writer's exclusive lock genuinely
     contends with a claim's shared lock.
  2. Source/behavior assertion: each of the three routes' source calls
     ``lock_host_exclusive`` before its ``repo.*`` mutation (``assign``/
     ``unassign``/``scrub``) — ``inspect.getsource``, mirroring
     ``tests/repositories/test_batch_pause_repo.py``'s source-assertion
     style.

RUN_DB_INTEGRATION=1 required (test 1 needs a real Postgres; test 2 is pure
source inspection but the whole file shares one skipif for consistency with
the rest of this package).

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_scrub \\
    uv run python -m pytest tests/integration/test_assignment_writer_locks.py -q
"""
from __future__ import annotations

import inspect
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_writer_exclusive_lock_contends_with_claim_shared_lock():
    """conn A holds lock_host_shared (uncommitted, simulating an in-flight
    claim tx) -> conn B's exclusive try-lock on the same host key must
    return False while A holds it, True after A commits."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    hostname = f"writer-lock-{uuid4().hex[:8]}"

    async with SessionLocal() as sa, SessionLocal() as sb:
        await sa.begin()
        await workers_repo.lock_host_shared(sa, hostname)

        key = (
            await sb.execute(text("SELECT hashtext(:key)"), {"key": f"host:{hostname}"})
        ).scalar_one()
        got_before = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
        ).scalar_one()
        assert got_before is False, (
            "a claim holding the shared lock must block the writer's exclusive attempt"
        )
        await sb.rollback()  # release B's failed-try transaction

        await sa.commit()  # releases A's tx-scoped SHARED lock

        got_after = (
            await sb.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key})
        ).scalar_one()
        assert got_after is True, (
            "once the claim commits, the writer's exclusive lock attempt must succeed"
        )
        await sb.rollback()


# ─── route source/ordering assertions ───────────────────────────────────────


def test_assign_route_takes_exclusive_lock_before_mutation():
    from app.api.v1 import sa_keys as routes

    src = inspect.getsource(routes.assign_sa_key)
    assert "lock_host_exclusive" in src, "assign_sa_key must take the exclusive host lock"
    lock_pos = src.index("lock_host_exclusive")
    mutate_pos = src.index("repo.assign(")
    assert lock_pos < mutate_pos, (
        "assign_sa_key must take the exclusive host lock BEFORE repo.assign"
    )


def test_unassign_route_takes_exclusive_lock_before_mutation():
    from app.api.v1 import sa_keys as routes

    src = inspect.getsource(routes.unassign_sa_key)
    assert "lock_host_exclusive" in src, "unassign_sa_key must take the exclusive host lock"
    lock_pos = src.index("lock_host_exclusive")
    mutate_pos = src.index("repo.unassign(")
    assert lock_pos < mutate_pos, (
        "unassign_sa_key must take the exclusive host lock BEFORE repo.unassign"
    )


def test_scrub_route_takes_exclusive_lock_before_mutation():
    from app.api.v1 import sa_keys as routes

    src = inspect.getsource(routes.scrub_sa_key)
    assert "lock_host_exclusive" in src, "scrub_sa_key must take the exclusive host lock"
    lock_pos = src.index("lock_host_exclusive")
    mutate_pos = src.index("repo.scrub(")
    assert lock_pos < mutate_pos, (
        "scrub_sa_key must take the exclusive host lock BEFORE repo.scrub"
    )
