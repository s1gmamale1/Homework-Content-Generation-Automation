# Notion Batch Re-archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator re-push an entire batch's already-generated homework to Notion from the website (head PC), for batches whose worker missed `NOTION_SUBJECT_PAGES` (or otherwise failed to archive).

**Architecture:** A new backgrounded `POST /jobs/batch/{batch_id}/retry-archive` endpoint enumerates the batch's `done` jobs with `notion_archived_at IS NULL` and re-runs the existing idempotent `notion_archive.archive_job(job_id)` for each — in the **API (head) process**, which already carries the full Notion config. The batch rollup gains `archived`/`unarchived` counts so the Monitor can show "X/Y archived" and surface a "Re-archive to Notion" button only when there's a gap.

**Tech Stack:** FastAPI + SQLAlchemy async + Postgres; React/TS Monitor; `notion_archive.archive_job` (already proven via the per-job `/jobs/{id}/retry-archive`).

---

## Approach & key decisions

**Chosen approach.** Re-use the per-job archival mechanism at batch granularity. The head-PC API process is fully capable of archiving on its own — `archive_job(job_id)` reads only the DB (`homework_jobs` + `books` + `toc_entries` + `phase_outputs`) plus head-side `settings.notion_*`, opens its **own** `SessionLocal`, is idempotent (`find_or_create` dedupes Notion pages), and **never raises** (records `notion_skip_reason` on failure). The existing `POST /jobs/{id}/retry-archive` (`app/api/v1/jobs.py:378`) already calls it inline in the API process. We add a batch-level fan-out plus Monitor visibility.

**Load-bearing facts (verified against code):**
- `notion_archive.archive_job(job_id: UUID)` (`app/services/notion_archive.py:215`) — fresh session, idempotent, best-effort (catches all, sets `notion_skip_reason`), updates `notion_archived_at` on success.
- `_rollup_payload(batch, tally, original_filename=None)` (`app/api/v1/batch.py:60`) is the SINGLE serializer feeding all 3 response paths (list `:330`, single `:342`, launch `:320`). Its unit tests pass `SimpleNamespace` fakes + a plain `tally` dict — so new fields must be **defaulted keyword args** to avoid breaking those fakes.
- `batches_repo.rollup_for_batch` (`app/repositories/batches.py:84`) uses a DISTINCT-ON-`toc_entry_id` latest-job-per-lesson subquery, then GROUP BY status. We mirror that exact pattern for the archive split so retries/top-ups don't double-count.
- `homework_jobs` has `notion_archived_at` (set/cleared by `jobs_repo.set_notion_archived` / `set_notion_skip_reason`).
- Background pattern in the codebase: `asyncio.create_task(...)` fire-and-forget (`app/api/v1/books.py:53` for `toc_extractor.run`). The sweep must be backgrounded because ~30 lessons × ~12 Notion pages each at the client's ~3 req/s rate limit = several minutes — a synchronous request would time out.

**Rejected alternatives.**
- *Synchronous endpoint* — would block/time out for a full book. Rejected.
- *Re-enqueue archive-only jobs on workers* — workers are the ones missing the config; the head has it. Re-using the worker queue would re-hit the same gap and needs a new job kind. Rejected.
- *Per-job-button-in-drawer only* (the cheap option) — doesn't match the failure mode (a missing-config worker fails the **whole book**); 30 clicks. Rejected as the primary (the per-job button already exists on the job page for one-offs).
- *Auto-sweep on the head* — deferred; the operator wants an explicit manual trigger. Can be added later on top of this.

