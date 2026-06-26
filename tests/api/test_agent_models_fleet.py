"""Real-DB proof: GET /api/v1/agent/models returns a `fleet` block.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL (same guard as all other
fleet integration tests). The tests seed real worker rows (via upsert_heartbeat),
then hit the endpoint via AsyncClient and assert the response body.

RED-proof: before adding the `fleet` key, the `assert "fleet" in body` assertions
fail — verified during development (the endpoint returned only providers/api_supported/tiers).

Harness mirrors test_workers_drain_endpoint.py: ASGITransport + AsyncClient +
real SessionLocal for DB seeding, teardown via delete(WorkerNode) in a finally block.
Existing-key regression is covered in assertion (c) of the online-worker test.
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

_PC_ID = "test-fleet-ep:31001"
_URL = "/api/v1/agent/models"


@pytest.mark.asyncio
async def test_fleet_no_workers_returns_online_false():
    """GET /agent/models with zero workers → fleet.online is False."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from main import app

    # Ensure no leftover row from a previous failed run
    async with SessionLocal() as s:
        await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == _PC_ID))
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get(_URL)

    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()

    # (a) no workers → fleet.online is False
    assert "fleet" in body, f"missing 'fleet' key; got keys: {list(body)}"
    assert body["fleet"]["online"] is False
    assert body["fleet"]["workers_online"] == 0

    # (c) regression: existing keys still present
    assert "providers" in body
    assert "api_supported" in body


@pytest.mark.asyncio
async def test_fleet_with_online_worker_reflects_capabilities():
    """GET /agent/models with a seeded online worker → fleet.cli/api reflect the blob."""
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from main import app

    _caps = {
        "cli": {"claude": True, "gemini": True, "kimi": False},
        "api": {"claude": True, "gemini": False},
    }

    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_ID, capabilities=_caps)
            await s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(_URL)

        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()

        # (a) fleet key present
        assert "fleet" in body, f"missing 'fleet' key; got keys: {list(body)}"

        # (b) capabilities blob reflected
        fleet = body["fleet"]
        assert fleet["online"] is True
        assert fleet["workers_online"] >= 1
        assert fleet["cli"]["claude"] is True
        assert fleet["cli"]["gemini"] is True
        assert fleet["cli"]["kimi"] is False
        assert fleet["api"]["claude"] is True
        assert fleet["api"]["gemini"] is False

        # (c) regression: existing keys still present
        assert "providers" in body
        assert "api_supported" in body
        assert "tiers" in body

    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == _PC_ID))
            await s.commit()
