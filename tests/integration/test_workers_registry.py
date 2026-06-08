"""Real-DB proof: a worker registers + heartbeats, and the liveness view +
endpoint report it online. Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_register_then_liveness_reports_online():
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "test-host:99999"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == pc]
        assert len(mine) == 1, f"expected exactly one row for {pc}, got {len(mine)}"
        assert mine[0]["online"] is True
        assert mine[0]["status"] == "online"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()


@pytest.mark.asyncio
async def test_busy_worker_still_heartbeats(monkeypatch):
    """Regression (the loop-top-beat bug): a worker whose slots are ALL busy —
    main loop blocked in _wait_for_slot_or_stop — must STILL report online,
    because the registry beat runs on its own task."""
    import asyncio

    from app.config import settings as cfg
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    monkeypatch.setattr(cfg, "heartbeat_seconds", 0.2)
    w = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
    await w._slots.acquire()  # occupy the only slot -> main loop blocks at :107
    run_task = asyncio.create_task(w.run())
    try:
        await asyncio.sleep(0.8)
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == w.id]
        assert mine and mine[0]["online"] is True, "busy worker was not reported online"
    finally:
        w.stop()
        await asyncio.wait_for(run_task, timeout=5)
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == w.id))
            await s.commit()


@pytest.mark.asyncio
async def test_workers_endpoint_returns_liveness():
    import httpx

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from main import app

    pc = "test-host:88888"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.get(
                "/api/v1/workers",
                headers={"Authorization": "Bearer 123"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert any(w["pc_id"] == pc and w["online"] is True for w in body["workers"])
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()
