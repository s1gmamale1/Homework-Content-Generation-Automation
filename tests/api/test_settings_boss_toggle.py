"""GET returns solver_boss_arena_enabled; PUT persists a bool; PUT explicit null
is a no-op (the NOT NULL column is never written null)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"user": "t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _row(boss=True):
    return SimpleNamespace(
        judge_provider="gemini", judge_model="gemini-2.5-flash", judge_transport="inherit",
        solver_provider="gemini", solver_model="gemini-3.1-pro-preview", solver_transport="inherit",
        extract_provider="gemini", extract_model="gemini-2.5-flash", extract_transport="inherit",
        content_provider="gemini", content_model="gemini-3-flash-preview", content_transport="api",
        toc_transport="cli", output_language="uz", solver_boss_arena_enabled=boss)


def test_get_exposes_boss_toggle():
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))):
        r = client.get("/api/v1/settings/launch-defaults")
    assert r.status_code == 200
    assert r.json()["solver_boss_arena_enabled"] is True


def test_put_persists_false():
    upd = AsyncMock(return_value=_row(False))
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))), \
         patch("app.api.v1.settings.launch_defaults_repo.update", upd):
        r = client.put("/api/v1/settings/launch-defaults",
                       json={"solver_boss_arena_enabled": False})
    assert r.status_code == 200
    # the write carried the toggle
    assert upd.await_args.args[1]["solver_boss_arena_enabled"] is False


def test_put_explicit_null_is_dropped():
    upd = AsyncMock(return_value=_row(True))
    with patch("app.api.v1.settings.launch_defaults_repo.get", AsyncMock(return_value=_row(True))), \
         patch("app.api.v1.settings.launch_defaults_repo.update", upd):
        r = client.put("/api/v1/settings/launch-defaults",
                       json={"solver_boss_arena_enabled": None})
    assert r.status_code == 200
    # null = no-op: the NOT NULL column is never written null
    assert "solver_boss_arena_enabled" not in upd.await_args.args[1]
