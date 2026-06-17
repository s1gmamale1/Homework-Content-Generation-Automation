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


def test_concurrent_same_book_fetches_use_distinct_temps(monkeypatch, tmp_path):
    """Two lessons of the SAME book in one worker process (asyncio tasks share
    the PID) fetch concurrently. Each must write to its OWN temp file — a shared
    name races and raises a sharing violation on Windows (the R13 field-test bug)."""
    import threading

    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head")
    monkeypatch.setattr(settings, "auth_token", "t")
    bid = uuid4()
    temps: list = []
    both_in_flight = threading.Barrier(2)

    def _slow(url, headers, tmp):
        temps.append(tmp)
        both_in_flight.wait(timeout=5)  # hold both calls mid-fetch simultaneously
        tmp.write_bytes(b"%PDF concurrent")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _slow)
    threads = [
        threading.Thread(target=book_fetch.ensure_book_pdf_sync, args=(bid,))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(temps) == 2
    assert temps[0] != temps[1], "concurrent same-book fetches collided on one temp file"
    assert storage.book_pdf_path(bid).exists()  # one of them won the atomic replace


# ── R13 integrity (`r13-integrity-1`): a corrupt/truncated cached PDF (wrong
# size) must NOT be trusted — re-fetch it; and a truncated download must be
# rejected before it's promoted, so corruption can't poison every job. ───────

def test_corrupt_cache_refetched_when_size_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000")
    monkeypatch.setattr(settings, "auth_token", "")
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"TRUNC")  # 5 bytes — corrupt/partial cache

    def _write_full(url, headers, tmp):
        tmp.write_bytes(b"%PDF the full correct body")  # 26 bytes == expected

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _write_full)
    got = book_fetch.ensure_book_pdf_sync(bid, expected_size=26)
    assert got == p
    assert got.read_bytes() == b"%PDF the full correct body"  # re-fetched, not the stub


def test_truncated_download_rejected_not_promoted(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000")
    monkeypatch.setattr(settings, "auth_token", "")
    bid = uuid4()

    def _write_short(url, headers, tmp):
        tmp.write_bytes(b"%PDF short")  # 10 bytes, but expected 9999

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _write_short)
    with pytest.raises(RuntimeError, match="fetch from head failed"):
        book_fetch.ensure_book_pdf_sync(bid, expected_size=9999)
    p = storage.book_pdf_path(bid)
    assert not p.exists()                              # bad bytes never promoted
    assert list(p.parent.glob("*.tmp")) == []          # temp cleaned up


def test_size_mismatch_without_head_trusts_existing(monkeypatch, tmp_path):
    """On the head (no fleet_head_url) the on-disk file is canonical — a size
    mismatch must NOT delete it (there's nowhere to re-fetch from)."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "")
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF whatever size")

    def _boom(*a, **k):
        raise AssertionError("must not fetch on the head")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _boom)
    assert book_fetch.ensure_book_pdf_sync(bid, expected_size=99999) == p
    assert p.exists()


def test_correct_size_uses_fast_path(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head:8000")
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF correct")  # 12 bytes

    def _boom(*a, **k):
        raise AssertionError("must not re-fetch when size matches")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _boom)
    assert book_fetch.ensure_book_pdf_sync(bid, expected_size=12) == p
