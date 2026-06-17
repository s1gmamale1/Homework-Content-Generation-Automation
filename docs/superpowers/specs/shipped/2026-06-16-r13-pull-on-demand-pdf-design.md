# R13 — Pull-on-demand source PDFs for multi-PC workers

- **Date:** 2026-06-16
- **Branch:** Nggaev-v2 (execution on `feat/r13-pull-on-demand-pdf`)
- **Backlog:** ROADMAP **R13** — the bytes-distribution half (the `var_dir`
  dead-setting half shipped 2026-06-12, worklog 0056). Unblocks `fleet-test-1`
  (real multi-PC generation).
- **Status:** design approved (pending written-spec review)

## Problem

The fleet's premise is ~10 PCs sharing one head Postgres. A worker generates by
reading the textbook PDF from **its own local disk** (`storage.book_pdf_path` →
`<var_dir>/books/<id>/source.pdf`). Nothing delivers the PDF *bytes* to the
machine that claimed the job. A remote worker that claims a lesson for a book it
doesn't physically have fails immediately at the `extract` phase with
`Book PDF missing on disk` (observed live 2026-06-09: every History lesson on
the fleet worktree failed until `var/books` was manually symlinked).

## Goal

Make any worker able to obtain any book's `source.pdf` on demand by fetching it
once from the head over HTTP, caching it to local disk, then proceeding — with
**zero behavior change** on a single box or the head's embedded worker.

## Locked decisions (from the brainstorm)

1. **Approach = pull-on-demand from the head** (over the existing FastAPI +
   bearer-token surface), chosen over a shared network volume (new runtime
   failure domain, brittle cross-OS mounts) and object storage (overkill at this
   scale; SDKs were deliberately removed). The head is already a hard dependency
   (shared DB lives there), so fetching bytes from it adds **no new failure
   domain**.
2. **Trigger = lazy, at the existing read-site (A1).** Replace the `raise` at
   `pipeline.py:104` with a call that ensures the PDF, fetching only when it's
   missing. Rejected A2 (fetch in the worker claim loop): it leaks a
   generation-input concern into the queue-mechanics layer and would fetch even
   on the head's embedded worker.
3. **Integrity = lightweight.** Verify HTTP 200 + non-empty body, write
   atomically (temp in the book dir → `os.replace`). No content hash, **no
   schema change**. A corrupt/truncated download fails later at extract (rare on
   a LAN; the job retries).
4. **Config = new `fleet_head_url` setting; empty = today's behavior.** When
   empty, the fetch is skipped and a missing PDF raises exactly as now — so
   single-box and the head's embedded worker are byte-for-byte unchanged. The
   feature is inert until a machine is a remote worker pointed at a head.

## Three verified facts the build relies on (don't re-derive)

- **Async call-site, sync-httpx house idiom.** The pipeline context-load is
  async. The codebase fetches HTTP with **synchronous** `httpx.Client` wrapped
  in `asyncio.to_thread` (`notion/client.py:4` documents this on purpose;
  `notion_fetch.py:123`, `notion/client.py:112,123`). The **same file already
  does this for PDF work**: `pipeline.py:673` —
  `await asyncio.to_thread(agent.read_whole_book_text, pdf_path)`. So the fetch
  helper is **sync**, called via `asyncio.to_thread`. An async helper doing sync
  httpx (or a careless `AsyncClient` + blocking disk write) would stall the loop
  that *also serves the API* on the head process.
- **`auth_token` is a comma-separated string**, not a list:
  `settings.auth_token` (default `"123"`) is split into a set by
  `valid_auth_tokens()` (`config.py:38,174–176`). The worker's outbound token is
  `settings.auth_token.split(",")[0].strip()`; if empty (auth disabled), send no
  `Authorization` header.
- **No raw-bytes endpoint exists yet.** `app/api/v1/books.py` has CRUD + TOC
  stream, all behind the shared bearer-token dependency; none serves
  `source.pdf`. A new endpoint is justified, and reuses the same auth.

## Design

### 1. Head endpoint — `GET /api/v1/books/{book_id}/source.pdf`

In `app/api/v1/books.py`, behind the existing auth dependency. Resolve
`storage.book_pdf_path(book_id)`; if the file exists →
`FileResponse(path, media_type="application/pdf")` (streams, off-loop);
else → `404` (book/file absent). No DB write; read-only.

### 2. Fetch helper — `app/services/book_fetch.py`

New module. **Synchronous** `ensure_book_pdf_sync(book_id) -> Path`:

1. `path = storage.book_pdf_path(book_id)`. If `path.exists()` → **return it**
   (fast path: head + already-cached remote take this, **no HTTP, no thread
   churn beyond the wrapper**).
