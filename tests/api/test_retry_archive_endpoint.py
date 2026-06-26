from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from app.api.v1.jobs import JobOut
from app.auth import get_current_user

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
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs.notion_archive.archive_job", arch), \
         patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 200
    arch.assert_awaited_once()


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
