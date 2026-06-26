# Cluster 6 — `notion-archive-1` (R15): make Notion push failures visible + recoverable

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Notion archive push fails (transient network/5xx), stop leaving the job invisibly un-archived (both `notion_archived_at` and `notion_skip_reason` NULL). Retry the push, record a skip reason on final failure, and give the operator a "Retry archive" button to recover later.

**Architecture:** Three layers. (1) `notion_archive.archive_job` gains a bounded-retry around the idempotent push and records `notion_skip_reason="push error: <Type>"` when retries exhaust (plus a best-effort skip marker on the outer catch-all). (2) A new `POST /jobs/{id}/retry-archive` endpoint re-invokes `archive_job` for a `done`, not-yet-archived job. (3) A FE "Retry archive" button next to the existing "Not archived to Notion" notice.

**Tech Stack:** FastAPI, SQLAlchemy async, React/TS (Vite), pytest, loguru.

---

## Approach & key decisions

- **Scope locked with the user (2026-06-26):** C6 → **R15 only, full stack**. The rest of C6 is deferred or blocked, and the cluster's framing premise is **stale**: the subject-page map now has **38 `(subject|grade)` entries** (not "7 of ~18"), and unmapped subjects are **already visible** — the explicit skip path writes `notion_skip_reason` (`notion_archive.py:191-199`), it's in `JobOut` (`schemas/job.py:45`), and the FE renders it (`job.tsx:400-404`). So R16 aliases (latent, no O'zb-history book) and the crawl rewrite (greenfield, needs a brainstorm) are **not** shipped here.
- **The one real gap:** the `except Exception` catch-all at `notion_archive.py:230` only `log.warning`s — a transient Notion error during the push leaves **both** columns NULL → looks like "never attempted." Fix = bounded retry of the (already-idempotent) push, then record a skip reason; harden the outer catch-all to do the same best-effort.
- **Why FE too (reversed my first instinct):** a bounded in-push retry only catches transient blips; if retries exhaust there is **no operator recovery** short of re-running the whole generation. An endpoint with no button is a dead half-feature. So the coherent deliverable is push-retry + endpoint + button in one run.
- **Idempotency makes retry safe:** `_push_to_notion` skips already-populated pages (`page_has_content`, `notion_archive.py:146-147`), and `archive_job` short-circuits when `notion_archived_at` is set (`:178`) — so retrying (auto or via the endpoint) never double-writes.
- **Identity-map staleness (load-bearing):** `archive_job` commits in its **own** `SessionLocal`. The endpoint loads `job` via the request `session` for the guard, so after `archive_job` the request session's cached row is stale. The endpoint must `session.expire_all()` before `_job_out` (which reads via `get_with_phases`) or it returns the pre-archive `notion_skip_reason`.
- **No generation-acceptance smoke needed:** Notion archival is post-generation, best-effort, and never touches the LLM pipeline — so CLAUDE.md's "real CLI smoke" gate doesn't apply. Proof = the pytest suite (Notion client mocked) + `tsc`. A live-Notion run is an optional operator check (would write to the real workspace), not a required gate.
- **Test patterns reused verbatim:** service tests mirror `tests/services/test_notion_archive_skip.py` (`_FakeSession`, `patch.object(na, ...)`); endpoint tests mirror `tests/api/test_retry_cancelled.py` (TestClient + `dependency_overrides[get_current_user]` + `patch("app.api.v1.jobs....")`).

**Housekeeping (Task 0):** one uncommitted doc edit is already in the tree (`docs/memory/REMEDIATION_CLUSTERS.md` — the 2026-06-26 C5/C6/C7/C9 re-verification). Commit it first, standalone, so the code tasks start from a clean tree.

---

### Task 0: Commit the pending cluster re-verification doc

**Files:**
- Modify: `docs/memory/REMEDIATION_CLUSTERS.md` (already edited, uncommitted)

- [ ] **Step 1: Review the diff**

Run: `git diff docs/memory/REMEDIATION_CLUSTERS.md` — confirm it's only the 2026-06-26 verification notes (stale-premise corrections for C5/C6/C7, ref fixes for C9).

- [ ] **Step 2: Commit (only that file)**

```bash
git add docs/memory/REMEDIATION_CLUSTERS.md
git commit -m "docs(clusters): record 2026-06-26 code re-verification (C5/C6/C7/C9)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1: `archive_job` — bounded retry + skip-reason on push failure

**Files:**
- Modify: `app/services/notion_archive.py` (add `_PUSH_MAX_ATTEMPTS`, `_PUSH_BACKOFF_BASE_SECONDS`, `_push_with_retry`, `_record_skip`; rewire the push + outer catch-all in `archive_job`)
- Test: `tests/services/test_notion_archive_skip.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_notion_archive_skip.py`:

```python
def test_archive_marks_skip_on_push_exception(monkeypatch):
    """A push that fails every attempt is retried _PUSH_MAX_ATTEMPTS times, then
    records notion_skip_reason='push error: <Type>' instead of vanishing."""
    jid = uuid4()
    job = SimpleNamespace(id=jid, notion_archived_at=None, subject="math-algebra",
                          book_id=uuid4(), toc_entry_id=uuid4())
    book = SimpleNamespace(grade="5", original_filename="x.pdf")
    section = SimpleNamespace(id=uuid4(), section_number="1", section_title="T")
    done_phase = SimpleNamespace(phase_name="case-based-preview", output_md="# x", status="done")
    push = MagicMock(side_effect=RuntimeError("boom"))
    set_skip = AsyncMock()
    sleeps = AsyncMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na.settings, "notion_subject_pages", {"math-algebra|5": "subj"}), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_to_notion", push), \
         patch.object(na.asyncio, "sleep", sleeps), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[done_phase])), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(jid))
    assert push.call_count == na._PUSH_MAX_ATTEMPTS          # retried, not one-shot
    set_skip.assert_awaited()
    assert "push error" in set_skip.await_args.args[2]
    assert "RuntimeError" in set_skip.await_args.args[2]
