"""The generic `POST /jobs/{id}/retry` must refuse a regeneration revision.

A revision job is owned by its campaign target. The generic retry only resets
the *job* row to `pending`; it knows nothing about
`regeneration_targets.status`. Reconciliation runs at the end of the retried
run, by which point the target sits at `generation_failed` and
`_TARGET_TRANSITIONS` has no legal edge from there to `publication_pending` —
so even a *successful* generic retry wedges the target permanently, with a
finished packet behind it and no sweep able to repair it
(`_REPAIRABLE_TARGET_STATUSES` excludes `generation_failed`).

The route therefore refuses SYNCHRONOUSLY — before `reset_for_retry` or any
other status/schedule mutation — pointing the operator at the regeneration
retry workflow, mirroring the archive-route refusal in
`tests/api/test_regeneration_archive_isolation.py`. Ordinary failed/cancelled
jobs are untouched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import jobs as jobs_api
from app.api.v1.jobs import JobOut
from app.auth import get_current_user
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _job(jid, *, status="failed", revision_of_job_id=None, target_id=None):
    return SimpleNamespace(
        id=jid, book_id=uuid4(), status=status,
        provider="gemini", model="gemini-3.5-flash",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        revision_of_job_id=revision_of_job_id,
        regeneration_target_id=target_id,
    )


def _out(jid, status="pending"):
    return JobOut(
        id=jid, book_id=uuid4(), toc_entry_id=uuid4(),
        subject="kimyo-g7-11", status=status)


@pytest.fixture()
def spies(monkeypatch):
    """Patch every mutation / background affordance the route could reach.

    `asyncio.create_task` is patched on the module's own `asyncio` reference
    (the real module), so these tests drive the route function DIRECTLY rather
    than through `TestClient` — the test client's own event-loop portal calls
    `create_task` internally and would otherwise blow up.
    """
    reset = AsyncMock()
    archive = AsyncMock()
    create_task = MagicMock()
    job_out = AsyncMock(return_value=_out(uuid4()))
    monkeypatch.setattr(jobs_api.jobs_repo, "reset_for_retry", reset)
    monkeypatch.setattr(jobs_api.notion_archive, "archive_job", archive)
    monkeypatch.setattr(jobs_api.asyncio, "create_task", create_task)
    monkeypatch.setattr(jobs_api, "_job_out", job_out)
    session = AsyncMock()
    session.expire = MagicMock()
    return SimpleNamespace(
        reset=reset, archive=archive, create_task=create_task,
        job_out=job_out, session=session)


def _assert_nothing_happened(spies):
    spies.reset.assert_not_awaited()
    spies.archive.assert_not_awaited()
    assert spies.create_task.call_args_list == []
    assert spies.session.commit.await_count == 0
    assert jobs_api._FORCE_REARCHIVE_TASKS == {}


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_generic_retry_refuses_a_revision_synchronously(spies, monkeypatch, status):
    jid, target_id = uuid4(), uuid4()
    job = _job(jid, status=status, revision_of_job_id=uuid4(), target_id=target_id)
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    with pytest.raises(HTTPException) as exc:
        await jobs_api.retry_job(jid, session=spies.session, user={})
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["error"] == "regeneration_revision"
    assert "regeneration retry" in detail["message"]
    assert detail["job_id"] == str(jid)
    assert detail["regeneration_target_id"] == str(target_id)
    _assert_nothing_happened(spies)


async def test_generic_retry_refuses_a_revision_BEFORE_the_status_guard(
    spies, monkeypatch
):
    """A revision in any other status is refused AS A REVISION, not reported as
    a plain wrong-status job — the operator must be sent to the right route."""
    jid = uuid4()
    job = _job(jid, status="done", revision_of_job_id=uuid4())
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    with pytest.raises(HTTPException) as exc:
        await jobs_api.retry_job(jid, session=spies.session, user={})
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "regeneration_revision"
    _assert_nothing_happened(spies)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_ordinary_job_retry_still_reaches_the_repository(
    spies, monkeypatch, status
):
    jid = uuid4()
    job = _job(jid, status=status)
    spies.reset.return_value = SimpleNamespace(id=jid)
    spies.job_out.return_value = _out(jid)
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    got = await jobs_api.retry_job(jid, session=spies.session, user={})
    assert got.status == "pending"
    spies.reset.assert_awaited_once_with(spies.session, jid)
    assert spies.session.commit.await_count == 1


def test_revision_refusal_serializes_as_a_structured_409_over_http():
    """The detail is a dict, not a string — clients branch on `error`."""
    jid, target_id = uuid4(), uuid4()
    job = _job(jid, status="failed", revision_of_job_id=uuid4(), target_id=target_id)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs._job_out", AsyncMock(return_value=_out(jid))), \
         patch("app.api.v1.jobs.jobs_repo.reset_for_retry",
               AsyncMock(return_value=SimpleNamespace(id=jid))) as reset:
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "regeneration_revision"
    assert r.json()["detail"]["regeneration_target_id"] == str(target_id)
    reset.assert_not_awaited()
