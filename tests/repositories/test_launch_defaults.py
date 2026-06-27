"""Real-DB: launch_defaults singleton table — get/update + singleton enforcement.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.launch_defaults import LaunchDefaults
from app.repositories import launch_defaults as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)


# ---------------------------------------------------------------------------
# db_session fixture — provides a real AsyncSession for each test,
# rolling back after each test to keep tests isolated.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_migration_seeds_singleton(db_session):
    row = await repo.get(db_session)
    assert row.id == 1
    assert (row.judge_provider, row.judge_model) == ("gemini", "gemini-2.5-flash")
    assert (row.extract_provider, row.extract_model) == ("gemini", "gemini-2.5-flash")
    assert row.judge_transport == "inherit"
    assert row.extract_transport == "inherit"
    assert row.toc_transport == "cli"


async def test_update_partial_roundtrip(db_session):
    await repo.update(db_session, {"judge_provider": "claude", "judge_model": "claude-opus-4-7"})
    row = await repo.get(db_session)
    assert (row.judge_provider, row.judge_model) == ("claude", "claude-opus-4-7")
    # untouched fields remain
    assert row.extract_provider == "gemini"
    assert row.updated_at is not None


async def test_singleton_invariant_rejects_second_row(db_session):
    # RED-prove the CHECK(id=1): inserting id=2 must violate the constraint.
    db_session.add(LaunchDefaults(id=2, judge_provider="x"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
