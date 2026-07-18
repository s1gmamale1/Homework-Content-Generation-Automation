"""Tests for the read-only dashboard-viewer app (viewer_main.py).

Covers: viewer-token auth on the coverage route (401/200), 404 on operator
routes and the docs surface, refuse-to-start lifespan behavior (empty
DASHBOARD_TOKEN / overlap with AUTH_TOKEN), and a route-enumeration proving
the app's entire API surface is `/health` + the coverage route, GET-only.

The 200-with-viewer-token case needs a real Postgres (the coverage route
queries the DB); it's guarded by the same RUN_DB_INTEGRATION skipif pattern
used across tests/api/. Every other test here is DB-free — the viewer auth
dependency (get_viewer_user) 401s before the route's DB session dependency
ever executes a query, and 404s never reach a route at all.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

import viewer_main
from app import config

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _viewer_tokens(monkeypatch):
    """Default viewer/operator token split shared by most tests below."""
    monkeypatch.setattr(config.settings, "dashboard_token", "viewer-secret")
    monkeypatch.setattr(config.settings, "auth_token", "operator-secret")


async def test_coverage_401_with_operator_token():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/v1/dashboard/coverage",
            headers={"Authorization": "Bearer operator-secret"},
        )
    assert r.status_code == 401


async def test_coverage_401_with_no_header():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/dashboard/coverage")
    assert r.status_code == 401


async def test_operator_routes_404():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/api/v1/books", "/api/v1/jobs/batches"):
            r = await c.get(path, headers={"Authorization": "Bearer viewer-secret"})
            assert r.status_code == 404, path


async def test_no_docs_surface():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            r = await c.get(path)
            assert r.status_code == 404, path


async def test_health_is_static_ok_no_db_detail():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_startup_refuses_when_dashboard_token_empty(monkeypatch):
    monkeypatch.setattr(config.settings, "dashboard_token", "")
    monkeypatch.setattr(config.settings, "auth_token", "operator-secret")
    with pytest.raises(RuntimeError, match="DASHBOARD_TOKEN"):
        async with viewer_main.app.router.lifespan_context(viewer_main.app):
            pass


async def test_startup_refuses_when_dashboard_token_overlaps_auth_token(monkeypatch):
    monkeypatch.setattr(config.settings, "dashboard_token", "shared-secret")
    monkeypatch.setattr(
        config.settings, "auth_token", "shared-secret,operator-secret"
    )
    with pytest.raises(RuntimeError, match="overlaps"):
        async with viewer_main.app.router.lifespan_context(viewer_main.app):
            pass


async def test_route_enumeration_is_read_only_and_minimal():
    api_paths = set()
    for route in viewer_main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        assert not (methods & {"POST", "PUT", "PATCH", "DELETE"}), (
            f"route {path} accepts a mutating method: {methods}"
        )
        if path and (path.startswith("/api/") or path == "/health"):
            api_paths.add(path)
    assert api_paths == {"/health", "/api/v1/dashboard/coverage"}


DB_AVAILABLE = os.environ.get("RUN_DB_INTEGRATION") == "1"


@pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)
async def test_coverage_200_with_viewer_token():
    transport = ASGITransport(app=viewer_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/v1/dashboard/coverage",
            headers={"Authorization": "Bearer viewer-secret"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_language"] == "uz"
    assert "entries" in body


async def test_query_token_rejected_at_asgi_level():
    """Final-review minor 2: prove header-only-ness with a real request — a
    valid viewer token passed ONLY as ?token= must 401 (before any DB touch),
    not authenticate. Pins the property against a future refactor that adds a
    Query parameter back to get_viewer_user."""
    import app.config as config
    from unittest.mock import patch

    with patch.object(config.settings, "dashboard_token", "viewer-secret"):
        transport = ASGITransport(app=viewer_main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/dashboard/coverage?token=viewer-secret")
    assert r.status_code == 401
