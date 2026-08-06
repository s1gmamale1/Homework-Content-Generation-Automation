"""Real-DB: migration 0052 — claim_token columns + job_lease_events ledger.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)


# ---------------------------------------------------------------------------
# db_session fixture — provides a real AsyncSession for each test,
# rolling back after each test to keep tests isolated. Mirrors the idiom in
# tests/repositories/test_launch_defaults.py.
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

async def test_0052_adds_token_columns_and_ledger(db_session):
    cols_j = await db_session.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='homework_jobs' AND column_name='claim_token'"))
    assert cols_j.first()[0] == "uuid"

    cols_p = await db_session.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='phase_outputs' AND column_name='claim_token'"))
    assert cols_p.first()[0] == "uuid"

    # ledger uniqueness bites (asyncpg paramstyle via sa.text bound params)
    jid, tok = uuid.uuid4(), uuid.uuid4()
    ins = text(
        "INSERT INTO job_lease_events (id, job_id, claim_token, event_type, owner) "
        "VALUES (gen_random_uuid(), :jid, :tok, 'claimed', 'h:1@sha')")
    await db_session.execute(ins, {"jid": jid, "tok": tok})
    with pytest.raises(Exception):
        await db_session.execute(ins, {"jid": jid, "tok": tok})  # dup (job,token,event)
    await db_session.rollback()
