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


@pytest.mark.asyncio
async def test_prune_stale_removes_only_rows_older_than_window():
    """Chunk-1 fleet fix: dead hostname:pid rows must be prunable. Surgical
    against a shared DB: the fake row's beat is ~9 years old and the window is
    8 years, so no real row can match."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    ancient = "test-host:88888"
    fresh = "test-host:88889"
    eight_years = 8 * 365 * 24 * 3600
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, ancient)
            await workers_repo.upsert_heartbeat(s, fresh)
            await s.execute(text(
                "UPDATE workers SET last_heartbeat = now() - interval '9 years' "
                "WHERE pc_id = :pc"), {"pc": ancient})
            await s.commit()
        async with SessionLocal() as s:
            n = await workers_repo.prune_stale(s, older_than_seconds=eight_years)
            await s.commit()
        assert n == 1
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        ids = {r["pc_id"] for r in rows}
        assert ancient not in ids
        assert fresh in ids
    finally:
        async with SessionLocal() as s:
            await s.execute(
                delete(WorkerNode).where(WorkerNode.pc_id.in_([ancient, fresh])))
            await s.commit()


@pytest.mark.asyncio
async def test_has_live_workers_fresh_beat_returns_true():
    """A row with a just-stamped heartbeat must report True."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "test-host:77771"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            result = await workers_repo.has_live_workers(s, stale_after_seconds=90)
        assert result is True
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()


@pytest.mark.asyncio
async def test_has_live_workers_only_stale_returns_false():
    """A row whose heartbeat is older than the window must return False."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "test-host:77772"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.execute(text(
                "UPDATE workers SET last_heartbeat = now() - interval '10 minutes' "
                "WHERE pc_id = :pc"), {"pc": pc})
            await s.commit()
        async with SessionLocal() as s:
            result = await workers_repo.has_live_workers(s, stale_after_seconds=90)
        assert result is False
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()


@pytest.mark.asyncio
async def test_has_live_workers_empty_table_returns_false():
    """No workers rows at all must return False.

    DELETE all rows inside the session, assert False, then ROLLBACK — no rows
    are permanently removed, so the shared DB stays intact for other tests.
    """
    from sqlalchemy import delete as sa_delete

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    async with SessionLocal() as s:
        await s.execute(sa_delete(WorkerNode))
        result = await workers_repo.has_live_workers(s, stale_after_seconds=90)
        await s.rollback()

    assert result is False


@pytest.mark.asyncio
async def test_deregister_removes_own_row():
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "test-host:88890"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            await workers_repo.deregister(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        assert pc not in {r["pc_id"] for r in rows}
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()
