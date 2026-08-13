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

**Dedup is three layers deep, innermost first (r13-fetch-2).** Each layer only
exists because the one inside it cannot see the contender the next one out can:

    asyncio.Lock   (ensure_book_pdf)  — N coroutines in ONE loop → 1 to_thread
    threading.Lock (_book_lock)       — N threads   in ONE process
    flock/LK_NBLCK (_host_lock)       — N PROCESSES on ONE host

The outermost layer is the one added last. `_lock_for` and `_async_lock_for`
are per-interpreter, so a host running two worker processes still made two full
transfers of the same book — Host-18 does exactly that. The fix is an OS lock
on `source.pdf.lock` beside the cache file, and the point of it is the
RE-CHECK after acquiring: the winner promotes the file with an atomic rename,
so the loser wakes up, sees a complete `source.pdf` and never opens a socket.

The lock is an OS lock (POSIX `fcntl.flock`, Windows `msvcrt.locking`) and not
a "the lockfile exists ⇒ held" scheme precisely because the kernel drops it
when the fd closes — including when the holder is SIGKILLed or the box loses
power. A presence-based lockfile strands the book on the first killed worker
and then needs a staleness heuristic to get unstuck, which is a deadlock with
extra steps. Both platforms are polled non-blockingly rather than blocked on,
so the wait obeys the same wall-clock budget as everything else here (POSIX
`LOCK_EX` cannot be given a timeout, and Windows' blocking `LK_LOCK` has its
own hardcoded ~10 s one).
"""
from __future__ import annotations

import asyncio
import os
import sys
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

if sys.platform == "win32":  # pragma: no cover - exercised on the Windows fleet
    import msvcrt
else:  # pragma: no cover - exercised on POSIX
    import fcntl

# Per-OPERATION httpx timeout (connect / one chunk read) — deliberately NOT the
# transfer budget: a stream that trickles forever satisfies it on every read.
# The whole-transfer bound is `settings.book_fetch_timeout_seconds`.
_TIMEOUT = 120.0  # multi-MB PDF over a LAN

_book_locks: dict[str, threading.Lock] = {}
_book_locks_guard = threading.Lock()

# Poll interval bounds for the cross-process lock. Small enough that an
# uncontended hand-off is imperceptible, capped so a 10-minute wait behind a
# 237 MB transfer costs ~1200 cheap syscalls rather than 12000.
_LOCK_POLL_MIN = 0.05
_LOCK_POLL_MAX = 0.5

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
    (r13-fetch-1). Per-PROCESS: it de-dupes the threads of this interpreter and
    nothing else. The other worker processes on this host are `_host_lock`'s
    job (r13-fetch-2); cross-PC dedup remains a non-goal (that's a shared
    VAR_DIR)."""
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


def _host_lock_path(book_id: UUID | str) -> Path:
    """The cross-process lock file, next to the cache file it guards:
    ``<var_dir>/books/<id>/source.pdf.lock``.

    Beside `source.pdf` on purpose — same directory, therefore same filesystem,
    therefore the same thing the `os.replace` promotion is atomic on. A lock in
    a temp dir could be on a different mount than the file it claims to guard.

    It is created once and NEVER unlinked. Deleting it would reintroduce the
    classic unlink race: A holds the lock on inode 1, B opens the path and gets
    inode 1, A unlinks and releases, C opens the path and CREATES inode 2 — B
    and C now both "hold" the lock on different inodes and both download. An
    empty file per book directory is a much better trade."""
    return storage.book_dir(book_id) / "source.pdf.lock"


