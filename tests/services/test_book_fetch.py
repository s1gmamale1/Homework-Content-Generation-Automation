"""ensure_book_pdf_sync — return the local PDF path, fetching from the head
only when missing + configured. Network (`_fetch_to_temp`) is stubbed so the
orchestration (mkdir, atomic replace, temp cleanup) is what's under test."""
from uuid import uuid4

import pytest

from app.config import settings
from app.services import book_fetch, storage


def test_returns_existing_without_fetching(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF local")

    def _boom(*a, **k):
        raise AssertionError("must not fetch when the file exists")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _boom)
    assert book_fetch.ensure_book_pdf_sync(bid) == p


def test_missing_and_no_head_url_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "")
    with pytest.raises(RuntimeError, match="missing on disk"):
        book_fetch.ensure_book_pdf_sync(uuid4())


def test_missing_with_head_fetches_and_creates_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000")
    bid = uuid4()

    def _write(url, headers, tmp):
        # parent dir must already exist (ensure_ creates it before calling us)
        assert tmp.parent.exists()
        tmp.write_bytes(b"%PDF fetched")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _write)
    p = book_fetch.ensure_book_pdf_sync(bid)
    assert p == storage.book_pdf_path(bid)
    assert p.read_bytes() == b"%PDF fetched"
    assert list(p.parent.glob("*.tmp")) == []


def test_fetch_failure_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000")
    bid = uuid4()

    def _partial_then_fail(url, headers, tmp):
        tmp.write_bytes(b"partial")  # simulate a mid-stream write...
        raise RuntimeError("connection reset")  # ...then the connection drops

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _partial_then_fail)
    with pytest.raises(RuntimeError, match="fetch from head failed"):
        book_fetch.ensure_book_pdf_sync(bid)
    p = storage.book_pdf_path(bid)
    assert not p.exists()
    assert list(p.parent.glob("*.tmp")) == []


def test_outbound_auth_header_from_first_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000/")
    monkeypatch.setattr(settings, "auth_token", "tokA, tokB")
    bid = uuid4()
    seen = {}

    def _capture(url, headers, tmp):
        seen["url"] = url
        seen["headers"] = headers
        tmp.write_bytes(b"%PDF x")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _capture)
    book_fetch.ensure_book_pdf_sync(bid)
    assert seen["url"] == f"http://head:8000/api/v1/books/{bid}/source.pdf"
    assert seen["headers"] == {"Authorization": "Bearer tokA"}
