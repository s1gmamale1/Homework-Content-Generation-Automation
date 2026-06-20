"""Real-DB proof: POST /workers/{pc_id}/drain and /undrain update the DB row.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL (same guard as all other
fleet integration tests). The happy-path tests seed a real worker row, POST the
endpoint via AsyncClient, then query the workers table in a fresh session and
assert the status column was actually changed — a mock-only test would pass even
if the handler returned the body without calling set_status.

RED-proof: temporarily returning the body directly (no set_status call) causes
the "DB row status == draining" assertion to fail — verified during development.
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_PC_ID = "test-drain-ep:31000"
_HDR = {"Authorization": "Bearer 123"}


@pytest.mark.asyncio
async def test_drain_worker_updates_db_status_to_draining():
    """POST /drain → 200 AND the workers row status is 'draining' in the DB."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from main import app

    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_ID)
            await s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(f"/api/v1/workers/{_PC_ID}/drain", headers=_HDR)

        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["pc_id"] == _PC_ID
        assert body["status"] == "draining"

        # Prove the DB row actually changed — not just the response body.
        async with SessionLocal() as s:
            status = await workers_repo.get_status(s, _PC_ID)
        assert status == "draining", (
            f"DB row status must be 'draining' after POST /drain, got {status!r}"
        )
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == _PC_ID))
            await s.commit()


@pytest.mark.asyncio
async def test_undrain_worker_updates_db_status_to_online():
    """POST /undrain → 200 AND the workers row status is 'online' in the DB."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from main import app

    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_ID, status="draining")
            await s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(f"/api/v1/workers/{_PC_ID}/undrain", headers=_HDR)

        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["pc_id"] == _PC_ID
        assert body["status"] == "online"

        # Prove the DB row actually changed.
        async with SessionLocal() as s:
            status = await workers_repo.get_status(s, _PC_ID)
        assert status == "online", (
            f"DB row status must be 'online' after POST /undrain, got {status!r}"
        )
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == _PC_ID))
            await s.commit()


@pytest.mark.asyncio
async def test_drain_unknown_worker_returns_404():
    """POST /drain for an unknown pc_id → 404 (real DB: set_status returns False)."""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/workers/ghost-host:99999/drain", headers=_HDR)

    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_undrain_unknown_worker_returns_404():
    """POST /undrain for an unknown pc_id → 404 (real DB: set_status returns False)."""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/v1/workers/ghost-host:99999/undrain", headers=_HDR)

    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