**Key decisions.**
1. **Backgrounded** via `asyncio.create_task`; endpoint returns immediately with `queued` (the count handed to the sweep). Progress is observed through the rollup's `archived`/`unarchived` counts (Monitor already polls batches).
2. **Sequential** sweep (one `archive_job` at a time) — the Notion client is globally rate-limited; parallel fan-out buys nothing and risks 429s.
3. **In-process guard** (`_REARCHIVING: set[UUID]`) so a double-click can't launch a second concurrent sweep of the same batch; second call returns `already_running: true, queued: 0`. (Archival is idempotent, so this is a politeness/efficiency guard, not a correctness one.)
4. **`_rollup_payload` gains `*, archived: int = 0, unarchived: int = 0`** keyword args (defaulted → existing unit tests + `SimpleNamespace` mocks untouched). "unarchived" counts only `done` jobs not yet archived (the actionable gap), NOT pending/running.

**Caveat to surface (not code):** re-archive only succeeds if the head's own `NOTION_SUBJECT_PAGES` actually maps that `subject|grade|language`; otherwise it re-records `"no Notion page for…"`. The feature is the lever; head-config completeness stays an operator concern.

---

## File Structure

- `app/repositories/batches.py` — add `archive_rollup_for_batch` (counts) + `done_unarchived_job_ids` (the sweep's worklist); extend `list_with_rollups` to include per-batch archive counts.
- `app/api/v1/batch.py` — extend `_rollup_payload` (kwargs); compute archive counts at the 3 call sites; add the `retry-archive` endpoint + module-level sweep helper + guard set.
- `tests/api/test_batch_rearchive.py` — new: repo + endpoint real-DB tests.
- `tests/api/test_rollup_archive_counts.py` — new: pure `_rollup_payload` keys test.
- `web/src/lib/types.ts` — `BatchSummary` gains `archived`/`unarchived`; new `BatchRearchiveResponse`.
- `web/src/lib/api.ts` — `retryArchiveBatch`.
- `web/src/components/fleet/batch-actions.tsx` — "Re-archive to Notion" button (per-batch, useMutation).
- `web/src/components/fleet/batch-funnel.tsx` — "Notion X/Y" archive-progress chip in `TransportRow`.

---

### Task 1: Repo — archive rollup + unarchived worklist

**Files:**
- Modify: `app/repositories/batches.py` (add two functions after `rollup_for_batch`, ~line 119; extend `list_with_rollups` ~line 122)
- Test: `tests/api/test_batch_rearchive.py`

- [ ] **Step 1: Write the failing test** (real-DB; needs `RUN_DB_INTEGRATION=1`)

```python
# tests/api/test_batch_rearchive.py
import os
from datetime import datetime, timezone
import pytest
from uuid import uuid4

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

from app.db import SessionLocal
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo


async def _seed_batch_with_two_done_jobs(s):
    """Book + 2 TOC entries + a batch + one done+archived job and one done+unarchived job.
    NOTE (verified against repo signatures): books_repo.create requires
    content_sha256 + file_size_bytes; TOC has only bulk_create (construct
    TOCEntry rows directly here); jobs_repo.create requires output_language."""
    from app.models.toc_entry import TOCEntry

    book = await books_repo.create(
        s, subject="geometriya-g7-11", grade="8",
        original_filename="g8.pdf", content_sha256="0" * 64,
        file_size_bytes=1, source_language="uz",
    )
    e1 = TOCEntry(book_id=book.id, section_number="1", section_title="L1", order_index=0)
    e2 = TOCEntry(book_id=book.id, section_number="2", section_title="L2", order_index=1)
    s.add_all([e1, e2])
    await s.flush()
    batch = await batches_repo.get_or_create_for_book(
        s, book_id=book.id, subject="geometriya-g7-11", grade="8",
        provider="gemini", model="gemini-2.5-pro", transport="api",
        output_language="uz",
    )
    j1 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=e1.id,
                                subject="geometriya-g7-11", output_language="uz",
                                provider="gemini", model="gemini-2.5-pro",
                                transport="api", batch_id=batch.id)
    j2 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=e2.id,
                                subject="geometriya-g7-11", output_language="uz",
                                provider="gemini", model="gemini-2.5-pro",
                                transport="api", batch_id=batch.id)
    await jobs_repo.set_status(s, j1.id, "done", completed_at=datetime.now(timezone.utc))
    await jobs_repo.set_status(s, j2.id, "done", completed_at=datetime.now(timezone.utc))
    await jobs_repo.set_notion_archived(s, j1.id, datetime.now(timezone.utc))  # j1 archived, j2 not
    await s.commit()
    return batch, j1, j2


@pytest.mark.asyncio
async def test_archive_rollup_splits_done_by_archived_state():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        counts = await batches_repo.archive_rollup_for_batch(s, batch.id)
        assert counts == {"archived": 1, "unarchived": 1}


@pytest.mark.asyncio
async def test_done_unarchived_job_ids_returns_only_unarchived():
    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)
        ids = await batches_repo.done_unarchived_job_ids(s, batch.id)
        assert ids == [j2.id]
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_scratch uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: FAIL — `AttributeError: module 'app.repositories.batches' has no attribute 'archive_rollup_for_batch'`. (If the repo helper signatures differ — `books_repo.create` / `toc_repo.create` / `jobs_repo.create` kwargs — fix the seed to match the real signatures before continuing; check `app/repositories/books.py`, `toc_entries.py`, `jobs.py`.)

- [ ] **Step 3: Implement** (after `rollup_for_batch`, before `list_with_rollups`)

```python
async def archive_rollup_for_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Among the batch's `done` lessons (latest job per toc_entry), split by
    Notion archive state: {"archived": n, "unarchived": m}. Mirrors
    rollup_for_batch's DISTINCT-ON latest-per-lesson so retries don't double-count."""
    latest = (
        select(
            HomeworkJob.status.label("status"),
            HomeworkJob.notion_archived_at.label("notion_archived_at"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.notion_archived_at)
            .where(latest.c.status == "done")
        )
    ).all()
    archived = sum(1 for (ts,) in rows if ts is not None)
    unarchived = sum(1 for (ts,) in rows if ts is None)
    return {"archived": archived, "unarchived": unarchived}


async def done_unarchived_job_ids(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Latest job per toc_entry in the batch that is `done` AND not yet archived.
    The worklist the head-side re-archive sweep iterates. Ordered for stable runs."""
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.notion_archived_at.label("notion_archived_at"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.job_id)
            .where(latest.c.status == "done")
            .where(latest.c.notion_archived_at.is_(None))
            .order_by(latest.c.toc_entry_id)
        )
    ).all()
    return [r.job_id for r in rows]
