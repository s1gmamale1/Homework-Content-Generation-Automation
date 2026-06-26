"""Tests for r13-fetch-1: per-book_id lock de-dupes concurrent same-book fetches.

RED before the lock: N threads each call ensure_book_pdf_sync for the SAME
book_id → _fetch_to_temp is called N times (one per thread).
GREEN after the lock: the first thread fetches; the rest wait then hit the
cached fast path → _fetch_to_temp is called exactly ONCE.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest

from app.services import book_fetch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_fake_fetch(calls: list, content: bytes = b"%PDF-1.4 x"):
    """Return a _fetch_to_temp replacement that records each call and writes
    realistic bytes so the second waiter finds a valid cached file."""
    def fake_fetch(url: str, headers: dict, tmp: Path) -> None:
        calls.append(1)
        time.sleep(0.05)  # simulate I/O delay; ensures overlap between threads
        Path(tmp).write_bytes(content)
    return fake_fetch


# ---------------------------------------------------------------------------
# test 1: same book — download exactly once
# ---------------------------------------------------------------------------

def test_concurrent_same_book_fetches_download_once(monkeypatch, tmp_path):
    """N threads on the SAME book_id → _fetch_to_temp called exactly once."""
    monkeypatch.setattr("app.config.settings.var_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.fleet_head_url", "http://head.local")
    monkeypatch.setattr("app.config.settings.auth_token", "")

    calls: list = []
    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _make_fake_fetch(calls))

    book_id = str(uuid.uuid4())
    n_threads = 5
    barrier = threading.Barrier(n_threads)
    results: list[Path] = [None] * n_threads
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        barrier.wait()  # all threads start together
        try:
            results[idx] = book_fetch.ensure_book_pdf_sync(book_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(calls) == 1, (
        f"expected 1 fetch but got {len(calls)} — lock is missing or not working"
    )
    for r in results:
        assert r is not None
        assert r.read_bytes() != b""


# ---------------------------------------------------------------------------
# test 2: distinct books — each fetches independently (lock must be per-book)
# ---------------------------------------------------------------------------

def test_distinct_books_each_fetch(monkeypatch, tmp_path):
    """Two different book_ids → _fetch_to_temp called exactly twice (one per book)."""
    monkeypatch.setattr("app.config.settings.var_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.fleet_head_url", "http://head.local")
    monkeypatch.setattr("app.config.settings.auth_token", "")

    calls: list = []
    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _make_fake_fetch(calls))

    book_a = str(uuid.uuid4())
    book_b = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    results: dict[str, Path] = {}
    errors: list[Exception] = []

    def worker(bid: str) -> None:
        barrier.wait()
        try:
            results[bid] = book_fetch.ensure_book_pdf_sync(bid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(book_a,)),
        threading.Thread(target=worker, args=(book_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(calls) == 2, (
        f"expected 2 fetches (one per book) but got {len(calls)} — lock is global (wrong)"
    )
    for r in results.values():
        assert r is not None
        assert r.read_bytes() != b""