```

(`MagicMock` is already imported at the top of this file? It imports `AsyncMock, patch`. Add `MagicMock` to that import line: `from unittest.mock import AsyncMock, MagicMock, patch`.)

- [ ] **Step 2: Run it — verify RED**

Run: `uv run python -m pytest tests/services/test_notion_archive_skip.py::test_archive_marks_skip_on_push_exception -v`
Expected: FAIL — `push.call_count == 1` (no retry) and `set_skip` not awaited (current catch-all only logs).

- [ ] **Step 3: Implement**

In `app/services/notion_archive.py`, add module constants after `_warned_unconfigured` (near line 30):

```python
# Bounded retry for the (idempotent) Notion push. A transient network/5xx must
# not leave the job invisibly un-archived (notion_archived_at + skip_reason both
# NULL); retry, then record a skip reason on final failure.
_PUSH_MAX_ATTEMPTS = 3
_PUSH_BACKOFF_BASE_SECONDS = 1.0
```

Add two helpers (e.g. just above `async def archive_job`):

```python
async def _push_with_retry(*, client, subject_page_id, lesson_title, phase_md) -> str:
    """Run the idempotent Notion push in a worker thread, retrying transient
    failures with exponential backoff. Re-raises the last exception if every
    attempt fails, so the caller can record a skip reason."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                _push_to_notion,
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                phase_md=phase_md,
            )
        except Exception as exc:  # noqa: BLE001 - retried, then recorded as a skip
            last_exc = exc
            log.warning("notion: push attempt %d/%d failed: %s",
                        attempt, _PUSH_MAX_ATTEMPTS, exc)
            if attempt < _PUSH_MAX_ATTEMPTS:
                await asyncio.sleep(_PUSH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


async def _record_skip(job_id: UUID, reason: str) -> None:
    """Best-effort persist of a skip reason in a fresh session; never raises."""
    try:
        async with SessionLocal() as session:
            await jobs_repo.set_notion_skip_reason(session, job_id, reason)
            await session.commit()
    except Exception:  # noqa: BLE001 - the skip marker is itself best-effort
        log.warning("notion: could not record skip reason for job %s", job_id, exc_info=True)
```

Then rewire the push block + outer catch-all in `archive_job` (replace lines ~216-231):

```python
        client = NotionClientWrapper(api_key=settings.notion_api_key)
        try:
            homework_id = await _push_with_retry(
                client=client,
                subject_page_id=subject_page_id,
                lesson_title=lesson_title,
                phase_md=phase_md,
            )
        except Exception as exc:  # noqa: BLE001 - push exhausted retries; record + give up
            log.warning("notion: push failed for job %s after %d attempts (non-fatal)",
                        job_id, _PUSH_MAX_ATTEMPTS, exc_info=True)
            await _record_skip(job_id, f"push error: {type(exc).__name__}")
            return

        async with SessionLocal() as session:
            await toc_repo.set_notion_homework_page_id(session, section_id, homework_id)
            await jobs_repo.set_notion_archived(session, job_id, _utcnow())
            await session.commit()
        log.info("notion: archived job %s → Homework page %s", job_id, homework_id)
    except Exception:
        log.warning("notion: archive failed for job %s (non-fatal)", job_id, exc_info=True)
        await _record_skip(job_id, "archive error")
```

- [ ] **Step 4: Run the file — verify GREEN + no regressions**

Run: `uv run python -m pytest tests/services/test_notion_archive_skip.py tests/services/test_notion_archive.py -v`
Expected: all PASS (the new test + the existing skip/structure tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive_skip.py
git commit -m "fix(notion): retry push + record skip_reason on archive failure (notion-archive-1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `POST /jobs/{id}/retry-archive` endpoint

**Files:**
- Modify: `app/api/v1/jobs.py` (import `notion_archive`; add the endpoint after `retry_job`, ~line 314)
- Test: `tests/api/test_retry_archive_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_retry_archive_endpoint.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from app.api.v1.jobs import JobOut
from app.auth import get_current_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_retry_archive_happy_path():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done", notion_archived_at=None)
    out = JobOut(id=jid, book_id=uuid4(), toc_entry_id=uuid4(),
                 subject="kimyo-g7-11", status="done")
    arch = AsyncMock()
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs.notion_archive.archive_job", arch), \
         patch("app.api.v1.jobs._job_out", AsyncMock(return_value=out)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 200
    arch.assert_awaited_once()


def test_retry_archive_rejects_non_done():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="running", notion_archived_at=None)
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 409


def test_retry_archive_rejects_already_archived():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done",
                          notion_archived_at=datetime.now(timezone.utc))
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 409


def test_retry_archive_404_when_missing():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=None)):
        r = client.post(f"/api/v1/jobs/{jid}/retry-archive")
    assert r.status_code == 404
```

- [ ] **Step 2: Run it — verify RED**

Run: `uv run python -m pytest tests/api/test_retry_archive_endpoint.py -v`
Expected: FAIL — the route doesn't exist (404/405) and `app.api.v1.jobs.notion_archive` isn't importable yet.

- [ ] **Step 3: Implement**

In `app/api/v1/jobs.py`, add to the `from app.services import ...` group (line 22 area):

```python
from app.services import events_bus, notion_archive, pricing
```

(extend the existing `from app.services import events_bus, pricing` line — don't add a second import line).

Add the endpoint after `retry_job` (after line 314):

```python
@router.post("/jobs/{job_id}/retry-archive")
async def retry_archive_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Re-attempt the best-effort Notion archive for a job whose push previously
    failed (status=done, notion_archived_at IS NULL). `archive_job` is idempotent
    (skips already-populated pages) and clears `notion_skip_reason` on success.
    Refuses non-done or already-archived jobs with 409."""
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(
            409, f"only done jobs can be re-archived; current status={job.status!r}")
    if job.notion_archived_at is not None:
        raise HTTPException(409, "job already archived to Notion")
    await notion_archive.archive_job(job_id)
    # archive_job commits in its OWN session; drop this session's stale copy so
    # _job_out re-reads the updated notion_skip_reason/notion_archived_at.
    session.expire_all()
    return await _job_out(session, job_id)
