"""Real-DB tests for the version-floor write paths (fleet-worker-version-gate-1).

The raise-only predicate is SQL — mocks can't prove it BITES. Runs only with
RUN_DB_INTEGRATION=1 against a scratch DB (127.0.0.1, never edu_copy).
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_DB_INTEGRATION"),
    reason="needs RUN_DB_INTEGRATION=1 + scratch DATABASE_URL",
)


@pytest.mark.asyncio
async def test_raise_version_floor_sets_from_null_then_raises_then_refuses_lower():
    from app.db import SessionLocal
    from app.repositories import budget as budget_repo

    async with SessionLocal() as session:
        # normalize: clear any floor left by other tests
        await budget_repo.set_version_floor(session, version=None, stamped_by="test")
        await session.commit()

    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=100, stamped_by="t1") is True
        await session.commit()

    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version == 100
        assert state.min_worker_version_stamped_by == "t1"
        assert state.min_worker_version_stamped_at is not None

    # RAISE: 100 -> 150 succeeds
    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=150, stamped_by="t2") is True
        await session.commit()

    # LOWER attempt: 150 -> 120 must be a no-op (RED-proof: without the
    # WHERE min<version guard this would overwrite and the assert fails)
    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=120, stamped_by="t3") is False
        await session.commit()

    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version == 150
        assert state.min_worker_version_stamped_by == "t2"

    # ESCAPE HATCH: set_version_floor CAN lower, and CAN clear
    async with SessionLocal() as session:
        await budget_repo.set_version_floor(session, version=90, stamped_by="operator")
        await session.commit()
    async with SessionLocal() as session:
        assert (await budget_repo.get_state(session)).min_worker_version == 90

    async with SessionLocal() as session:
        await budget_repo.set_version_floor(session, version=None, stamped_by="operator")
        await session.commit()
    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version is None
        assert state.min_worker_version_stamped_by == "operator"
