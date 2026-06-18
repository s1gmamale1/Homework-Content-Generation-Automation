"""POST /books/{id}/toc/retry re-runs TOC extraction for a book stuck in
`failed` or a hung `toc_extracting`. Mirrors POST /jobs/{id}/retry: allowed
statuses → 200 + status flips to `toc_extracting` and the background extractor
is scheduled exactly once; any other status → 409; missing book → 404; missing
source PDF on disk → 409 (re-upload required). The extractor itself is stubbed
so no real extraction runs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app
from app.auth import get_current_user
from app.schemas import BookOut

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def _bookout(book_id, status):
    return BookOut(id=book_id, subject="math-algebra",
                   original_filename="alg.pdf", status=status)


def _retry(book_id, *, book, pdf_exists=True):
    """Drive the endpoint with the repo/extractor/storage stubbed out. Returns
    (response, run_spy)."""
    run_spy = AsyncMock()
    pdf_path = SimpleNamespace(exists=lambda: pdf_exists)
    with patch("app.api.v1.books.books_repo.get", AsyncMock(return_value=book)), \
         patch("app.api.v1.books.books_repo.set_status", AsyncMock()) as set_status, \
         patch("app.api.v1.books.storage.book_pdf_path", return_value=pdf_path), \
         patch("app.api.v1.books.toc_extractor.run", run_spy), \
         patch("app.api.v1.books._book_out_with_toc",
               AsyncMock(return_value=_bookout(book_id, "toc_extracting"))):
        r = client.post(f"/api/v1/books/{book_id}/toc/retry")
    return r, run_spy, set_status


def test_retry_from_failed_fires_and_sets_extracting():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, set_status = _retry(bid, book=book)
    assert r.status_code == 200
    assert r.json()["status"] == "toc_extracting"
    run_spy.assert_awaited_once()
    # status was flipped to toc_extracting with the old error cleared
    set_status.assert_awaited_once()
    assert set_status.await_args.args[2] == "toc_extracting"
    assert set_status.await_args.kwargs.get("error_message") is None


def test_retry_from_stuck_extracting_allowed():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_extracting", subject="math-algebra")
    r, run_spy, _ = _retry(bid, book=book)
    assert r.status_code == 200
    run_spy.assert_awaited_once()


def test_retry_from_ready_rejected_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_ready", subject="math-algebra")
    r, run_spy, _ = _retry(bid, book=book)
    assert r.status_code == 409
    run_spy.assert_not_awaited()


def test_retry_from_uploading_rejected_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="uploading", subject="math-algebra")
    r, run_spy, _ = _retry(bid, book=book)
    assert r.status_code == 409
    run_spy.assert_not_awaited()


def test_retry_missing_book_404():
    bid = uuid4()
    r, run_spy, _ = _retry(bid, book=None)
    assert r.status_code == 404
    run_spy.assert_not_awaited()


def test_retry_missing_pdf_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, _ = _retry(bid, book=book, pdf_exists=False)
    assert r.status_code == 409
    run_spy.assert_not_awaited()
