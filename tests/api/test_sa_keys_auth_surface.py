from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import api_v1_router
from app.auth import get_current_user, get_current_user_strict
from app.config import settings
from app.db import get_session
from main import app


STRONG_TOKEN = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"
KEY_ID = "00000000-0000-0000-0000-000000000001"

CASES = [
    ("POST", "/api/v1/sa-keys", {"files": {"file": ("k.json", b"{}")}}),
    ("GET", "/api/v1/sa-keys", {}),
    ("DELETE", f"/api/v1/sa-keys/{KEY_ID}", {}),
    (
        "PATCH",
        f"/api/v1/sa-keys/{KEY_ID}",
        {"json": {"max_concurrent_calls": 1}},
    ),
    ("GET", f"/api/v1/sa-keys/{KEY_ID}/download", {}),
    ("GET", "/api/v1/sa-keys/assignments", {}),
    (
        "PUT",
        "/api/v1/sa-keys/assignments/Host-01",
        {"json": {"key_id": KEY_ID}},
    ),
    ("DELETE", "/api/v1/sa-keys/assignments/Host-01", {}),
    ("POST", "/api/v1/sa-keys/assignments/Host-01/scrub", {}),
]

ROUTES = {
    ("POST", "/api/v1/sa-keys"),
    ("GET", "/api/v1/sa-keys"),
    ("DELETE", "/api/v1/sa-keys/{key_id}"),
    ("PATCH", "/api/v1/sa-keys/{key_id}"),
    ("GET", "/api/v1/sa-keys/{key_id}/download"),
    ("GET", "/api/v1/sa-keys/assignments"),
    ("PUT", "/api/v1/sa-keys/assignments/{hostname}"),
    ("DELETE", "/api/v1/sa-keys/assignments/{hostname}"),
    ("POST", "/api/v1/sa-keys/assignments/{hostname}/scrub"),
}


def _dependency_calls(dependency) -> set[Callable]:
    calls = {dependency.call}
    for child in dependency.dependencies:
        calls.update(_dependency_calls(child))
    return calls


def test_every_sa_key_route_uses_only_the_strict_auth_dependency():
    routes = [
        route
        for route in api_v1_router.routes
        if getattr(route, "path", "").startswith("/api/v1/sa-keys")
    ]
    actual = {
        (method, route.path)
        for route in routes
        for method in route.methods
    }
    assert actual == ROUTES

    for route in routes:
        calls = _dependency_calls(route.dependant)
        assert get_current_user_strict in calls, route.path
        assert get_current_user not in calls, route.path


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "request_kwargs"), CASES)
@pytest.mark.parametrize(
    ("auth_case", "expected_status"),
    [
        ("query-only", 401),
        ("header-plus-query", 401),
        ("missing", 401),
        ("unconfigured-local-mode", 503),
    ],
)
async def test_every_sa_key_route_rejects_non_header_only_auth_before_db(
    monkeypatch, method, path, request_kwargs, auth_case, expected_status
):
    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", True)

    async def forbidden_session():
        raise AssertionError("SA-key auth rejection must happen before DB access")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = forbidden_session
    headers = None
    request_path = path
    if auth_case == "query-only":
        request_path = f"{path}?token={STRONG_TOKEN}"
    elif auth_case == "header-plus-query":
        request_path = f"{path}?token={STRONG_TOKEN}"
        headers = {"Authorization": f"Bearer {STRONG_TOKEN}"}
    elif auth_case == "unconfigured-local-mode":
        monkeypatch.setattr(settings, "auth_token", "")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.request(
                method, request_path, headers=headers, **request_kwargs
            )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == expected_status
