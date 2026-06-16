# R13 Pull-on-demand source PDFs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any fleet worker fetch a book's `source.pdf` from the head over HTTP when it's missing locally, so multi-PC generation stops failing at `extract` with `Book PDF missing on disk`.

**Architecture:** A new auth-gated `GET /api/v1/books/{id}/source.pdf` endpoint on the head streams the file. A new **synchronous** helper `book_fetch.ensure_book_pdf_sync` returns the local path, fetching from `settings.fleet_head_url` (atomic temp→`os.replace`) only when the file is absent. The pipeline's existing read-site calls it via `asyncio.to_thread` (matching `pipeline.py:673`). Empty `fleet_head_url` = today's behavior (raise on missing), so single-box/head are unchanged.

**Tech Stack:** Python, FastAPI, `httpx` (sync `Client`, already a dep at `pyproject.toml:8`), pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-06-16-r13-pull-on-demand-pdf-design.md`.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `app/config.py` | add `fleet_head_url: str = ""` | the opt-in knob (env `FLEET_HEAD_URL`) |
| `app/api/v1/books.py` | add `GET /{book_id}/source.pdf` route | head serves raw PDF bytes (auth auto-applied at include) |
| `app/services/book_fetch.py` | **new** | exists-check / fetch-from-head / atomic-write / cleanup helper |
| `app/services/pipeline.py:103–105` | swap read-site to the helper | one-line wiring, off-loop via `to_thread` |
| `tests/services/test_book_fetch.py` | **new** | unit-test the helper (network stubbed) |
| `tests/api/test_book_source_pdf.py` | **new** | endpoint: 200/404/401 |

---

## Task 1: Config knob `fleet_head_url`

**Files:**
- Modify: `app/config.py` (the `Settings` class, near `var_dir` at line ~123)

- [ ] **Step 1: Add the field**

In `app/config.py`, inside the `Settings` class, immediately after the `var_dir` line, add:

```python
    # Fleet R13 — base URL of the head's API (e.g. "http://192.168.1.69:8000").
    # When a worker is missing a book's source.pdf, it fetches it from here.
    # EMPTY (default) = no fetch: a missing PDF raises as before, so single-box
    # and the head's own embedded worker are unchanged. Set on remote workers.
    fleet_head_url: str = ""
```

- [ ] **Step 2: Verify it loads with the right default**

Run: `uv run python -c "from app.config import settings; assert settings.fleet_head_url == '', repr(settings.fleet_head_url); print('OK')"`
Expected: prints `OK` (no assertion error). The field is exercised behaviorally in Task 3.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(r13): add fleet_head_url setting (empty = today's behavior)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Head endpoint — serve `source.pdf`

**Files:**
- Modify: `app/api/v1/books.py` (add a route after `get_book`, ~line 172; add a `FileResponse` import)
- Test: `tests/api/test_book_source_pdf.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_book_source_pdf.py`:

```python
"""GET /api/v1/books/{id}/source.pdf — the head serves raw PDF bytes so a
remote worker can pull-on-demand (R13). File-presence only; no DB lookup."""
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.services import storage

_HDR = {"Authorization": "Bearer 123"}


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_serves_pdf_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 hello")
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{bid}/source.pdf", headers=_HDR)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 hello"
    assert r.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_404_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{uuid4()}/source.pdf", headers=_HDR)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_401_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{uuid4()}/source.pdf")
    assert r.status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/api/test_book_source_pdf.py -q`
Expected: FAIL — the 200 test gets 404 (route missing); the 404 test may pass incidentally; treat the suite as red until the route exists.

- [ ] **Step 3: Add the import**

In `app/api/v1/books.py`, after the existing FastAPI import line (line 7), add:

```python
from fastapi.responses import FileResponse
```

- [ ] **Step 4: Add the route**

In `app/api/v1/books.py`, immediately after the `get_book` function (the `@router.get("/{book_id}")` block ending ~line 172), add:

```python
@router.get("/{book_id}/source.pdf")
async def get_book_source_pdf(book_id: UUID):
    """Serve a book's raw source PDF so a remote fleet worker that's missing the
    bytes can fetch it on demand (ROADMAP R13). Auth is applied at the
    router-include level. File-presence only (no DB lookup) — a worker only
    asks for books in the shared DB it is already working from."""
    path = storage.book_pdf_path(book_id)
    if not path.exists():
        raise HTTPException(404, "source PDF not found")
    return FileResponse(path, media_type="application/pdf", filename="source.pdf")
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run python -m pytest tests/api/test_book_source_pdf.py -q`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/books.py tests/api/test_book_source_pdf.py
git commit -m "feat(r13): serve book source.pdf for worker pull-on-demand

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Fetch helper `book_fetch.ensure_book_pdf_sync`

**Files:**
- Create: `app/services/book_fetch.py`
- Test: `tests/services/test_book_fetch.py` (new)

**Windows note (load-bearing):** the fleet runs Windows workers, and on Windows you **cannot unlink an open file**. The body write MUST be a `with open(tmp, "wb")` block that closes before any cleanup; the network read lives in a `_fetch_to_temp` seam so the orchestration (mkdir / atomic replace / temp cleanup) is unit-testable with the network stubbed. `os.replace` is atomic and overwrites on Windows.

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_book_fetch.py`:

