"""HTTP-layer coverage for the version-floor surface on the workers router
(Task 5 review fix): GET /workers exposes `version_floor`, and
PUT /workers/version-floor sets/clears it via `budget_repo.set_version_floor`.

Follows the mocking style already used for sibling PUT endpoints in this repo
(tests/api/test_settings_boss_toggle.py): TestClient + get_current_user
dependency override + `unittest.mock.patch` on the repo functions the route
module imports. The session itself is overridden with an AsyncMock so we can
assert `session.commit()` was actually awaited — a mock-only assertion on the
repo call would pass even if the handler forgot to persist the write.

RED-proof for the validation test: removing `ge=0` from `VersionFloorIn.value`
would make `{"value": -1}` return 200 instead of 422.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.db import get_session
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"user": "t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def mock_session():
    """Override get_session with an AsyncMock so `session.commit()` (and any
    other session use) can be asserted on without a real DB connection."""
    session = AsyncMock()

    async def _fake_get_session():
        yield session

    app.dependency_overrides[get_session] = _fake_get_session
    yield session
    app.dependency_overrides.pop(get_session, None)


def _state(min_worker_version=None):
    return SimpleNamespace(min_worker_version=min_worker_version)


def _list_workers_patches(min_worker_version):
    return (
        patch("app.api.v1.workers.workers_repo.list_with_liveness", AsyncMock(return_value=[])),
        patch("app.api.v1.workers.sa_keys_repo.list_assignments", AsyncMock(return_value=[])),
        patch("app.api.v1.workers.budget_repo.get_state", AsyncMock(return_value=_state(min_worker_version))),
    )


def test_get_workers_exposes_version_floor_when_set(mock_session):
    p1, p2, p3 = _list_workers_patches(42)
    with p1, p2, p3:
        r = client.get("/api/v1/workers")
    assert r.status_code == 200
    assert r.json()["version_floor"] == 42


def test_get_workers_exposes_version_floor_when_none(mock_session):
    p1, p2, p3 = _list_workers_patches(None)
    with p1, p2, p3:
        r = client.get("/api/v1/workers")
    assert r.status_code == 200
    assert r.json()["version_floor"] is None


def test_put_version_floor_sets_value_and_commits(mock_session):
    set_floor = AsyncMock(return_value=None)
    with patch("app.api.v1.workers.budget_repo.set_version_floor", set_floor):
        r = client.put("/api/v1/workers/version-floor", json={"value": 123})

    assert r.status_code == 200, r.text
    assert r.json() == {"version_floor": 123}

    set_floor.assert_awaited_once()
    _, kwargs = set_floor.await_args
    assert kwargs["version"] == 123
    assert kwargs["stamped_by"] == "operator"

    mock_session.commit.assert_awaited_once()


def test_put_version_floor_clears_with_null(mock_session):
    set_floor = AsyncMock(return_value=None)
    with patch("app.api.v1.workers.budget_repo.set_version_floor", set_floor):
        r = client.put("/api/v1/workers/version-floor", json={"value": None})

    assert r.status_code == 200, r.text
    assert r.json() == {"version_floor": None}

    set_floor.assert_awaited_once()
    _, kwargs = set_floor.await_args
    assert kwargs["version"] is None
    assert kwargs["stamped_by"] == "operator"

    mock_session.commit.assert_awaited_once()


def test_put_version_floor_rejects_negative_value(mock_session):
    """ge=0 constraint BITES: a negative value is rejected before the handler
    (and therefore before set_version_floor) ever runs."""
    set_floor = AsyncMock(return_value=None)
    with patch("app.api.v1.workers.budget_repo.set_version_floor", set_floor):
        r = client.put("/api/v1/workers/version-floor", json={"value": -1})

    assert r.status_code == 422, r.text
    set_floor.assert_not_awaited()
    mock_session.commit.assert_not_awaited()
