"""POST /books/{id}/toc/accept promotes a `toc_review` book to `toc_ready`.

Coverage:
  - accept on a `toc_review` book → 200 + returned status is `toc_ready`
  - accept on a `toc_ready` book → 409
  - accept on a missing book → 404
  - BookOut serialises toc_validation / toc_validation_detail fields
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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


def _bookout(book_id, status, *, toc_validation=None, toc_validation_detail=None):
    return BookOut(
        id=book_id,
        subject="math-algebra",
        original_filename="alg.pdf",
        status=status,
        toc_validation=toc_validation,
        toc_validation_detail=toc_validation_detail,
    )


def _accept(book_id, *, book):
    """Drive the accept endpoint with repos/helper stubbed out."""
    set_status_mock = AsyncMock()
    with patch("app.api.v1.books.books_repo.get", AsyncMock(return_value=book)), \
         patch("app.api.v1.books.books_repo.set_status", set_status_mock), \
         patch("app.api.v1.books._book_out_with_toc",
               AsyncMock(return_value=_bookout(book_id, "toc_ready"))):
        r = client.post(f"/api/v1/books/{book_id}/toc/accept")
    return r, set_status_mock


def test_accept_toc_review_book_returns_200_and_toc_ready():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_review", subject="math-algebra")
    r, set_status_mock = _accept(bid, book=book)
    assert r.status_code == 200
    assert r.json()["status"] == "toc_ready"
    # set_status must have been called with "toc_ready"
    set_status_mock.assert_awaited_once()
    assert set_status_mock.await_args.args[2] == "toc_ready"


def test_accept_toc_ready_book_returns_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_ready", subject="math-algebra")
    r, set_status_mock = _accept(bid, book=book)
    assert r.status_code == 409
    set_status_mock.assert_not_awaited()


def test_accept_missing_book_returns_404():
    bid = uuid4()
    r, set_status_mock = _accept(bid, book=None)
    assert r.status_code == 404
    set_status_mock.assert_not_awaited()


def test_bookout_serializes_validation_fields():
    """BookOut.model_dump() must include toc_validation and toc_validation_detail."""
    bid = uuid4()
    out = BookOut(
        id=bid,
        subject="math-algebra",
        original_filename="alg.pdf",
        status="toc_review",
        toc_validation="mismatch",
        toc_validation_detail="expected 12 chapters, found 10",
    )
    data = out.model_dump()
    assert data["toc_validation"] == "mismatch"
    assert data["toc_validation_detail"] == "expected 12 chapters, found 10"


def test_bookout_validation_fields_default_none():
    """toc_validation / toc_validation_detail default to None when omitted."""
    bid = uuid4()
    out = BookOut(
        id=bid,
        subject="math-algebra",
        original_filename="alg.pdf",
        status="toc_ready",
    )
    data = out.model_dump()
    assert data["toc_validation"] is None
    assert data["toc_validation_detail"] is None