```

Then extend `list_with_rollups` to attach archive counts (add to the per-row dict):

```python
    for b, original_filename in rows:
        tally = await rollup_for_batch(session, b.id)
        archive = await archive_rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally, "archive": archive,
                    "original_filename": original_filename})
```

- [ ] **Step 4: Run to verify it passes**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_scratch uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/batches.py tests/api/test_batch_rearchive.py
git commit -m "feat(notion): batch archive rollup + done-unarchived worklist"
```

---

### Task 2: Serializer — expose archived/unarchived in the rollup payload

**Files:**
- Modify: `app/api/v1/batch.py:60` (`_rollup_payload`), `:320` (launch), `:342` (single), `:330` (list)
- Test: `tests/api/test_rollup_archive_counts.py`

- [ ] **Step 1: Write the failing test** (pure, no DB)

```python
# tests/api/test_rollup_archive_counts.py
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from app.api.v1.batch import _rollup_payload


def _fake_batch():
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), subject="math", grade="8",
        output_language="uz", provider="gemini", model="gemini-2.5-pro",
        transport="api", extract_transport="inherit", judge_transport="inherit",
        extract_provider=None, extract_model=None, judge_provider=None, judge_model=None,
        created_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        paused_at=None, paused_reason=None, session_limit_strategy="inherit",
    )


def test_rollup_payload_defaults_archive_counts_to_zero():
    p = _rollup_payload(_fake_batch(), {"done": 3})
    assert p["archived"] == 0
    assert p["unarchived"] == 0


def test_rollup_payload_carries_archive_counts():
    p = _rollup_payload(_fake_batch(), {"done": 3}, archived=2, unarchived=1)
    assert p["archived"] == 2
    assert p["unarchived"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/api/test_rollup_archive_counts.py -q`
