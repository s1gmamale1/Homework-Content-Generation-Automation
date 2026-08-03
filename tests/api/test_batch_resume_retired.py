"""Batch resume (POST /jobs/batch/{id}/resume, backed by
jobs_repo.resume_failed_in_batch) must SKIP any failed/cancelled job pinned to
a retired model (gemini-2.5, retired 2026-08-03) rather than re-enqueuing it —
resume reuses the job's pinned provider/model verbatim, so resuming a
retired-stamped job would call a dead model. It re-enqueues every OTHER
failed/cancelled job in the batch and reports what it skipped.

Two layers:
  - repo layer: resume_failed_in_batch itself, against a fake session whose
    execute() hands back a mixed batch of live + retired-stamped fake job rows.
  - API layer: the endpoint maps the repo's {resumed, skipped_retired} result
    onto {jobs_resumed, jobs_skipped_retired} in the response body.
"""
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user
from app.db import get_session
from app.repositories import jobs as jobs_repo

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


def _job(**overrides):
    base = dict(
        id=uuid4(),
        provider=None, model=None,
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


# ─── repo layer ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_failed_in_batch_skips_retired_resumes_live():
    live_a = _job(provider="gemini", model="gemini-3.5-flash")
    live_b = _job(judge_provider="claude", judge_model="claude-sonnet-4-6")
    retired = _job(provider="gemini", model="gemini-2.5-flash")
    session = MagicMock()
    session.execute = AsyncMock(return_value=_FakeResult([live_a, live_b, retired]))

    with patch("app.repositories.jobs.reset_for_retry", AsyncMock()) as reset_mock:
        result = await jobs_repo.resume_failed_in_batch(session, uuid4())

    assert result["resumed"] == 2
    assert result["skipped_retired"] == [str(retired.id)]
    assert reset_mock.await_count == 2
    resumed_ids = {c.args[1] for c in reset_mock.await_args_list}
    assert resumed_ids == {live_a.id, live_b.id}


@pytest.mark.asyncio
async def test_resume_failed_in_batch_all_retired_resumes_none():
    retired_a = _job(provider="gemini", model="gemini-2.5-pro")
    retired_b = _job(solver_provider="gemini", solver_model="gemini-2.5-flash-lite")
    session = MagicMock()
    session.execute = AsyncMock(return_value=_FakeResult([retired_a, retired_b]))

    with patch("app.repositories.jobs.reset_for_retry", AsyncMock()) as reset_mock:
        result = await jobs_repo.resume_failed_in_batch(session, uuid4())

    assert result["resumed"] == 0
    assert set(result["skipped_retired"]) == {str(retired_a.id), str(retired_b.id)}
    reset_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_failed_in_batch_empty_batch():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_FakeResult([]))
    result = await jobs_repo.resume_failed_in_batch(session, uuid4())
    assert result == {"resumed": 0, "skipped_retired": []}


# ─── API layer ───────────────────────────────────────────────────────────

def _make_session_override(batch_obj):
    async def _fake_get_session():
        session = MagicMock()
        session.get = AsyncMock(return_value=batch_obj)
        session.commit = AsyncMock()
        yield session

    return _fake_get_session


def test_resume_batch_endpoint_reports_resumed_and_skipped_retired():
    bid = uuid4()
    fake_batch = SimpleNamespace(id=bid, book_id=uuid4())
    skipped_id = str(uuid4())

    app.dependency_overrides[get_session] = _make_session_override(fake_batch)
    try:
        with patch(
            "app.api.v1.batch.jobs_repo.resume_failed_in_batch",
            AsyncMock(return_value={"resumed": 2, "skipped_retired": [skipped_id]}),
        ):
            r = client.post(f"/api/v1/jobs/batch/{bid}/resume")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert r.status_code == 200
    data = r.json()
    assert data["jobs_resumed"] == 2
    assert data["jobs_skipped_retired"] == [skipped_id]
