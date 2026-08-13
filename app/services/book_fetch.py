"""Pull-on-demand delivery of a book's source PDF to a fleet worker (R13).

A worker generates by reading `<var_dir>/books/<id>/source.pdf` from local
disk. On a multi-PC fleet a worker may claim a lesson for a book whose PDF it
doesn't have. `ensure_book_pdf_sync` returns the local path, fetching the bytes
once from the head (`settings.fleet_head_url`) when they're missing, then
caching them locally. Empty `fleet_head_url` preserves the original
"raise if missing" behavior, so single-box / head are unchanged.

`ensure_book_pdf` (async) is the pipeline's entry point; `ensure_book_pdf_sync`
is the worker underneath it, kept public for the sync callers (smoke scripts).
The sync half runs off the event loop via `asyncio.to_thread` (same idiom as
`pipeline.py:673` and `notion/client.py`) — sync httpx inside an `async def`
would stall the loop that also serves the API on the head.

**Budget (2026-08-12 incident).** Every transfer here is bounded by
`settings.book_fetch_timeout_seconds`, NOT by the job timeout: waiting for
another job's in-flight fetch, blocking on the per-book lock, and the byte
stream itself all fail with `BookFetchTimeout`. httpx's own timeout is
per-operation — a stream that keeps delivering chunks slowly never trips it —
so a 237 MB book pulled by 24 workers at once ate the whole 1800 s job budget
before phase 1 and produced a bare `timeout after 1800s`.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

import httpx
from loguru import logger

from app.config import settings
from app.services import storage
from app.services.errors import BookFetchTimeout

# Per-OPERATION httpx timeout (connect / one chunk read) — deliberately NOT the
# transfer budget: a stream that trickles forever satisfies it on every read.
# The whole-transfer bound is `settings.book_fetch_timeout_seconds`.
_TIMEOUT = 120.0  # multi-MB PDF over a LAN

_book_locks: dict[str, threading.Lock] = {}
_book_locks_guard = threading.Lock()

# Per-event-loop, per-book asyncio locks (see `_async_lock_for`). Keyed weakly
# by loop so a lock is never reused across loops (asyncio.Lock binds to one).
_async_locks: WeakKeyDictionary = WeakKeyDictionary()
_async_locks_guard = threading.Lock()


def _fetch_budget_seconds() -> float:
    """Wall-clock budget for ONE fetch attempt. Read at call time so env/tests
    can move it. Separate from `settings.job_timeout_seconds` by design."""
    return float(settings.book_fetch_timeout_seconds)


def _mb(n: int | None) -> str:
    return f"{n / 1_048_576:.1f} MB" if n else "unknown size"


def _lock_for(book_id: UUID | str) -> threading.Lock:
    """Per-book_id lock so concurrent same-book fetches in one worker download
    ONCE — the first thread fetches, the rest wait then hit the cached fast path
    (r13-fetch-1). Per-process by design; cross-PC dedup is a non-goal."""
    key = str(book_id)
    with _book_locks_guard:
        lk = _book_locks.get(key)
        if lk is None:
            lk = _book_locks[key] = threading.Lock()
        return lk


def _async_lock_for(book_id: UUID | str) -> asyncio.Lock:
    """Per-book_id asyncio lock for the running loop, so the 2nd..Nth job on
    this host WAITS ON THE LOOP for the first fetch instead of each burning a
    thread-pool thread blocked on `_lock_for`'s threading.Lock. With N jobs of
    one book in flight, exactly one `to_thread` is outstanding."""
    loop = asyncio.get_running_loop()
    key = str(book_id)
    with _async_locks_guard:
        per_loop = _async_locks.get(loop)
        if per_loop is None:
            per_loop = _async_locks[loop] = {}
        lk = per_loop.get(key)
        if lk is None:
            lk = per_loop[key] = asyncio.Lock()
        return lk


@contextmanager
def _book_lock(book_id: UUID | str, expected_size: int | None):
    """`_lock_for` acquired with the fetch budget as a ceiling. An unbounded
    `with lock:` is what let a wedged leader park every other thread until the
    job timeout killed them (and the abandoned threads kept piling up across
    retries) — a waiter now fails with a named error instead."""
    budget = _fetch_budget_seconds()
    lk = _lock_for(book_id)
    started = time.monotonic()
    if not lk.acquire(timeout=budget):
        raise BookFetchTimeout(
            f"book {book_id} source.pdf fetch gave up after "
            f"{time.monotonic() - started:.0f}s waiting behind an in-flight fetch "
            f"of the same book on this host (book size {_mb(expected_size)}, "
            f"budget {budget:.0f}s / BOOK_FETCH_TIMEOUT_SECONDS)"
        )
    try:
        yield
    finally:
        lk.release()


def _warn_if_oversized(book_id: UUID | str, size: int | None) -> None:
    """Loud, non-blocking size guard. Never refuses: the goal is to make a big
    book survivable, not unsupported — refusing would just convert 35 timeouts
    into 35 rejections. Warn-only keeps normal books (<= threshold) silent."""
    threshold_mb = settings.book_fetch_warn_mb
    if not size or not threshold_mb or size <= threshold_mb * 1_048_576:
        return
    logger.warning(
        f"oversized book source.pdf: book {book_id} is {_mb(size)}, over the "
        f"{threshold_mb} MB BOOK_FETCH_WARN_MB guard — every worker missing it "
        f"pulls the whole file from the head (budget "
        f"{_fetch_budget_seconds():.0f}s). Consider pre-shrinking the PDF or a "
        f"shared VAR_DIR so the fleet stops re-transferring it."
    )


def _cached_ok(path: Path, expected_size: int | None, head: str) -> bool:
    """True when the on-disk PDF can be returned as-is. A wrong-size cache is
    'not ok' ONLY when a head is configured to re-fetch from (on the head the
    file is canonical — there's nowhere to re-pull, so it stays ok).

    Tolerates a concurrent unlink: if the file vanishes between exists() and
    stat() (another thread re-fetching a wrong-size cache under the lock), treat
    it as not-cached rather than raising out of the lock-free fast path."""
    try:
        if not path.exists():
            return False
        if expected_size and head and path.stat().st_size != expected_size:
            return False
    except OSError:
        return False
    return True


def _fetch_to_temp(url: str, headers: dict, tmp: Path) -> None:
    """GET `url` and stream the body into `tmp`. Raises on non-200 / empty /
    network error, and on `BookFetchTimeout` once the transfer outlives
    `settings.book_fetch_timeout_seconds` — `_TIMEOUT` only bounds a single
    read, so without this a slow stream runs until the JOB timeout kills it (and
    the thread keeps running and keeps holding the book lock even then). The
    `with open` closes the file before this returns or raises, so a caller can
    unlink `tmp` on Windows (can't unlink an open file)."""
    budget = _fetch_budget_seconds()
    started = time.monotonic()
    received = 0
    with httpx.Client(timeout=_TIMEOUT) as http:
        with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"head returned HTTP {resp.status_code}")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
                    received += len(chunk)
                    elapsed = time.monotonic() - started
                    if elapsed > budget:
                        rate = received / elapsed / 1024 if elapsed else 0.0
                        raise BookFetchTimeout(
                            f"transfer exceeded its {budget:.0f}s budget "
                            f"(BOOK_FETCH_TIMEOUT_SECONDS): {_mb(received)} received "
                            f"in {elapsed:.0f}s (~{rate:.0f} KB/s) from {url}"
                        )
    if tmp.stat().st_size == 0:
        raise RuntimeError("head returned an empty body")


def ensure_book_pdf_sync(book_id: UUID | str, expected_size: int | None = None) -> Path:
    """Return the local path to the book's source PDF, fetching it from the head
    if it's missing and `fleet_head_url` is configured. Raises RuntimeError if
    the PDF cannot be produced.

    `expected_size` (the book's `file_size_bytes`) enables an integrity guard
    (`r13-integrity-1`): a truncated/corrupt cached copy — e.g. an interrupted
    earlier fetch — has the WRONG size and would otherwise be reused on every
    claim, failing each job at extract (`pypdf: Cannot find Root object`). When
    it's known, a wrong-size cache is dropped and re-fetched, and a short
    download is rejected before it's promoted. None ⇒ legacy behaviour. (A
    sha256 check vs `content_sha256` would be stricter but heavier; size catches
    the observed truncation mode cheaply.)"""
    path = storage.book_pdf_path(book_id)
    head = settings.fleet_head_url.strip()
    # Lock-free fast path: already cached & valid (the common case — no lock).
    if _cached_ok(path, expected_size, head):
        return path
    # Serialize concurrent fetches of the SAME book so N lessons don't each
    # download the full PDF — first fetches, the rest wait then hit cache.
    # Bounded by the fetch budget (see `_book_lock`).
    with _book_lock(book_id, expected_size):
        if _cached_ok(path, expected_size, head):
            return path
        # wrong-size cache with a head to re-fetch from → drop it (r13-integrity-1)
        if path.exists() and expected_size and head and path.stat().st_size != expected_size:
            path.unlink(missing_ok=True)
        if not head:
            raise RuntimeError(f"Book PDF missing on disk: {path}")

        book_dir = storage.book_dir(book_id)
        book_dir.mkdir(parents=True, exist_ok=True)  # first-time remote: no dir yet
        # Unique per call. The per-book lock above now serializes same-book
        # fetches, so this is belt-and-suspenders: it keeps temps distinct for
        # any unrelated concurrent writer and avoids a stale-temp clash from an
        # earlier interrupted fetch; os.replace stays atomic.
        tmp = book_dir / f"source.pdf.{os.getpid()}.{uuid4().hex}.tmp"  # same fs -> atomic replace

        url = f"{head.rstrip('/')}/api/v1/books/{book_id}/source.pdf"
        token = settings.auth_token.split(",")[0].strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        _warn_if_oversized(book_id, expected_size)
        try:
            _fetch_to_temp(url, headers, tmp)
            # Reject a short/corrupt download BEFORE promoting it — otherwise we'd
            # just re-cache the same corruption that poisons every job (r13-integrity-1).
            if expected_size and tmp.stat().st_size != expected_size:
                raise RuntimeError(
                    f"size {tmp.stat().st_size} != expected {expected_size} (truncated?)"
                )
        except BookFetchTimeout as e:
            # Keep the distinct type (a generic "fetch from head failed" would
            # bury it) and name the book + its size, so the operator sees WHAT
            # was being transferred, not just that something timed out.
            tmp.unlink(missing_ok=True)
            raise BookFetchTimeout(
                f"book {book_id} source.pdf fetch from head timed out "
                f"(book size {_mb(expected_size)}): {e}"
            ) from e
        except Exception as e:
            tmp.unlink(missing_ok=True)  # handle already closed by _fetch_to_temp
            raise RuntimeError(f"fetch from head failed: {e}") from e

        if not expected_size:
            _warn_if_oversized(book_id, tmp.stat().st_size)
        os.replace(tmp, path)  # atomic; overwrites on Windows
        return path


async def ensure_book_pdf(book_id: UUID | str, expected_size: int | None = None) -> Path:
    """Async entry point: the local path to the book's source PDF, fetched from
    the head once per host if missing. **This is what the pipeline calls.**

    Two things it adds over `asyncio.to_thread(ensure_book_pdf_sync, ...)`:

    1. **Per-book dedup on the loop.** N jobs of one book queue on an
       `asyncio.Lock`, not on N thread-pool threads — the 2nd..Nth wait for the
       first fetch to populate the cache and then hit the fast path, so one host
       makes ONE transfer and holds ONE `to_thread` slot. (The default executor
       is `min(32, cpu+4)` threads; 24 jobs blocked in it starve every other
       `to_thread` in the process, jobs of other books included.)
    2. **Its own budget.** The whole wait+transfer is capped by
       `settings.book_fetch_timeout_seconds` and fails as `BookFetchTimeout`,
       so a fetch can no longer quietly spend a job's entire generation budget.
    """
    budget = _fetch_budget_seconds()
    started = time.monotonic()
    lock = _async_lock_for(book_id)
    acquired = False
    cm = asyncio.timeout(budget)
    try:
        async with cm:
            await lock.acquire()
            acquired = True
            return await asyncio.to_thread(ensure_book_pdf_sync, book_id, expected_size)
    except TimeoutError as exc:  # asyncio.timeout → TimeoutError (3.11+)
        if not cm.expired():
            raise  # somebody else's TimeoutError — don't relabel it as ours
        waited = time.monotonic() - started
        stage = (
            "downloading from the head"
            if acquired
            else "waiting behind an in-flight fetch of the same book on this host"
        )
        raise BookFetchTimeout(
            f"book {book_id} source.pdf fetch exceeded its {budget:.0f}s budget "
            f"(BOOK_FETCH_TIMEOUT_SECONDS) after {waited:.0f}s {stage} — book size "
            f"{_mb(expected_size)}. The job's generation budget "
            f"({settings.job_timeout_seconds}s) was NOT spent on this."
        ) from exc
    finally:
        if acquired:
            lock.release()