Expected: FAIL — `KeyError: 'archived'` (and `TypeError` on the kwargs in the 2nd test).

- [ ] **Step 3: Implement**

Change the signature and add the two keys:

```python
def _rollup_payload(batch, tally: dict[str, int], original_filename: str | None = None,
                    *, archived: int = 0, unarchived: int = 0) -> dict:
    return {
        # ... existing keys unchanged ...
        "session_limit_strategy": batch.session_limit_strategy,
        "archived": archived,
        "unarchived": unarchived,
    }
```

Wire the 3 call sites:
- List (`:330`): `_rollup_payload(r["batch"], r["rollup"], r.get("original_filename"), archived=r["archive"]["archived"], unarchived=r["archive"]["unarchived"]) for r in rows`
- Single (`get_batch`, ~`:340`): add `archive = await batches_repo.archive_rollup_for_batch(session, batch_id)` then `_rollup_payload(batch, tally, ..., archived=archive["archived"], unarchived=archive["unarchived"])`
- Launch (`:317`): add `archive = await batches_repo.archive_rollup_for_batch(session, batch.id)` then pass the same two kwargs into the launch `_rollup_payload(...)` call.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/api/test_rollup_archive_counts.py tests/api/test_rollup_pause_and_not_started.py tests/api/test_batch_payload_variant.py tests/api/test_never_pay_twice.py -q`
Expected: all passed (the defaulted kwargs keep the existing `SimpleNamespace`-fake tests green).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/batch.py tests/api/test_rollup_archive_counts.py
git commit -m "feat(notion): surface archived/unarchived counts in batch rollup"
```

---

### Task 3: Endpoint — backgrounded batch re-archive

**Files:**
- Modify: `app/api/v1/batch.py` (module-level guard + sweep helper near top after `router`; endpoint after `unpause_batch` ~`:421`)
- Test: `tests/api/test_batch_rearchive.py` (append)

- [ ] **Step 1: Write the failing test** (real-DB; monkeypatches `archive_job` so no real Notion call)

```python
# append to tests/api/test_batch_rearchive.py
@pytest.mark.asyncio
async def test_retry_archive_endpoint_sweeps_unarchived(monkeypatch):
    import asyncio
    from httpx import AsyncClient, ASGITransport
    from main import app
    from app.api.v1 import batch as batch_api
    from app.services import notion_archive

    called: list = []

    async def _fake_archive(job_id):
        called.append(job_id)

    monkeypatch.setattr(notion_archive, "archive_job", _fake_archive)

    async with SessionLocal() as s:
        batch, j1, j2 = await _seed_batch_with_two_done_jobs(s)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{batch.id}/retry-archive")
    assert r.status_code == 200
    assert r.json()["queued"] == 1

    task = batch_api._REARCHIVE_TASKS.get(batch.id)
    if task is not None:
        await task
    assert called == [j2.id]   # only the unarchived done job


@pytest.mark.asyncio
async def test_retry_archive_unknown_batch_404():
    from httpx import AsyncClient, ASGITransport
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(f"/api/v1/jobs/batch/{uuid4()}/retry-archive")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_scratch uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: FAIL — 404 from the not-yet-existing route (the success test gets 404 too).

- [ ] **Step 3: Implement**

Near the top of `app/api/v1/batch.py` (after `router = APIRouter(...)`):

```python
import asyncio
import logging

log = logging.getLogger(__name__)

# Tracks in-flight head-side re-archive sweeps so a double-click can't launch a
# second concurrent sweep of the same batch (archive_job is idempotent, so this
# is an efficiency/politeness guard). Keyed by batch_id.
_REARCHIVE_TASKS: dict[UUID, "asyncio.Task"] = {}


