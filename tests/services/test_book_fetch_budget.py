"""Fleet-capacity guards on the R13 source.pdf fetch.

Production incident 2026-08-12 — one generation run, six books:

    book             PDF size   done  failed  timeouts
    adabiyot g10     237.2 MB      0      35        16
    chizmachilik g8   19.1 MB     29       5         0
    english g10       19.5 MB     29       1         0
    adabiyot g11       3.3 MB     38       0         0
    chqbt g11          2.1 MB     46       0         0
    adabiyot g7        1.7 MB     43       1         2

24 workers each pulled the same 237 MB file at once (~5.7 GB through one relay
hop). 21 of 24 running jobs sat at ``current_phase IS NULL`` for ~15 min — the
fetch happens before phase 1 — then died on ``JOB_TIMEOUT_SECONDS`` (1800 s),
retried, and repeated. Zero lessons, ~18-24 worker slots gone.

What these tests pin down:
  * N jobs of one book on one host ⇒ ONE transfer and ONE thread-pool thread.
  * A fetch that outlives ``book_fetch_timeout_seconds`` raises the distinct
    ``BookFetchTimeout`` naming the book and its size — it does NOT quietly
    spend the job's generation budget.
  * An oversized book is announced loudly and still fetched (warn, never refuse).
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path

import pytest
from loguru import logger

from app.config import settings
from app.services import book_fetch, storage
from app.services.errors import BookFetchError, BookFetchTimeout

# The two books from the incident table, in bytes.
_ADABIYOT_G10 = 248_725_094  # 237.2 MB — the one that sank the fleet
_ENGLISH_G10 = 20_447_232    # 19.5 MB — healthy (29 done / 1 failed / 0 timeouts)


@pytest.fixture
def fleet_worker(monkeypatch, tmp_path):
    """A fleet worker with an empty local PDF cache, pointed at a head."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")
    return tmp_path


@pytest.fixture
def warnings_seen():
    """Collect loguru WARNING+ messages emitted inside the test."""
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(m.record["message"]), level="WARNING")
    try:
        yield records
    finally:
        logger.remove(sink_id)


# ── 1. per-host dedup: N concurrent jobs ⇒ one transfer, one thread ──────────

async def test_concurrent_same_book_jobs_make_one_transfer_and_hold_one_thread(
    monkeypatch, fleet_worker
):
    """The mechanism of the incident: every job of the book opened its own
    full-file transfer. Now the 2nd..Nth wait on the loop for the first.

    Two assertions, both load-bearing:
      * ``len(calls) == 1``     — one download per host, not N (~5.7 GB → 237 MB).
      * ``peak_in_thread == 1`` — the waiters wait on an asyncio.Lock, not on N
        thread-pool threads. The default executor is ``min(32, cpu+4)`` threads;
        24 jobs blocked inside it starve every other ``asyncio.to_thread`` in the
        process, including jobs of the *other* five books.
    """
    calls: list[str] = []
    in_thread = 0
    peak_in_thread = 0
    guard = threading.Lock()
    real_sync = book_fetch.ensure_book_pdf_sync

    def _slow_transfer(url: str, headers: dict, tmp: Path) -> None:
        calls.append(url)
        time.sleep(0.2)  # long enough for every other job to pile up behind it
        Path(tmp).write_bytes(b"%PDF-1.4 body")

    def _tracked_sync(book_id, expected_size=None):
        nonlocal in_thread, peak_in_thread
        with guard:
            in_thread += 1
            peak_in_thread = max(peak_in_thread, in_thread)
        try:
            return real_sync(book_id, expected_size)
        finally:
            with guard:
                in_thread -= 1

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _slow_transfer)
    monkeypatch.setattr(book_fetch, "ensure_book_pdf_sync", _tracked_sync)

    book_id = str(uuid.uuid4())
    paths = await asyncio.gather(
        *[book_fetch.ensure_book_pdf(book_id) for _ in range(8)]
    )

    assert len(calls) == 1, (
        f"8 jobs of one book opened {len(calls)} transfers — the per-host fetch "
        f"lock is missing or not keyed by book_id"
    )
    assert peak_in_thread == 1, (
        f"{peak_in_thread} fetches were in the thread pool at once — waiters must "
        f"queue on the event loop, not burn a thread each"
    )
    expected = storage.book_pdf_path(book_id)
    assert all(p == expected for p in paths)
    assert expected.read_bytes() == b"%PDF-1.4 body"


async def test_distinct_books_are_not_serialized_against_each_other(
    monkeypatch, fleet_worker
):
    """The lock is per book_id: two different books still fetch concurrently."""
    calls: list[str] = []

    def _transfer(url: str, headers: dict, tmp: Path) -> None:
        calls.append(url)
        time.sleep(0.05)
        Path(tmp).write_bytes(b"%PDF-1.4 body")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _transfer)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    await asyncio.gather(book_fetch.ensure_book_pdf(a), book_fetch.ensure_book_pdf(b))
    assert len(calls) == 2


# ── 2. the fetch has its own budget, and its own error ──────────────────────

