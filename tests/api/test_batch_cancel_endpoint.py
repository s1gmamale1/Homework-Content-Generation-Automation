from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.db import get_session

# Override auth globally for this module
app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def _make_session_override(batch_obj):
    """Return an async generator that yields a fake session whose .get() returns batch_obj."""
    async def _fake_get_session():
        session = MagicMock()
        # Make session.get() an awaitable returning batch_obj
        session.get = AsyncMock(return_value=batch_obj)
        session.commit = AsyncMock()
        yield session

    return _fake_get_session


def test_cancel_batch_returns_counts():
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid)

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    try:
        with patch("app.api.v1.batch.jobs_repo.cancel_all_in_batch",
                   AsyncMock(return_value={"cancelled": 2, "cancelling": 1})), \
             patch("app.api.v1.batch.jobs_repo.running_job_ids_in_batch",
                   AsyncMock(return_value=[])):
            r = client.post(f"/api/v1/jobs/batch/{bid}/cancel")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    data = r.json()
    assert data["cancelled"] == 2
    assert data["cancelling"] == 1
    assert data["batch_id"] == str(bid)


def test_cancel_batch_404():
    bid = uuid4()

    app.dependency_overrides[get_session] = _make_session_override(None)
    try:
        r = client.post(f"/api/v1/jobs/batch/{bid}/cancel")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 404
