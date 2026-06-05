from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.api.v1.jobs import JobOut
from app.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_retry_allows_cancelled():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="cancelled")
    updated = SimpleNamespace(id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="pending")
    out = JobOut(id=updated.id, book_id=updated.book_id, toc_entry_id=updated.toc_entry_id,
                 subject=updated.subject, status=updated.status)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs.jobs_repo.reset_for_retry", AsyncMock(return_value=updated)), \
         patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_retry_still_rejects_running():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(id=jid, status="running"))):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
