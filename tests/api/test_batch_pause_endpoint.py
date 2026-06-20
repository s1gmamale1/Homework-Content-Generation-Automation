import pytest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.db import get_session

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


def _make_session_override(batch_obj):
    """Return an async generator that yields a fake session whose .get() returns batch_obj."""
    async def _fake_get_session():
        session = MagicMock()
        session.get = AsyncMock(return_value=batch_obj)
        session.commit = AsyncMock()
        yield session

    return _fake_get_session


# ---------------------------------------------------------------------------
# pause endpoint
# ---------------------------------------------------------------------------

def test_pause_batch_returns_200_and_paused_true():
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid)

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    try:
        with patch("app.api.v1.batch.batches_repo.pause_batch",
                   AsyncMock(return_value=None)):
            r = client.post(f"/api/v1/jobs/batch/{bid}/pause")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    data = r.json()
    assert data["batch_id"] == str(bid)
    assert data["paused"] is True


def test_pause_batch_calls_repo_with_reason_manual():
    """The endpoint must pass reason='manual' to batches_repo.pause_batch."""
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid)

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    mock_pause = AsyncMock(return_value=None)
    try:
        with patch("app.api.v1.batch.batches_repo.pause_batch", mock_pause):
            client.post(f"/api/v1/jobs/batch/{bid}/pause")
    finally:
        app.dependency_overrides.pop(get_session, None)

    # Verify the call was made with reason="manual"
    assert mock_pause.called
    _session_arg, batch_id_arg, reason_arg = mock_pause.call_args.args
    assert reason_arg == "manual", (
        f"pause_batch must be called with reason='manual', got {reason_arg!r}"
    )


def test_pause_batch_404():
    bid = uuid4()

    app.dependency_overrides[get_session] = _make_session_override(None)
    try:
        r = client.post(f"/api/v1/jobs/batch/{bid}/pause")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# unpause endpoint
# ---------------------------------------------------------------------------

def test_unpause_batch_returns_200_and_paused_false():
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid)

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    try:
        with patch("app.api.v1.batch.batches_repo.unpause_batch",
                   AsyncMock(return_value=None)):
            r = client.post(f"/api/v1/jobs/batch/{bid}/unpause")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    data = r.json()
    assert data["batch_id"] == str(bid)
    assert data["paused"] is False


def test_unpause_batch_404():
    bid = uuid4()

    app.dependency_overrides[get_session] = _make_session_override(None)
    try:
        r = client.post(f"/api/v1/jobs/batch/{bid}/unpause")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 404
