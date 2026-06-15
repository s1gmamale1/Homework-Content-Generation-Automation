"""Unit tests for the book-storage path helper — proves `settings.var_dir` is
actually honored (it was a dead setting until 2026-06-12; both the write and
read paths hardcoded "var")."""

from uuid import uuid4

from app.config import settings
from app.services import storage


def test_book_pdf_path_default_var_dir():
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    assert p.as_posix() == f"var/books/{bid}/source.pdf"


def test_book_pdf_path_honors_var_dir(monkeypatch):
    """The whole point of the fix: overriding VAR_DIR moves the storage root."""
    monkeypatch.setattr(settings, "var_dir", "/mnt/shared")
    bid = uuid4()
    assert storage.book_pdf_path(bid).as_posix() == f"/mnt/shared/books/{bid}/source.pdf"
    assert storage.book_dir(bid).as_posix() == f"/mnt/shared/books/{bid}"


def test_book_pdf_path_accepts_str_id():
    assert storage.book_pdf_path("abc").as_posix() == "var/books/abc/source.pdf"
