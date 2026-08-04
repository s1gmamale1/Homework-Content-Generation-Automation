"""POST /jobs/{job_id}/retry must refuse a job stamped with a retired model
(gemini-2.5, retired 2026-08-03) with a structured 409 instead of resetting it
to pending — the pinned provider/model is reused verbatim on retry, so
retrying a retired-stamped job would call a dead model. A live-model job still
retries normally (unchanged behavior — mirrors tests/api/test_retry_cancelled.py).
"""
import pytest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
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


def _job(jid, book_id, status="failed", **role_overrides):
    base = dict(
        id=jid, book_id=book_id, status=status,
        provider="gemini", model="gemini-3.5-flash",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
    )
    base.update(role_overrides)
    return SimpleNamespace(**base)


def test_retry_retired_stamped_job_returns_409_structured():
    jid = uuid4()
    job = _job(jid, uuid4(), provider="gemini", model="gemini-2.5-flash")
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "retired_model"
    roles = {entry["role"]: entry for entry in detail["retired_roles"]}
    assert roles["content"]["provider"] == "gemini"
    assert roles["content"]["model"] == "gemini-2.5-flash"


def test_retry_retired_judge_role_also_returns_409():
    jid = uuid4()
    job = _job(jid, uuid4(), judge_provider="gemini", judge_model="gemini-2.5-pro")
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
    detail = r.json()["detail"]
    roles = {entry["role"] for entry in detail["retired_roles"]}
    assert roles == {"judge"}


def test_retry_live_model_job_still_retries():
    jid = uuid4()
    job = _job(jid, uuid4())  # default provider/model are live (gemini-3.5-flash)
    updated = SimpleNamespace(
        id=jid, book_id=job.book_id, toc_entry_id=uuid4(),
        subject="kimyo-g7-11", status="pending",
    )
    out = JobOut(id=updated.id, book_id=updated.book_id, toc_entry_id=updated.toc_entry_id,
                 subject=updated.subject, status=updated.status)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs.jobs_repo.reset_for_retry", AsyncMock(return_value=updated)), \
         patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_retry_still_rejects_non_terminal_status_before_retired_check():
    # Guard ordering unchanged: a running job is still rejected for being
    # running, regardless of what model it's stamped with.
    jid = uuid4()
    job = _job(jid, uuid4(), status="running", provider="gemini", model="gemini-2.5-flash")
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
    # Not the retired-model shape — the plain string-detail status guard.
    assert isinstance(r.json()["detail"], str)