```

- [ ] **Step 4: Run it — verify GREEN**

Run: `uv run python -m pytest tests/api/test_retry_archive_endpoint.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_retry_archive_endpoint.py
git commit -m "feat(api): POST /jobs/{id}/retry-archive to re-attempt Notion archive (notion-archive-1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: FE "Retry archive" button

**Files:**
- Modify: `web/src/lib/api.ts` (add `retryArchiveJob`, after `retryJob` ~line 240)
- Modify: `web/src/routes/job.tsx` (state + handler + button in the notion-skip notice ~line 400)

- [ ] **Step 1: Add the API client method**

In `web/src/lib/api.ts`, immediately after the `retryJob` method (~line 240):

```typescript
  async retryArchiveJob(jobId: string): Promise<Job> {
    const res = await authFetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/retry-archive`,
      { method: "POST" },
    );
    return unwrap<Job>(res);
  },
```

- [ ] **Step 2: Add state + handler in `job.tsx`**

Add the loading state next to the existing `retrying` state (~line 61):

```typescript
  const [archiving, setArchiving] = useState(false);
```

Add the handler immediately after `handleRetry` (~line 97):

```typescript
  async function handleRetryArchive() {
    if (!id) return;
    setArchiving(true);
    try {
      const updated = await api.retryArchiveJob(id);
      queryClient.setQueryData(["job", id], updated);
      setNotionSkip(updated.notion_skip_reason ?? null);
      if (updated.notion_skip_reason) {
        toast.error(`Archive failed again: ${updated.notion_skip_reason}`);
      } else {
        toast.success("Archived to Notion");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Archive retry failed");
    } finally {
      setArchiving(false);
    }
  }
```

- [ ] **Step 3: Add the button to the notion-skip notice**

Replace the block at `job.tsx:400-404`:

```tsx
        {status === "done" && notionSkip && (
          <div className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm text-white/55">
            Not archived to Notion: {notionSkip}
            <button
              type="button"
              onClick={handleRetryArchive}
              disabled={archiving}
              className="ml-2 inline-flex items-center gap-1.5 rounded-lg border border-white/[0.15] px-2 py-1 text-xs text-white/75 hover:bg-white/[0.08] disabled:opacity-50"
            >
              {archiving ? (
                <>
                  <Loader2 className="size-3 animate-spin" />
                  Archiving…
                </>
              ) : (
                <>
                  <RefreshCcw className="size-3" />
                  Retry archive
                </>
              )}
            </button>
          </div>
        )}
```

(`Loader2` and `RefreshCcw` are already imported — they back the existing "Retry this job" button.)

- [ ] **Step 4: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

- [ ] **Step 5: Build (smoke the bundle)**

Run: `cd web && npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/api.ts web/src/routes/job.tsx
git commit -m "feat(web): Retry archive button for jobs that failed Notion push (notion-archive-1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Finish (after all tasks green)

1. **Full suite:** `uv run python -m pytest tests/ -q` — green except the 2 pre-existing `test_judge_resolution.py` gemini-3.1 self-grade failures (unrelated; verified on base).
2. **Rebase-check:** `git fetch origin` then `git log HEAD..origin/Nggaev-v2` — if base moved, rebase onto `origin/Nggaev-v2`, resolve, re-run the suite.
3. **Hand to the reviewer for the merge gate** — I'm the implementer; do **not** self-merge/push without the user's GO.
4. **On GO, same finish (do not defer):**
   - (a) Worklog entry in `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`.
   - (b) Close `notion-archive-1` / R15 in `docs/memory/ROADMAP.md` (and mark it shipped in `WISHLIST.md` if listed); leave R16/crawl/validator open with the stale-premise note.
   - (c) `git mv docs/superpowers/plans/2026-06-26-c6-notion-archive-retry.md docs/superpowers/plans/shipped/`.
   - (d) De-stale reference docs that mention Notion archival behavior: `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md` (note the new endpoint + retry); `README.md` only if it documents the archive flow. No schema/deploy change → `DATABASE.md`/`DEPLOY.md` untouched.

## Out of scope (explicitly not shipped here)
- R16 keyword aliases (`notion-archive-2`) — latent, no O'zb-history book; `history|*` already object-form.
- Crawl-based subject-page auto-resolve (item 3) — greenfield, needs a brainstorm; not urgent (38 entries mapped, unmapped already visible).
- Notion archive validator (item 4) — parked.
- `fe-redesign` (item 5) — brainstorm-blocked.
- `fleet-ui-2/3/4` (item 6) — blocked on `sse-multipod-1` (C5).
