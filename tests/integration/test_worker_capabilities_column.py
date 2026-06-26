"""Real-DB: workers.capabilities JSONB column (migration 0035).

Verifies that a WorkerNode row can be inserted with a capabilities dict and
read back with the same value. Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL set.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_worker_capabilities_roundtrip():
    """Insert a WorkerNode with a capabilities dict; read it back equal."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    caps = {
        "can_claude_api": True,
        "can_gemini_api": False,
        "judge_api_ok": True,
    }
    pc_id = "test-worker:99999"

    # Clean up any leftover row from a previous failed run
    async with SessionLocal() as s:
        await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
        await s.commit()

    try:
        async with SessionLocal() as s:
            worker = WorkerNode(
                pc_id=pc_id,
                last_heartbeat=datetime.now(timezone.utc),
                status="online",
                capabilities=caps,
            )
            s.add(worker)
            await s.commit()

        async with SessionLocal() as s:
            row = await s.get(WorkerNode, pc_id)
            assert row is not None, "WorkerNode row not found after insert"
            assert row.capabilities == caps, (
                f"capabilities mismatch: got {row.capabilities!r}, expected {caps!r}"
            )
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
            await s.commit()


@pytest.mark.asyncio
async def test_worker_capabilities_null_default():
    """Insert a WorkerNode without capabilities; column must read back as None."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    pc_id = "test-worker:88888"

    async with SessionLocal() as s:
        await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
        await s.commit()

    try:
        async with SessionLocal() as s:
            worker = WorkerNode(
                pc_id=pc_id,
                last_heartbeat=datetime.now(timezone.utc),
                status="online",
                # capabilities omitted — should default to NULL
            )
            s.add(worker)
            await s.commit()

        async with SessionLocal() as s:
            row = await s.get(WorkerNode, pc_id)
            assert row is not None
            assert row.capabilities is None, (
                f"expected None for omitted capabilities, got {row.capabilities!r}"
            )
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))
            await s.commit()
