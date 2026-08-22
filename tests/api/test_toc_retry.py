"""POST /books/{id}/toc/retry re-runs TOC extraction for a book stuck in
`failed` or a hung `toc_extracting`, still under review (`toc_review`), or
already accepted (`toc_ready`, a deliberate redo). Mirrors POST /jobs/{id}/retry:
allowed statuses → 200 + status flips to `toc_extracting`, the prior
`toc_validation`/`toc_validation_detail`/`toc_ready_at` are cleared, and the
background extractor is scheduled exactly once; any other status (e.g.
`uploading`) → 409; missing book → 404; missing source PDF on disk → 409
(re-upload required); jobs referencing this book's sections → structured 409
(`{"error": "toc_retry_blocked_by_jobs", "count", "jobs"}`, never prose). The
extractor itself is stubbed so no real extraction runs."""
import pytest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user
from app.schemas import BookOut

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _bookout(book_id, status):
    return BookOut(id=book_id, subject="math-algebra",
                   original_filename="alg.pdf", status=status)


def _retry(book_id, *, book, pdf_exists=True, blocking_jobs=None):
    """Drive the endpoint with the repo/extractor/storage stubbed out. Returns
    (response, run_spy, set_status_spy, clear_spy)."""
    run_spy = AsyncMock()
    pdf_path = SimpleNamespace(exists=lambda: pdf_exists)
    with patch("app.api.v1.books.books_repo.get", AsyncMock(return_value=book)), \
         patch("app.api.v1.books.books_repo.set_status", AsyncMock()) as set_status, \
         patch("app.api.v1.books.books_repo.clear_toc_validation_and_ready",
               AsyncMock()) as clear_spy, \
         patch("app.api.v1.books.storage.book_pdf_path", return_value=pdf_path), \
         patch("app.api.v1.books.jobs_repo.list_for_book",
               AsyncMock(return_value=blocking_jobs or [])), \
         patch("app.api.v1.books.targets_repo.history_for_book",
               AsyncMock(return_value=[])), \
         patch("app.api.v1.books.toc_extractor.run", run_spy), \
         patch("app.api.v1.books._book_out_with_toc",
               AsyncMock(return_value=_bookout(book_id, "toc_extracting"))):
        r = client.post(f"/api/v1/books/{book_id}/toc/retry")
    return r, run_spy, set_status, clear_spy


def test_retry_from_failed_fires_and_sets_extracting():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, set_status, clear_spy = _retry(bid, book=book)
    assert r.status_code == 200
    assert r.json()["status"] == "toc_extracting"
    run_spy.assert_awaited_once()
    # status was flipped to toc_extracting with the old error cleared
    set_status.assert_awaited_once()
    assert set_status.await_args.args[2] == "toc_extracting"
    assert set_status.await_args.kwargs.get("error_message") is None
    # the prior validation verdict + ready stamp are cleared for the redo
    clear_spy.assert_awaited_once_with(ANY, bid)


def test_retry_from_stuck_extracting_allowed():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_extracting", subject="math-algebra")
    r, run_spy, _, _ = _retry(bid, book=book)
    assert r.status_code == 200
    run_spy.assert_awaited_once()


def test_retry_from_toc_ready_allowed():
    # toc_ready is now a legitimate redo source (system-aware "Prepare a
    # subject" dialog can offer "redo" on an already-extracted book).
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_ready", subject="math-algebra")
    r, run_spy, set_status, clear_spy = _retry(bid, book=book)
    assert r.status_code == 200
    run_spy.assert_awaited_once()
    set_status.assert_awaited_once()
    clear_spy.assert_awaited_once_with(ANY, bid)


def test_retry_from_uploading_rejected_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="uploading", subject="math-algebra")
    r, run_spy, _, clear_spy = _retry(bid, book=book)
    assert r.status_code == 409
    run_spy.assert_not_awaited()
    clear_spy.assert_not_awaited()


def test_retry_missing_book_404():
    bid = uuid4()
    r, run_spy, _, _ = _retry(bid, book=None)
    assert r.status_code == 404
    run_spy.assert_not_awaited()


def test_retry_missing_pdf_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, _, clear_spy = _retry(bid, book=book, pdf_exists=False)
    assert r.status_code == 409
    run_spy.assert_not_awaited()
    clear_spy.assert_not_awaited()


def test_retry_from_toc_review_allowed():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_review", subject="math-algebra")
    r, run_spy, _, _ = _retry(bid, book=book)
    assert r.status_code == 200
    run_spy.assert_awaited_once()


def test_retry_blocked_by_referencing_jobs_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_review", subject="math-algebra")
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done")
    r, run_spy, set_status, clear_spy = _retry(bid, book=book, blocking_jobs=[job])
    assert r.status_code == 409
    # structured detail (FE must never parse prose)
    detail = r.json()["detail"]
    assert detail["error"] == "toc_retry_blocked_by_jobs"
    assert detail["count"] == 1
    assert detail["jobs"] == [{"id": str(jid), "status": "done"}]
    # Human "message" field (Task 3 review rider), matching the
    # ambiguous_textbook {error, message, ...} convention.
    assert detail["message"] == (
        "1 homework job(s) reference this book's sections — "
        "delete the affected sections first"
    )
    # book status is NOT flipped, nothing cleared, no extraction fires
    run_spy.assert_not_awaited()
    set_status.assert_not_awaited()
    clear_spy.assert_not_awaited()


def test_retry_proceeds_when_no_referencing_jobs():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, _, _ = _retry(bid, book=book, blocking_jobs=[])
    assert r.status_code == 200
    run_spy.assert_awaited_once()


def test_retry_409_caps_job_listing_at_20_with_total():
    # B1: a full-TOC book carries 50-60+ jobs; the payload lists ~20 + the total,
    # never the whole roster. RED-provable: without the [:20] cap all 25 enumerate.
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    jobs = [SimpleNamespace(id=uuid4(), status="done") for _ in range(25)]
    r, run_spy, _, clear_spy = _retry(bid, book=book, blocking_jobs=jobs)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "toc_retry_blocked_by_jobs"
    assert detail["count"] == 25            # total count present, uncapped
    assert len(detail["jobs"]) == 20         # listing capped at 20
    assert all(j["status"] == "done" for j in detail["jobs"])
    # message uses the UNCAPPED total (25), not the capped listing length (20).
    assert detail["message"] == (
        "25 homework job(s) reference this book's sections — "
        "delete the affected sections first"
    )
    run_spy.assert_not_awaited()
    clear_spy.assert_not_awaited()
