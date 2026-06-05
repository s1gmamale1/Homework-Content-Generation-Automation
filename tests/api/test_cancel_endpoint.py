from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_cancel_pending_is_atomic():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=True)), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="cancelled"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_running_sets_cancelling_and_cancels_task():
    jid = uuid4()
    fake_task = SimpleNamespace(cancel=lambda: setattr(fake_task, "cancelled", True))
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.request_cancel", AsyncMock(return_value=True)), \
         patch.dict("app.services.worker.RUNNING_JOBS", {jid: fake_task}, clear=False), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="cancelling"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200
    assert getattr(fake_task, "cancelled", False) is True


def test_cancel_done_job_409():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.request_cancel", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, status="done"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 409
