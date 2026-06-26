from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from app.api.v1.jobs import JobOut
from app.auth import get_current_user
from app.db import get_session

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_retry_archive_happy_path():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done", notion_archived_at=None)
    out = JobOut(id=jid, book_id=uuid4(), toc_entry_id=uuid4(),
                 subject="kimyo-g7-11", status="done")
    arch = AsyncMock()

    # Inject a fake session so we can assert expire_all() is called.
    # expire_all() is a sync method on AsyncSession; a plain MagicMock suffices.
    fake_session = MagicMock()
    fake_session.expire_all = MagicMock()
    app.dependency_overrides[get_session] = lambda: fake_session
    try:
        with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
             patch("app.api.v1.jobs.notion_archive.archive_job", arch), \
             patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
            r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    arch.assert_awaited_once()
    # Guard: this assertion MUST fail if session.expire_all() is removed from
    # the endpoint — without it _job_out returns stale notion_skip_reason data.
    fake_session.expire_all.assert_called_once()


def test_retry_archive_rejects_non_done():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="running", notion_archived_at=None)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 409


def test_retry_archive_rejects_already_archived():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done",
                          notion_archived_at=datetime.now(timezone.utc))
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 409


def test_retry_archive_404_when_missing():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=None)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 404
