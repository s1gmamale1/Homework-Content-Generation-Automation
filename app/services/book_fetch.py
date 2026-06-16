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
from pathlib import Path
from uuid import UUID

import httpx

from app.config import settings
from app.services import storage

_TIMEOUT = 120.0  # multi-MB PDF over a LAN


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


def ensure_book_pdf_sync(book_id: UUID | str) -> Path:
    """Return the local path to the book's source PDF, fetching it from the head
    if it's missing and `fleet_head_url` is configured. Raises RuntimeError if
    the PDF cannot be produced."""
    path = storage.book_pdf_path(book_id)
    if path.exists():
        return path  # fast path: head + already-cached remote — no HTTP

    head = settings.fleet_head_url.strip()
    if not head:
        raise RuntimeError(f"Book PDF missing on disk: {path}")

    book_dir = storage.book_dir(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)  # first-time remote: no dir yet
    tmp = book_dir / f"source.pdf.{os.getpid()}.tmp"  # same fs -> atomic replace

    url = f"{head.rstrip('/')}/api/v1/books/{book_id}/source.pdf"
    token = settings.auth_token.split(",")[0].strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        _fetch_to_temp(url, headers, tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)  # handle already closed by _fetch_to_temp
        raise RuntimeError(f"fetch from head failed: {e}") from e

    os.replace(tmp, path)  # atomic; overwrites on Windows
    return path
