"""Pull-on-demand delivery of a book's source PDF to a fleet worker (R13).

A worker generates by reading `<var_dir>/books/<id>/source.pdf` from local
disk. On a multi-PC fleet a worker may claim a lesson for a book whose PDF it
doesn't have. `ensure_book_pdf_sync` returns the local path, fetching the bytes
once from the head (`settings.fleet_head_url`) when they're missing, then
caching them locally. Empty `fleet_head_url` preserves the original
"raise if missing" behavior, so single-box / head are unchanged.

Synchronous on purpose; the async pipeline calls it via `asyncio.to_thread`
(same idiom as `pipeline.py:673` and `notion/client.py`). Sync httpx inside an
`async def` would stall the loop that also serves the API on the head.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.services import storage

_TIMEOUT = 120.0  # multi-MB PDF over a LAN

_book_locks: dict[str, threading.Lock] = {}
_book_locks_guard = threading.Lock()


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


def _cached_ok(path: Path, expected_size: int | None, head: str) -> bool:
    """True when the on-disk PDF can be returned as-is. A wrong-size cache is
    'not ok' ONLY when a head is configured to re-fetch from (on the head the
    file is canonical — there's nowhere to re-pull, so it stays ok)."""
    if not path.exists():
        return False
    if expected_size and head and path.stat().st_size != expected_size:
        return False
    return True


def _fetch_to_temp(url: str, headers: dict, tmp: Path) -> None:
    """GET `url` and stream the body into `tmp`. Raises on non-200 / empty /
    network error. The `with open` closes the file before this returns or
    raises, so a caller can unlink `tmp` on Windows (can't unlink an open file).
    """
    with httpx.Client(timeout=_TIMEOUT) as http:
        with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"head returned HTTP {resp.status_code}")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
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
    with _lock_for(book_id):
        if _cached_ok(path, expected_size, head):
            return path
        # wrong-size cache with a head to re-fetch from → drop it (r13-integrity-1)
        if path.exists() and expected_size and head and path.stat().st_size != expected_size:
            path.unlink(missing_ok=True)
        if not head:
            raise RuntimeError(f"Book PDF missing on disk: {path}")

        book_dir = storage.book_dir(book_id)
        book_dir.mkdir(parents=True, exist_ok=True)  # first-time remote: no dir yet
        # Unique per call: the PID alone collides when one worker process runs
        # several lessons of the SAME book concurrently (asyncio tasks share the
        # PID) — they'd race on one temp file (a sharing violation on Windows).
        # uuid4 makes each fetch's temp distinct; os.replace stays atomic.
        tmp = book_dir / f"source.pdf.{os.getpid()}.{uuid4().hex}.tmp"  # same fs -> atomic replace

        url = f"{head.rstrip('/')}/api/v1/books/{book_id}/source.pdf"
        token = settings.auth_token.split(",")[0].strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            _fetch_to_temp(url, headers, tmp)
            # Reject a short/corrupt download BEFORE promoting it — otherwise we'd
            # just re-cache the same corruption that poisons every job (r13-integrity-1).
            if expected_size and tmp.stat().st_size != expected_size:
                raise RuntimeError(
                    f"size {tmp.stat().st_size} != expected {expected_size} (truncated?)"
                )
        except Exception as e:
            tmp.unlink(missing_ok=True)  # handle already closed by _fetch_to_temp
            raise RuntimeError(f"fetch from head failed: {e}") from e

        os.replace(tmp, path)  # atomic; overwrites on Windows
        return path
