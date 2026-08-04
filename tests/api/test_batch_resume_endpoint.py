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


def test_resume_batch_returns_jobs_resumed():
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid, book_id=uuid4())

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    try:
        with patch("app.api.v1.batch.jobs_repo.resume_failed_in_batch",
                   AsyncMock(return_value={"resumed": 3, "skipped_retired": []})):
            r = client.post(f"/api/v1/jobs/batch/{bid}/resume")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    data = r.json()
    assert data["jobs_resumed"] == 3
    assert data["jobs_skipped_retired"] == []
    assert data["batch_id"] == str(bid)


def test_resume_batch_404():
    bid = uuid4()

    app.dependency_overrides[get_session] = _make_session_override(None)
    try:
        r = client.post(f"/api/v1/jobs/batch/{bid}/resume")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 404
