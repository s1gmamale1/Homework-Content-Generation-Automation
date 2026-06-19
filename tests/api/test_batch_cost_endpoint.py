"""Tests for GET /jobs/batch/{id}/cost — cost observability endpoint (C4 Task 8).

Covers:
  1. A paused batch returns batch_api_cost_usd, paused_at, paused_reason.
  2. A non-paused batch returns NULL pause fields.
  3. The fleet budget_state is always included (api_paused_at / api_paused_reason).
  4. A missing batch_id returns 404 (load-bearing: if the NOT-FOUND guard were
     removed, the endpoint would panic on None.paused_at instead of returning 404).

Each test is written so removing the relevant field from the response causes it to
fail (ensuring the fields can't be silently dropped in a future refactor).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

_BATCH_ID = uuid4()
_PAUSED_AT = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)

_FAKE_BATCH_PAUSED = SimpleNamespace(
    id=_BATCH_ID,
    paused_at=_PAUSED_AT,
    paused_reason="batch-cap",
)
_FAKE_BATCH_UNPAUSED = SimpleNamespace(
    id=_BATCH_ID,
    paused_at=None,
    paused_reason=None,
)
_FAKE_BUDGET_PAUSED = SimpleNamespace(
    api_paused_at=_PAUSED_AT,
    api_paused_reason="fleet-daily-cap",
)
_FAKE_BUDGET_CLEAR = SimpleNamespace(
    api_paused_at=None,
    api_paused_reason=None,
)

_HDR = {"Authorization": "Bearer 123"}


def _app():
    from main import app
    return app


def _client():
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t")


def _make_fake_session():
    s = MagicMock()
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.rollback = AsyncMock()
    s.close = AsyncMock()
    return s


def _session_override(session):
    from app.db import get_session
    async def _fake():
        yield session
    return get_session, _fake


@pytest.mark.asyncio
async def test_cost_endpoint_paused_batch():
    """A paused batch must return batch_api_cost_usd, paused_at, paused_reason.

    Removing any of these fields from the endpoint causes an assertion to fail.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    session = _make_fake_session()
    session.get = AsyncMock(return_value=_FAKE_BATCH_PAUSED)

    app_obj = _app()
    _, override = _session_override(session)
    app_obj.dependency_overrides[get_session] = override
    try:
        with (
            patch.object(batch_mod.cost_repo, "batch_api_cost_usd",
                         AsyncMock(return_value=1.50)),
            patch.object(batch_mod.budget_repo, "get_state",
                         AsyncMock(return_value=_FAKE_BUDGET_CLEAR)),
        ):
            async with _client() as c:
                resp = await c.get(f"/api/v1/jobs/batch/{_BATCH_ID}/cost", headers=_HDR)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "batch_api_cost_usd" in data, "batch_api_cost_usd must be present"
    assert data["batch_api_cost_usd"] == pytest.approx(1.50), (
        f"Expected 1.50, got {data['batch_api_cost_usd']}"
    )
    assert "paused_at" in data, "paused_at must be present"
    assert data["paused_at"] is not None, "paused_at must be non-null for a paused batch"
    assert "paused_reason" in data, "paused_reason must be present"
    assert data["paused_reason"] == "batch-cap"

    # Fleet state must also be present (even if the fleet is not paused).
    assert "fleet_api_paused_at" in data, "fleet_api_paused_at must be present"
    assert data["fleet_api_paused_at"] is None  # fleet clear in this test
    assert "fleet_api_paused_reason" in data, "fleet_api_paused_reason must be present"


@pytest.mark.asyncio
async def test_cost_endpoint_unpaused_batch():
    """An unpaused batch must return null pause fields (not missing — null)."""
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    session = _make_fake_session()
    session.get = AsyncMock(return_value=_FAKE_BATCH_UNPAUSED)

    app_obj = _app()
    _, override = _session_override(session)
    app_obj.dependency_overrides[get_session] = override
    try:
        with (
            patch.object(batch_mod.cost_repo, "batch_api_cost_usd",
                         AsyncMock(return_value=0.0)),
            patch.object(batch_mod.budget_repo, "get_state",
                         AsyncMock(return_value=_FAKE_BUDGET_CLEAR)),
        ):
            async with _client() as c:
                resp = await c.get(f"/api/v1/jobs/batch/{_BATCH_ID}/cost", headers=_HDR)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["paused_at"] is None, "paused_at must be null for an unpaused batch"
    assert data["paused_reason"] is None, "paused_reason must be null for an unpaused batch"
    assert data["batch_api_cost_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_cost_endpoint_fleet_paused():
    """When the fleet is paused, fleet_api_paused_at and fleet_api_paused_reason
    must be non-null — even if the specific batch is not paused.

    Dropping the fleet fields from the endpoint response causes this test to fail.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    session = _make_fake_session()
    session.get = AsyncMock(return_value=_FAKE_BATCH_UNPAUSED)

    app_obj = _app()
    _, override = _session_override(session)
    app_obj.dependency_overrides[get_session] = override
    try:
        with (
            patch.object(batch_mod.cost_repo, "batch_api_cost_usd",
                         AsyncMock(return_value=3.00)),
            patch.object(batch_mod.budget_repo, "get_state",
                         AsyncMock(return_value=_FAKE_BUDGET_PAUSED)),
        ):
            async with _client() as c:
                resp = await c.get(f"/api/v1/jobs/batch/{_BATCH_ID}/cost", headers=_HDR)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["fleet_api_paused_at"] is not None, (
        "fleet_api_paused_at must be non-null when the fleet is paused"
    )
    assert data["fleet_api_paused_reason"] == "fleet-daily-cap"


@pytest.mark.asyncio
async def test_cost_endpoint_missing_batch_returns_404():
    """A missing batch_id must return 404.

    If the NOT-FOUND guard were removed, the code would AttributeError on
    None.paused_at instead — hiding the guard's load-bearing nature.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    session = _make_fake_session()
    session.get = AsyncMock(return_value=None)  # batch not found

    app_obj = _app()
    _, override = _session_override(session)
    app_obj.dependency_overrides[get_session] = override
    try:
        with (
            patch.object(batch_mod.cost_repo, "batch_api_cost_usd",
                         AsyncMock(return_value=0.0)),
            patch.object(batch_mod.budget_repo, "get_state",
                         AsyncMock(return_value=_FAKE_BUDGET_CLEAR)),
        ):
            async with _client() as c:
                resp = await c.get(f"/api/v1/jobs/batch/{_BATCH_ID}/cost", headers=_HDR)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 404, (
        f"Expected 404 for a missing batch, got {resp.status_code}: {resp.text}"
    )