```python
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
    # no leftover temp files
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
    assert not p.exists()                       # no half-written final file
    assert list(p.parent.glob("*.tmp")) == []   # temp cleaned up


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_book_fetch.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.book_fetch` (or attribute errors).

- [ ] **Step 3: Implement the helper**

Create `app/services/book_fetch.py`:

```python
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
    tmp = book_dir / f"source.pdf.{os.getpid()}.tmp"  # same fs → atomic replace

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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run python -m pytest tests/services/test_book_fetch.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/book_fetch.py tests/services/test_book_fetch.py
git commit -m "feat(r13): book_fetch.ensure_book_pdf_sync — pull PDF from head when missing

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Wire the pipeline read-site to the helper

**Files:**
- Modify: `app/services/pipeline.py:18` (import) and `pipeline.py:103–105` (the swap)

This is a mechanical wiring swap; the behavior is fully covered by Task 3's tests. Verification = clean import + the existing suite stays green (no new red test, because the read-site lives inside a large pipeline function with no cheap unit seam — delegating to the tested helper is the point).

- [ ] **Step 1: Add the import**

In `app/services/pipeline.py`, change line 18 from:

```python
from app.services import agent, events_bus, failure_classifier, notion_archive, phase_judge, storage
```

to (add `book_fetch`):

```python
from app.services import agent, book_fetch, events_bus, failure_classifier, notion_archive, phase_judge, storage
```

- [ ] **Step 2: Swap the read-site**

In `app/services/pipeline.py`, replace lines 103–105:

```python
        pdf_path = storage.book_pdf_path(book_id)
        if not pdf_path.exists():
            raise RuntimeError(f"Book PDF missing on disk: {pdf_path}")
```

with:

```python
        # Local on-disk PDF; on a multi-PC fleet a worker may be missing it, so
        # fetch-on-demand from the head (R13). Sync helper off the event loop —
        # same idiom as read_whole_book_text below. Raises if it can't produce it.
        pdf_path = await asyncio.to_thread(book_fetch.ensure_book_pdf_sync, book_id)
```

(Keep the existing comment block above line 100 or fold it in; the key is the `raise` is gone and the helper owns exists/fetch/raise.)

- [ ] **Step 3: Verify the module imports cleanly**

Run: `uv run python -c "import app.services.pipeline; print('import OK')"`
Expected: prints `import OK` (no ImportError / NameError).

- [ ] **Step 4: Confirm the swap landed**

Run: `grep -n "ensure_book_pdf_sync\|Book PDF missing on disk" app/services/pipeline.py`
Expected: `ensure_book_pdf_sync` appears in `pipeline.py`; the old `raise ... Book PDF missing on disk` is gone from `pipeline.py` (it now lives only in `book_fetch.py`).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `uv run python -m pytest tests/ -q`
Expected: green except the pre-existing Notion-network failures noted in prior worklogs (`5 failed (Notion)` is the known baseline; nothing new should fail). If anything in `tests/services/` or `tests/api/` newly fails, stop and investigate.

- [ ] **Step 6: Commit**

```bash
git add app/services/pipeline.py
git commit -m "feat(r13): pipeline fetches missing book PDF from head via book_fetch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Acceptance smoke + finish