async def _rearchive_sweep(batch_id: UUID, job_ids: list[UUID]) -> None:
    """Sequentially re-run the idempotent, best-effort archive_job for each
    done-but-unarchived job in a batch, in the API process. Runs in the
    background; never raises (archive_job swallows + records skip reasons)."""
    from app.services import notion_archive
    try:
        for jid in job_ids:
            try:
                await notion_archive.archive_job(jid)
            except Exception:  # defensive; archive_job is already best-effort
                log.warning("[batch %s] re-archive of job %s failed (non-fatal)",
                            batch_id, jid, exc_info=True)
    finally:
        _REARCHIVE_TASKS.pop(batch_id, None)
```

Endpoint (after `unpause_batch`):

```python
@router.post("/jobs/batch/{batch_id}/retry-archive")
async def retry_archive_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    """Re-push every done-but-unarchived lesson of a batch to Notion from the
    HEAD process (which carries NOTION_SUBJECT_PAGES). Backgrounded + idempotent:
    returns immediately with how many jobs were queued. A second call while a
    sweep is in flight is a no-op (already_running)."""
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    if batch_id in _REARCHIVE_TASKS:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": True}
    job_ids = await batches_repo.done_unarchived_job_ids(session, batch_id)
    if not job_ids:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": False}
    _REARCHIVE_TASKS[batch_id] = asyncio.create_task(_rearchive_sweep(batch_id, job_ids))
    return {"batch_id": str(batch_id), "queued": len(job_ids), "already_running": False}
```

- [ ] **Step 4: Run to verify it passes**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_scratch uv run python -m pytest tests/api/test_batch_rearchive.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/batch.py tests/api/test_batch_rearchive.py
git commit -m "feat(notion): backgrounded batch retry-archive endpoint"
```

---

### Task 4: Frontend — types + API client

**Files:**
- Modify: `web/src/lib/types.ts` (`BatchSummary` + new response type)
- Modify: `web/src/lib/api.ts` (`retryArchiveBatch`)

- [ ] **Step 1: Add fields to `BatchSummary`** (after `session_limit_strategy?`, ~line 390)

```typescript
  /** Notion archive progress for the batch's done lessons (C6 batch re-archive). */
  archived: number;
  unarchived: number;
```

- [ ] **Step 2: Add the response type** (near `BatchPauseResponse`, ~line 421)

```typescript
/** Response from POST /jobs/batch/{id}/retry-archive */
export interface BatchRearchiveResponse {
  batch_id: string;
  queued: number;
  already_running: boolean;
}
```

- [ ] **Step 3: Add the API method** (in `web/src/lib/api.ts`, near `retryArchiveJob` ~line 268)

```typescript
  async retryArchiveBatch(batchId: string): Promise<BatchRearchiveResponse> {
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/retry-archive`,
      { method: "POST" },
    );
    return unwrap<BatchRearchiveResponse>(res);
  }
```

(Add `BatchRearchiveResponse` to the existing `types` import in `api.ts`.)

- [ ] **Step 4: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts
git commit -m "feat(monitor): batch re-archive types + api client"
```

---

### Task 5: Frontend — Monitor archived chip + "Re-archive to Notion" button

**Real structure (verified post-sigma `#71`):** per-batch actions live in `web/src/components/fleet/batch-actions.tsx` (`BatchActions({batch})`) — it already uses `useMutation` + `useQueryClient` + `toast` + `api`, the exact pattern the new button must follow. `TransportRow` in `batch-funnel.tsx` (line ~64, after `<RollupBar/>`) is always rendered, so the informational "Notion X/Y" chip goes **there** (BatchActions returns `null` when idle — line 50 — so a fully-archived complete batch would hide a chip placed inside it).

**Files:**
- Modify: `web/src/components/fleet/batch-actions.tsx` (re-archive button + mutation; relax the idle early-return)
- Modify: `web/src/components/fleet/batch-funnel.tsx` (TransportRow: "Notion X/Y" chip)