2. Else if `settings.fleet_head_url` is empty → raise
   `RuntimeError(f"Book PDF missing on disk: {path}")` (today's exact message).
3. Else fetch:
   - `storage.book_dir(book_id).mkdir(parents=True, exist_ok=True)` (a
     first-time remote worker has no book dir).
   - `tmp = book_dir / f"source.pdf.{os.getpid()}.tmp"` (same filesystem as the
     final path → `os.replace` stays atomic; pid-suffix avoids collisions
     between two local workers).
   - `url = f"{fleet_head_url.rstrip('/')}/api/v1/books/{book_id}/source.pdf"`.
   - `token = settings.auth_token.split(",")[0].strip()`; headers =
     `{"Authorization": f"Bearer {token}"}` only if token is non-empty.
   - `with httpx.Client(timeout=120.0) as http:` open `http.stream("GET", url,
     headers=...)`; **check `resp.status_code == 200` first** (raise before
     touching disk, so an error body never lands in the temp file), then stream
     the body to `tmp`. Wrap the whole stream+write in a `try/except` that
     `tmp.unlink(missing_ok=True)` before re-raising as a clear `RuntimeError`
     (`f"fetch from head failed: {reason}"`).
   - After a clean write: verify `tmp` is non-empty (else unlink + raise), then
     `os.replace(tmp, path)`.
4. Return `path`.

### 3. Pipeline integration — `pipeline.py:103–105`

Replace:
```python
pdf_path = storage.book_pdf_path(book_id)
if not pdf_path.exists():
    raise RuntimeError(f"Book PDF missing on disk: {pdf_path}")
```
with:
```python
pdf_path = await asyncio.to_thread(book_fetch.ensure_book_pdf_sync, book_id)
```
The helper owns exists / fetch / raise. (`asyncio` is already imported in
`pipeline.py`.)

### 4. Config — `app/config.py`

Add `fleet_head_url: str = ""` (env `FLEET_HEAD_URL`, e.g.
`http://<head-ip>:8000`).

## Data flow

Remote worker claims a job → pipeline context-load →
`ensure_book_pdf_sync` sees the file missing + a head configured → streams it
from the head → atomic rename → extract reads the now-local file. The **next**
job for that book on the same PC hits the fast path (cached). Head / single-box:
file always present → fast path, identical to today.

## Error handling

- Head unreachable / non-200 / empty body → `RuntimeError` → job fails → the
  existing reclaim/retry machinery (attempts, `reclaim_stale_seconds`) handles
  it. Messages distinguish **"not configured"** (empty `fleet_head_url`) from
  **"fetch failed: <reason>"**.
- Mid-stream drop / non-200 → temp is unlinked before re-raise (no orphan
  `.tmp`); `os.replace` only runs after a clean, verified stream.
- Concurrency: pid-suffixed temp + atomic replace → two local workers fetching
  the same book is wasteful (double download) but safe.

## Operational precondition

Fleet workers must carry an `AUTH_TOKEN` the head accepts (normally the same
shared value) **and** `FLEET_HEAD_URL` pointing at the head's API. Without
`FLEET_HEAD_URL` the feature stays inert (raises as today).

## Testing / acceptance

Unit (`tests/services/test_book_fetch.py`), `httpx` mocked, no real DB:
1. file exists → returns immediately, **no HTTP call** made.
2. missing + empty `fleet_head_url` → raises (today's message).
3. missing + `fleet_head_url` set → fetches, writes atomically, returns path;
   final file present + non-empty, no leftover `.tmp`.
4. fetch non-200 / empty body → raises, **temp cleaned up**.
5. parent dir absent → created before write (the first-time-remote case).

Endpoint (`tests/api/...`, TestClient):
6. `GET /books/{id}/source.pdf` → 200 + bytes when present (with auth).
7. → 404 when the file is absent.
8. → 401 without a valid token.

Acceptance (this is infra/PDF-delivery, **not** generation content, so the
CLAUDE.md "real CLI smoke" gate doesn't apply): a **localhost two-process
smoke** — start the head on `:8000` with one book uploaded; run a second process
with a *separate empty* `VAR_DIR` and `FLEET_HEAD_URL=http://127.0.0.1:8000`,
drive `ensure_book_pdf_sync` (or a real extract) → confirm it fetches the PDF
into the second `var_dir` and extract proceeds. The real cross-machine 2-PC run
remains the operator-run `fleet-test-1` proof.

## Out of scope

- Shared-volume / object-store distribution (this *is* the chosen alternative).
- Content-hash integrity + any `books` schema change (lightweight by decision 3).
- Pre-warming / pushing PDFs to workers ahead of claims (lazy by decision 2).
- Cleanup/eviction of cached PDFs on workers (they persist, same as the head).
- Auth model changes (reuses the existing shared bearer token).