async def test_fetch_over_budget_raises_the_distinct_error_not_the_job_timeout(
    monkeypatch, fleet_worker
):
    """A stalled fetch must fail fast on ITS budget and say so. Before this it
    consumed all 1800 s of ``JOB_TIMEOUT_SECONDS`` and surfaced as a bare
    ``timeout after 1800s`` with ``current_phase=NULL``."""
    monkeypatch.setattr(settings, "book_fetch_timeout_seconds", 0.3)
    release = threading.Event()

    def _stalled_transfer(url: str, headers: dict, tmp: Path) -> None:
        release.wait(timeout=10)  # the trickle that used to run until the job died
        Path(tmp).write_bytes(b"%PDF-1.4 body")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _stalled_transfer)
    book_id = str(uuid.uuid4())

    started = time.monotonic()
    try:
        with pytest.raises(BookFetchTimeout) as excinfo:
            await book_fetch.ensure_book_pdf(book_id, expected_size=_ADABIYOT_G10)
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert elapsed < 5, (
        f"the fetch ran {elapsed:.1f}s against a 0.3s budget — it is still being "
        f"bounded by the job timeout, not its own"
    )
    assert isinstance(excinfo.value, BookFetchError)
    message = str(excinfo.value)
    assert book_id in message, message          # WHICH book
    assert "237.2 MB" in message, message       # and how big it is
    assert "BOOK_FETCH_TIMEOUT_SECONDS" in message, message  # which knob to turn
    assert "source.pdf fetch" in message, message


def test_trickling_transfer_stops_at_its_own_budget(monkeypatch, fleet_worker):
    """The transfer loop itself is bounded, not just the awaiting coroutine.

    This is the real 237 MB failure mode: httpx's timeout is per-operation, so a
    stream that keeps delivering chunks slowly never trips it, and cancelling the
    coroutine does not stop the thread — it keeps downloading and keeps holding
    the book lock. The chunk loop now watches the clock itself.
    """
    monkeypatch.setattr(settings, "book_fetch_timeout_seconds", 0.3)

    class _TricklingResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def iter_bytes(self):
            # Bounded (~4 s) so the pre-fix code fails this test instead of
            # hanging the suite forever.
            for _ in range(400):
                time.sleep(0.01)
                yield b"x" * 1024

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def stream(self, _method, _url, headers=None):
            return _TricklingResponse()

    monkeypatch.setattr(book_fetch.httpx, "Client", _FakeClient)
    book_id = uuid.uuid4()

    started = time.monotonic()
    with pytest.raises(BookFetchTimeout) as excinfo:
        book_fetch.ensure_book_pdf_sync(book_id, expected_size=_ADABIYOT_G10)
    elapsed = time.monotonic() - started

    assert elapsed < 3, f"the trickling transfer ran {elapsed:.1f}s past a 0.3s budget"
    message = str(excinfo.value)
    assert "237.2 MB" in message, message   # the book size
    assert "KB/s" in message, message       # what the transfer was actually doing
    path = storage.book_pdf_path(book_id)
    assert not path.exists(), "a timed-out transfer must never be promoted"
    assert list(path.parent.glob("*.tmp")) == [], "temp not cleaned up"


def test_waiting_behind_an_in_flight_fetch_is_bounded(monkeypatch, fleet_worker):
    """A thread waiting on the per-book lock is bounded too. Unbounded, a wedged
    leader parked every other thread until the job timeout killed it — and each
    retry piled another blocked thread on top."""
    monkeypatch.setattr(settings, "book_fetch_timeout_seconds", 0.3)
    book_id = uuid.uuid4()

    def _must_not_transfer(*_a, **_k):
        raise AssertionError("must not transfer while another fetch holds the book")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _must_not_transfer)

    lock = book_fetch._lock_for(book_id)
    lock.acquire()  # stand-in for the leader mid-transfer
    # Pre-fix, the waiter simply sat here; release late so that shows up as a
    # failed assertion rather than a hung test.
    releaser = threading.Timer(3.0, lock.release)
    releaser.start()
    try:
        started = time.monotonic()
        with pytest.raises(BookFetchTimeout, match="waiting behind an in-flight fetch"):
            book_fetch.ensure_book_pdf_sync(book_id, expected_size=_ADABIYOT_G10)
        elapsed = time.monotonic() - started
        assert elapsed < 2.5, f"waited {elapsed:.1f}s on a 0.3s budget"
    finally:
        releaser.cancel()
        if lock.locked():
            lock.release()


# ── 3. size guard: loud, never refusing ─────────────────────────────────────

def test_size_guard_warns_only_above_the_threshold(monkeypatch, warnings_seen):
    """Generous default (100 MB): the five healthy books in the incident were
    1.7-19.5 MB and must stay silent; the 237.2 MB one must be announced."""
    monkeypatch.setattr(settings, "book_fetch_warn_mb", 100)

    book_fetch._warn_if_oversized(uuid.uuid4(), _ENGLISH_G10)
    assert warnings_seen == [], "a normal book must not change behaviour at all"

    big = uuid.uuid4()
    book_fetch._warn_if_oversized(big, _ADABIYOT_G10)
    assert len(warnings_seen) == 1
    message = warnings_seen[0]
    assert str(big) in message
    assert "237.2 MB" in message
    assert "BOOK_FETCH_WARN_MB" in message


def test_oversized_book_is_warned_about_but_still_fetched(
    monkeypatch, fleet_worker, warnings_seen
):
    """Warn, never refuse — the goal is to make a big book survivable, not
    unsupported. Refusing would only convert 35 timeouts into 35 rejections."""
    monkeypatch.setattr(settings, "book_fetch_warn_mb", 100)
    transfers: list[str] = []

    def _transfer(url: str, headers: dict, tmp: Path) -> None:
        transfers.append(url)
        Path(tmp).write_bytes(b"%PDF-1.4 body")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _transfer)
    book_id = uuid.uuid4()

    # (the stub body is short, so the existing r13-integrity size check rejects
    # it — irrelevant here; what matters is that the transfer was ATTEMPTED)
    with pytest.raises(RuntimeError):
        book_fetch.ensure_book_pdf_sync(book_id, expected_size=_ADABIYOT_G10)

    assert transfers, "an oversized book must still be fetched, never refused"
    assert any("237.2 MB" in m and str(book_id) in m for m in warnings_seen), warnings_seen