**Files:** none (verification + docs)

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: only the known pre-existing Notion failures; all R13 tests (8 new) pass.

- [ ] **Step 2: Localhost two-process acceptance smoke**

This proves the real httpx path + endpoint end-to-end (not stubbed). It is infra/PDF-delivery, not generation content, so the CLAUDE.md "real CLI smoke" gate does not apply.

1. Pick a book that has a PDF on disk (or upload one) and note its `book_id` and the current `VAR_DIR` (call it dir A).
2. Start the head: `uv run uvicorn main:app --port 8000` (serves with `VAR_DIR=A`, `AUTH_TOKEN=123`).
3. In a second shell, run a throwaway script against a *separate empty* var dir:

```bash
VAR_DIR=/tmp/r13smoke FLEET_HEAD_URL=http://127.0.0.1:8000 AUTH_TOKEN=123 \
  uv run python -c "
from uuid import UUID
from app.services import book_fetch, storage
bid = UUID('<book_id-from-step-1>')
p = book_fetch.ensure_book_pdf_sync(bid)
print('fetched to', p, p.stat().st_size, 'bytes')
assert p == storage.book_pdf_path(bid) and p.stat().st_size > 0
"
```

Expected: prints `fetched to /tmp/r13smoke/books/<id>/source.pdf <N> bytes` with N>0; the file now exists under `/tmp/r13smoke`. Re-running is a no-op fast path (still prints, no second download). Clean up: `rm -rf /tmp/r13smoke`.

Record the observed byte count in the worklog. (The real cross-machine 2-PC run remains the operator-run `fleet-test-1` proof.)

- [ ] **Step 3: Worklog + INDEX**

Add a `## [0061]` entry to `docs/memory/MASTER_MEMORY.md` and a matching row to `docs/memory/INDEX.md` summarizing: R13 bytes-distribution half shipped via pull-on-demand; the endpoint + helper + pipeline swap; the four Windows/robustness gaps handled; suite + smoke results. Then update `docs/memory/ROADMAP.md` R13 — mark the bytes-distribution half done (R13 fully closed), noting `fleet-test-1` real 2-PC run is the remaining operator proof.

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/ROADMAP.md
git commit -m "docs(memory): worklog 0061 — R13 pull-on-demand PDF shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Finish the branch**

Use `superpowers:finishing-a-development-branch` — push `feat/r13-pull-on-demand-pdf` and open a PR into `Nggaev-v2` (project convention: one PR per feature).

---

## Self-review

**Spec coverage:**
- Endpoint (spec §Design.1) → Task 2 ✓
- Fetch helper, exists/no-url/fetch/atomic/cleanup (spec §Design.2) → Task 3 ✓
- Pipeline swap via `to_thread` (spec §Design.3 + verified-fact #1) → Task 4 ✓
- `fleet_head_url` config (spec §Design.4) → Task 1 ✓
- Verified-fact: sync-httpx + `to_thread` → Task 3 helper is sync, Task 4 wraps in `to_thread` ✓
- Verified-fact: `auth_token` comma-string, empty→no header → Task 3 code + `test_outbound_auth_header_from_first_token` ✓
- Verified-fact: no raw-bytes endpoint → Task 2 adds one ✓
- Four review gaps: loop-block (Task 4 `to_thread`), mkdir parents (Task 3 + `test_missing_with_head_fetches_and_creates_parent`), auth first-token/empty (Task 3 + auth test), temp cleanup incl. Windows close-before-unlink (Task 3 `_fetch_to_temp` `with open` + except unlink + `test_fetch_failure_cleans_temp`) ✓
- Test plan (spec's 8 cases): 5 helper + 3 endpoint = 8 ✓
- Acceptance: localhost two-process smoke (spec) → Task 5 Step 2 ✓

**Placeholder scan:** none — every step has exact paths, full code, exact commands + expected output.

**Type/name consistency:** `ensure_book_pdf_sync` and `_fetch_to_temp` used identically in `book_fetch.py`, its tests, the pipeline swap, and the smoke script. `fleet_head_url` consistent across config, helper, tests. Endpoint path `/{book_id}/source.pdf` consistent between Task 2 route, endpoint tests, helper URL build, and `test_outbound_auth_header_from_first_token`.