- [ ] **Step 1: Add the re-archive button to `BatchActions`.** Add the import `CloudUpload` to the existing `lucide-react` import; add a mutation alongside the others:

```tsx
  const rearchiveMut = useMutation({
    mutationFn: () => api.retryArchiveBatch(batch.batch_id),
    onSuccess: (res) => {
      toast.success(
        res.already_running
          ? "Re-archive already running"
          : `Re-archiving ${res.queued} lesson(s) to Notion`,
      );
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });
```

Relax the idle guard (line 50) so the button still shows when re-archive is the only available action:

```tsx
  const canRearchive = batch.unarchived > 0;
  if (!canPause && !isPaused && !canCancel && !canRetry && !canRearchive) return null;
```

Add the button inside the actions `<div>` (after the `canRetry` block), styled like its siblings:

```tsx
      {canRearchive && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs disabled:opacity-50")}
          disabled={rearchiveMut.isPending}
          title={`Re-push ${batch.unarchived} un-archived lesson(s) to Notion (head PC)`}
          onClick={() => rearchiveMut.mutate()}
        >
          {rearchiveMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <CloudUpload className="size-3.5" />
          )}
          Re-archive ({batch.unarchived})
        </button>
      )}
```

- [ ] **Step 2: Add the "Notion X/Y" chip to `TransportRow`.** In `batch-funnel.tsx`, right after `<RollupBar rollup={batch.rollup} covered={batch.lessons_covered} />` (line ~64):

```tsx
      {batch.archived + batch.unarchived > 0 && (
        <div className="text-[0.7rem] text-white/45">
          Notion archive · {batch.archived}/{batch.archived + batch.unarchived}
        </div>
      )}
```

- [ ] **Step 3: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: typecheck clean, build writes `web/dist/`.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/fleet/batch-actions.tsx web/src/components/fleet/batch-funnel.tsx
git commit -m "feat(monitor): Re-archive to Notion action + archive-progress chip"
```

---

## Acceptance gate

- Full backend suite green: `uv run python -m pytest tests/ -q` (real-DB tests via `RUN_DB_INTEGRATION=1` + scratch DB).
- FE structural gate: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`.
- **Live smoke (gate/operator, in-browser):** on a batch with done-but-unarchived lessons, the card shows "Notion X/Y" and the button; clicking it toasts "Re-archiving N lesson(s)"; after the sweep, re-polling shows the archived count rising (or the unchanged count + a persisted `notion_skip_reason` if the head's `NOTION_SUBJECT_PAGES` lacks that subject — which is the expected "config gap" signal, not a bug).

## Lane order & known assumptions

- **Lane order (Monitor FE is single-threaded):** this plan's Task 5 and the sigma-redesign's Task 4 both edit `batch-funnel.tsx` BookCard — the *only* overlap (sigma touches no `types.ts`/`api.ts`). **Sigma merges first; this plan's worktree is cut off (or rebased onto) sigma's merged result** and reconciles `batch-funnel.tsx` there, inheriting sigma's restyled buttons.
- **Worklog note — single-process guard:** `_REARCHIVE_TASKS` lives in one process's memory. Correct for the head (`uvicorn main:app`, `WORKER_CONCURRENCY=0`); under `--workers N` the dedup guard wouldn't span workers (harmless — `archive_job` is idempotent, just a redundant sweep). State this assumption.
- **Worklog note — restart mid-sweep:** a head restart drops an in-flight sweep task; progress is lost but safe — idempotent, and the button simply reappears while `unarchived > 0`. One honest line in the docs.

## Finish (after all tasks)

Rebase-check onto `origin/Nggaev-v2`; worklog entry in `docs/memory/MASTER_MEMORY.md` + `INDEX.md` row; close the matching item in `ROADMAP.md` if present; `git mv` this plan to `docs/superpowers/plans/shipped/`; de-stale `docs/HOW_IT_WORKS.md` (Notion archival section) + `docs/CODE_MAP.md` (new endpoint + repo functions).
