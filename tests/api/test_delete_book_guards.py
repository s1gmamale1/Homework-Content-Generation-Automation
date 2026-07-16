"""DELETE /books/{id} guards (BE-02 task 2). Order matters: (1) fetch the
book — 404 if missing, so a missing book never gets masked by the status/jobs
guards below; (2) status guard — `uploading`/`toc_extracting` mean the live
`_TOC_TASKS` extractor is still using the on-disk PDF, so deletion is refused
with "still being ingested" wording; `failed`/`toc_review`/`toc_ready` are
fine (the wedged-book escape hatch); (3) active-jobs guard — a book with any
`pending`/`running`/`cancelling` job is refused (the worker/heartbeat could be
mid-spawn against files this delete would remove); only once all three gates
pass does books_repo.delete run. Repo/session are mocked per the existing
tests/api convention (see test_toc_retry.py) — no real DB needed."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _delete(book_id, *, book, active_count=0):
    """Drive the endpoint with books_repo.get/delete and jobs_repo's active-job
    count stubbed out. Returns (response, delete_spy)."""
    delete_spy = AsyncMock(return_value=True)
    with patch("app.api.v1.books.books_repo.get", AsyncMock(return_value=book)), \
         patch("app.api.v1.books.books_repo.delete", delete_spy), \
         patch("app.api.v1.books.jobs_repo.count_active_for_book",
               AsyncMock(return_value=active_count)):
        r = client.delete(f"/api/v1/books/{book_id}")
    return r, delete_spy


def test_delete_missing_book_404():
    bid = uuid4()
    r, delete_spy = _delete(bid, book=None)
    assert r.status_code == 404
    delete_spy.assert_not_awaited()


def test_delete_uploading_book_409_still_being_ingested():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="uploading")
    r, delete_spy = _delete(bid, book=book)
    assert r.status_code == 409
    assert "still being ingested" in r.json()["detail"]
    delete_spy.assert_not_awaited()


def test_delete_toc_extracting_book_409_still_being_ingested():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_extracting")
    r, delete_spy = _delete(bid, book=book)
    assert r.status_code == 409
    assert "still being ingested" in r.json()["detail"]
    delete_spy.assert_not_awaited()


@pytest.mark.parametrize("status", ["failed", "toc_review", "toc_ready"])
def test_delete_wedged_or_ready_statuses_proceed_204(status):
    bid = uuid4()
    book = SimpleNamespace(id=bid, status=status)
    r, delete_spy = _delete(bid, book=book, active_count=0)
    assert r.status_code == 204
    delete_spy.assert_awaited_once()


@pytest.mark.parametrize("active_count", [1, 2, 5])
def test_delete_with_active_jobs_409(active_count):
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_ready")
    r, delete_spy = _delete(bid, book=book, active_count=active_count)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert f"{active_count} active job(s)" in detail
    assert "pending/running/cancelling" in detail
    assert "cancel the active job(s) or their batch first" in detail
    delete_spy.assert_not_awaited()


def test_delete_with_only_terminal_jobs_204():
    # done/failed/cancelled jobs only -> count_active_for_book returns 0 ->
    # deletion proceeds.
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_ready")
    r, delete_spy = _delete(bid, book=book, active_count=0)
    assert r.status_code == 204
    delete_spy.assert_awaited_once()