if sys.platform == "win32":  # pragma: no cover - exercised on the Windows fleet

    def _try_lock(fd: int) -> bool:
        """Non-blocking exclusive lock on byte 0. Windows byte-range locks are
        mandatory and per-HANDLE (a second handle in the same process is a real
        contender), and the kernel drops them when the handle closes — process
        death included. `msvcrt.locking` acts at the current file position, so
        seek first. Locking past EOF is legal, which is why the file can stay
        empty."""
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - exercised on POSIX

    def _try_lock(fd: int) -> bool:
        """Non-blocking exclusive `flock`. Advisory, per open-file-description
        (so a second `os.open` in this very process contends for real), and
        released by the kernel on close — including the implicit close when the
        process dies."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _host_lock(book_id: UUID | str, expected_size: int | None, deadline: float):
    """Hold the host-wide lock for one book until `deadline` (a `time.monotonic`
    stamp), or raise `BookFetchTimeout`.

    Bounded, never blocking-forever, for the same reason `_book_lock` is: the
    caller is one thread-pool thread that is ALSO holding `_book_lock`, so a
    waiter parked here parks every other thread on this book too. `deadline` is
    the caller's remaining fetch budget, not a fresh one — waiting 10 minutes
    in-process and then 10 more minutes cross-process is not a 10-minute budget.

    Costs no extra thread: `ensure_book_pdf` already funnels every coroutine for
    a book through ONE `asyncio.to_thread`, so exactly one thread per book per
    process ever reaches this line. That property is the whole reason the fetch
    stopped starving the default `min(32, cpu+4)` executor, and it survives."""
    lock_path = _host_lock_path(book_id)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        started = time.monotonic()
        delay = _LOCK_POLL_MIN
        while not _try_lock(fd):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BookFetchTimeout(
                    f"book {book_id} source.pdf fetch gave up after "
                    f"{time.monotonic() - started:.0f}s waiting behind an in-flight "
                    f"fetch of the same book by another process on this host "
                    f"(book size {_mb(expected_size)}, budget "
                    f"{_fetch_budget_seconds():.0f}s / BOOK_FETCH_TIMEOUT_SECONDS)"
                )
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _LOCK_POLL_MAX)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        # Closing releases the lock even if _unlock itself failed — this is the
        # backstop that makes "a dead holder never wedges the book" true.
        os.close(fd)


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
    # One budget for the whole call, spent across BOTH waits. Started here so
    # the cross-process wait inherits what the in-process wait left over.
    deadline = time.monotonic() + _fetch_budget_seconds()
    # Serialize concurrent fetches of the SAME book so N lessons don't each
    # download the full PDF — first fetches, the rest wait then hit cache.
    # Bounded by the fetch budget (see `_book_lock`).
    with _book_lock(book_id, expected_size):
        if _cached_ok(path, expected_size, head):
            return path
        if not head:
            # Nothing to fetch from: this IS the canonical copy's host. Bail
            # before touching the lock file — the head has no contender, and a
            # missing book here is a hard error, not a wait.
            raise RuntimeError(f"Book PDF missing on disk: {path}")

        book_dir = storage.book_dir(book_id)
        book_dir.mkdir(parents=True, exist_ok=True)  # first-time remote: no dir yet
        # Second layer out: the other WORKER PROCESSES on this box (r13-fetch-2).
        with _host_lock(book_id, expected_size, deadline):
            # The re-check that makes the wait worth anything. Whoever held the
            # lock may have just finished this exact download; `os.replace` is
            # atomic, so a `source.pdf` visible here is whole, never half-written.
            if _cached_ok(path, expected_size, head):
                return path
            # wrong-size cache with a head to re-fetch from → drop it (r13-integrity-1)
            if path.exists() and expected_size and path.stat().st_size != expected_size:
                path.unlink(missing_ok=True)
            # Unique per call. The two locks above now serialize same-book
            # fetches, so this is belt-and-suspenders: it keeps temps distinct for
            # any unrelated concurrent writer and avoids a stale-temp clash from an
            # earlier interrupted fetch; os.replace stays atomic. Bytes are only
            # ever visible at `source.pdf` via that rename, so a partial download
            # can never be read back as a cache hit.
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
       first fetch to populate the cache and then hit the fast path, so this
       PROCESS makes one transfer and holds ONE `to_thread` slot. (The default
       executor is `min(32, cpu+4)` threads; 24 jobs blocked in it starve every
       other `to_thread` in the process, jobs of other books included.)
       Collapsing the process to one thread here is also what lets
       `_host_lock` block on the filesystem without costing anything: the one
       thread that waits cross-process is the one that was already outstanding.
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
