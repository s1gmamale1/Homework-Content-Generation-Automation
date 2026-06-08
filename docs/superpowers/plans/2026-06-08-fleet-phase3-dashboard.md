# Fleet Phase 3 — Operations Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `/fleet` operator screen — launch a Notion subject as a batch, watch rollups + PC liveness, drill into a batch's lessons — built on the existing endpoints plus exactly one new read endpoint.

**Architecture:** Backend adds one DISTINCT-ON read (`GET /jobs/batches/{id}/jobs`). Frontend adds a `/fleet` route (react-query polling, no SSE) reusing the space-dashboard kit. Everything else reuses Phase 0–2 + existing endpoints.

**Tech Stack:** Backend — FastAPI, SQLAlchemy async, Postgres. Frontend — React 19, react-router, **@tanstack/react-query** (polling via `refetchInterval`, actions via `useMutation`), Tailwind, `lib/ui.ts` consts, `SpaceBackdrop`, `sonner` toasts, `lucide-react` icons, Geist.

**Spec:** `docs/superpowers/specs/2026-06-08-fleet-phase3-dashboard-design.md` (approved, reviewer-verified).

**Test invocation:**
- Backend real-DB: `cd /c/Users/Recruiter/Desktop/homework-fleet-engine && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework /c/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe -m pytest <path> -q` (throwaway PG on 5436, migrated to head `a1b2c3d4e5f6`). DB-free baseline `5 failed (Notion) / 330+ passed / N skipped`.
- **Frontend gate** (no FE unit harness): `cd web && npx tsc -p tsconfig.app.json --noEmit` clean **and** `npm run build` succeeds. FastAPI serves `web/dist`, so FE changes need `npm run build` to show up.

**Standing rules:** stage ONLY the files each task lists (other sessions touch `web/`); commit per task; controller stress-tests every commit (read diff + re-run gate).

---

## File Structure

**Backend (one endpoint):**
- Modify `app/repositories/batches.py` — add `list_jobs(session, batch_id)`.
- Modify `app/api/v1/batch.py` — add `GET /jobs/batches/{batch_id}/jobs`.
- Modify `tests/integration/test_batches.py` — add a drill-in case.

**Frontend:**
- Modify `web/src/lib/types.ts` — `Worker`, `BatchSummary`, `BatchRollup`, `BatchLessonRow`.
- Modify `web/src/lib/api.ts` — `listBatches`, `getBatch`, `batchJobs`, `launchBatch`, `listWorkers` (methods on the `api` object).
- Modify `web/src/App.tsx` (route) + `web/src/components/layout.tsx` (nav pill + `/fleet` in the `wide` set).
- Create `web/src/routes/fleet.tsx` + `web/src/components/fleet/{launcher,worker-cards,batch-funnel,rollup-bar,batch-lesson-list}.tsx`.

---

### Task 1: Backend — `GET /jobs/batches/{batch_id}/jobs` (per-lesson drill-in)

**Files:** Modify `app/repositories/batches.py`, `app/api/v1/batch.py`; Test `tests/integration/test_batches.py`

- [ ] **Step 1: Write the failing real-DB test** — append to `tests/integration/test_batches.py`:

```python
@pytest.mark.asyncio
async def test_batch_jobs_drilldown_is_per_lesson_latest():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, toc_ids = await _seed_book("k", n=4)
    try:
        async with _client() as c:
            r = await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            bid = r.json()["batch_id"]
            # fail lesson 0's job, then re-launch -> a NEW (newer) job for lesson 0
            async with SessionLocal() as s:
                jid = (await s.execute(
                    select(HomeworkJob.id).where(HomeworkJob.toc_entry_id == toc_ids[0]))
                ).scalar_one()
                (await s.get(HomeworkJob, jid)).status = "failed"
                await s.commit()
            await c.post("/api/v1/jobs/batch", headers=_HDR, json={"book_id": str(book_id)})
            g = await c.get(f"/api/v1/jobs/batches/{bid}/jobs", headers=_HDR)
        assert g.status_code == 200
        rows = g.json()["jobs"]
        assert len(rows) == 4, f"one row per lesson, got {len(rows)}"
        assert [row["order_index"] for row in rows] == [0, 1, 2, 3], "ordered by order_index"
        lesson0 = next(r for r in rows if r["order_index"] == 0)
        assert lesson0["status"] == "pending", "shows the NEWEST job (the retry), not the failed one"
        assert all("section_title" in r and "job_id" in r and "attempts" in r for r in rows)
        # 404 for an unknown batch
        async with _client() as c:
            nf = await c.get("/api/v1/jobs/batches/00000000-0000-0000-0000-000000000099/jobs", headers=_HDR)
        assert nf.status_code == 404
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run it — expect failure** (404 / route missing). `RUN_DB_INTEGRATION=1 DATABASE_URL=… <venv> -m pytest tests/integration/test_batches.py::test_batch_jobs_drilldown_is_per_lesson_latest -q`

- [ ] **Step 3: Add `list_jobs` to `app/repositories/batches.py`:**

```python
async def list_jobs(session: AsyncSession, batch_id: UUID) -> list[dict]:
    """Per-lesson-latest rows for a batch: one row per toc_entry (its newest job),
    joined to the lesson title, ordered by order_index. Mirrors rollup_for_batch's
    DISTINCT ON but returns rows; row count == the rollup denominator."""
    from app.models.toc_entry import TOCEntry
    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.attempts.label("attempts"),
            HomeworkJob.current_phase.label("current_phase"),
            HomeworkJob.error_message.label("error_message"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    stmt = (
        select(
            latest.c.job_id, latest.c.toc_entry_id, latest.c.status,
            latest.c.attempts, latest.c.current_phase, latest.c.error_message,
            TOCEntry.section_title, TOCEntry.order_index,
        )
        .join(TOCEntry, TOCEntry.id == latest.c.toc_entry_id)
        .order_by(TOCEntry.order_index)
    )
    rows = await session.execute(stmt)
    return [
        {
            "job_id": str(r.job_id),
            "toc_entry_id": str(r.toc_entry_id),
            "section_title": r.section_title,
            "order_index": r.order_index,
            "status": r.status,
            "attempts": r.attempts,
            "current_phase": r.current_phase,
            "error_message": r.error_message,
        }
        for r in rows
    ]
```

(`select`, `func`, `AsyncSession`, `HomeworkJob` already imported in `batches.py`.)

- [ ] **Step 4: Add the route to `app/api/v1/batch.py`** (after `get_batch`):

```python
@router.get("/jobs/batches/{batch_id}/jobs")
async def list_batch_jobs(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    return {"batch_id": str(batch_id), "jobs": await batches_repo.list_jobs(session, batch_id)}
```

- [ ] **Step 5: Run the test — expect green.** Then the whole batches file: `… -m pytest tests/integration/test_batches.py -q` → all green.

- [ ] **Step 6: DB-free baseline holds** — `<venv> -m pytest tests/ -q` → `5 failed (Notion) / 330+ passed / N skipped`, no new failures.

- [ ] **Step 7: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add app/repositories/batches.py app/api/v1/batch.py tests/integration/test_batches.py
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): GET /jobs/batches/{id}/jobs — per-lesson drill-in (Phase 3)"
```

---

### Task 2: Frontend data layer + `/fleet` route scaffold

**Files:** Modify `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/App.tsx`, `web/src/components/layout.tsx`; Create `web/src/routes/fleet.tsx`

- [ ] **Step 1: Add types** to `web/src/lib/types.ts`:

```typescript
export interface Worker {
  pc_id: string;
  last_heartbeat: string | null;
  status: string;
  notes: string | null;
  online: boolean;
}

export type BatchRollup = Partial<Record<JobStatus, number>>;

export interface BatchSummary {
  batch_id: string;
  book_id: string;
  subject: string;
  grade: string | null;
  provider: string;
  model: string | null;
  rollup: BatchRollup;
  lessons_covered: number;
  complete: boolean;
  created_at: string;
}

export interface BatchLessonRow {
  job_id: string;
  toc_entry_id: string;
  section_title: string;
  order_index: number;
  status: JobStatus;
  attempts: number;
  current_phase: string | null;
  error_message: string | null;
}
```

- [ ] **Step 2: Add `api` methods** to `web/src/lib/api.ts` (inside the `api` object; follow the existing `authFetch`/`unwrap` pattern). Import the new types at the top.

```typescript
  async listBatches(): Promise<BatchSummary[]> {
    const res = await authFetch("/api/v1/jobs/batches");
    return (await unwrap<{ batches: BatchSummary[] }>(res)).batches;
  },
  async getBatch(batchId: string): Promise<BatchSummary> {
    const res = await authFetch(`/api/v1/jobs/batches/${encodeURIComponent(batchId)}`);
    return unwrap<BatchSummary>(res);
  },
  async batchJobs(batchId: string): Promise<BatchLessonRow[]> {
    const res = await authFetch(`/api/v1/jobs/batches/${encodeURIComponent(batchId)}/jobs`);
    return (await unwrap<{ jobs: BatchLessonRow[] }>(res)).jobs;
  },
  async launchBatch(body: {
    book_id: string; toc_entry_ids?: string[]; provider?: string; model?: string | null;
  }): Promise<BatchSummary & { jobs_created: number; jobs_adopted: number; jobs_skipped: number }> {
    const res = await authFetch("/api/v1/jobs/batch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    return unwrap(res);
  },
  async listWorkers(): Promise<{ workers: Worker[]; total: number; online: number; stale_after_seconds: number }> {
    const res = await authFetch("/api/v1/workers");
    return unwrap(res);
  },
```

- [ ] **Step 3: Register the route** in `web/src/App.tsx` — import `FleetPage` from `@/routes/fleet` and add inside the protected `<Route element={<Layout/>}>` group:

```tsx
            <Route path="/fleet" element={<FleetPage />} />
```

- [ ] **Step 4: Add the nav pill** in `web/src/components/layout.tsx` — import an icon (`Rocket` from `lucide-react`), add a `NavItem` after the Usage item, and add `/fleet` to the `wide` set:

```tsx
              <NavItem to="/fleet" icon={<Rocket className="size-4" />}>
                Fleet
              </NavItem>
```
```tsx
  const wide =
    pathname.startsWith("/usage") || pathname.startsWith("/library") || pathname.startsWith("/fleet");
```

- [ ] **Step 5: Create the page scaffold** `web/src/routes/fleet.tsx` — `SpaceBackdrop` + three zones as placeholders, react-query polling wired (filled in Tasks 3–4). Match how `usage.tsx` mounts `SpaceBackdrop` + a `relative z-10` content wrapper.

```tsx
import { useQuery } from "@tanstack/react-query";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";

export function FleetPage() {
  const batches = useQuery({ queryKey: ["batches"], queryFn: api.listBatches, refetchInterval: 3500 });
  const workers = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers, refetchInterval: 3500 });
  const books = useQuery({ queryKey: ["books"], queryFn: api.listBooks, refetchInterval: 3500 });

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fleet</h1>
          <p className="mt-1 text-white/55">Launch a whole subject; watch the workers chew through it.</p>
        </div>
        {/* Task 3: <FleetLauncher books={books.data} batches={batches.data} /> + <WorkerCards data={workers.data} /> */}
        {/* Task 4: <BatchFunnel batches={batches.data} /> */}
      </div>
    </>
  );
}
```

- [ ] **Step 6: FE gate** — `cd web && npx tsc -p tsconfig.app.json --noEmit` clean + `npm run build` succeeds. `/fleet` renders the header + polls (Network tab shows the three calls).

- [ ] **Step 7: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add web/src/lib/types.ts web/src/lib/api.ts web/src/App.tsx web/src/components/layout.tsx web/src/routes/fleet.tsx
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): /fleet route + data layer (api methods, types, nav) (Phase 3)"
```

---

### Task 3: Launcher (prepare + server-derived tray) + WorkerCards

**Files:** Create `web/src/components/fleet/launcher.tsx`, `web/src/components/fleet/worker-cards.tsx`; Modify `web/src/routes/fleet.tsx`

- [ ] **Step 1: `worker-cards.tsx`** — grid of glass cards from `listWorkers`. Each card: `pc_id`, an online/offline dot (green `oklch(0.78 0.10 145)` if `online`, muted grey otherwise), `last_heartbeat` relative ("3m ago"), header "online X / N". Use `cn(CARD, …)` from `lib/ui.ts`. Props: `{ data?: { workers: Worker[]; online: number; total: number } }`.

- [ ] **Step 2: `launcher.tsx`** — two parts in a `CARD`:
  - **Prepare form:** Grade `<select>` from `api.listNotionGrades()` (returns `NotionGrade[]`, each `{ name, page_id }`) → Subject `<select>` from `api.listNotionSubjects(gradePageId)` (returns `NotionSubject[]` `{ notion_title, page_id, app_subject, has_textbook }`; disable options where `!has_textbook`) → **Prepare** button (`PRIMARY_BTN`). On click: `useMutation(() => api.fetchBookFromNotion(subjectPageId, grade))`; on success `toast.success` + invalidate `["books"]`. (Both methods exist in `api.ts:208/213`.)
  - **Tray (server-derived, per spec §6):** from the already-polled `books` + `batches`:
    ```ts
    const batchedBookIds = new Set((batches ?? []).map(b => b.book_id));
    const preparing = books.filter(b => b.status === "toc_extracting");
    const failed    = books.filter(b => b.status === "failed");
    const ready     = books.filter(b => b.status === "toc_ready" && !batchedBookIds.has(b.id));
    ```
    Render rows: **preparing** → spinner + "extracting lessons… ~1–3 min"; **ready** → "N lessons" + provider `<select>` (from `api.getAgentModels()` → `ProviderModelManifest`, default `claude`) + **Launch** (`useMutation(api.launchBatch)` → toast + invalidate `["batches","books"]`); **failed** → `book.error_message` + a Retry (re-run prepare) button. (Lesson count for a ready book = `book.toc?.length` — `Book.toc: TOCEntry[] | null`; `GET /books` may not populate `toc`, so fetch it lazily per ready book via `api.getBook(id)` which includes `toc`.)

- [ ] **Step 3: Wire into `fleet.tsx`** — render `<FleetLauncher books={books.data} batches={batches.data} />` and `<WorkerCards data={workers.data} />` in a two-column grid (`grid-template-columns: minmax(320px, 360px) 1fr` on `sm+`).

- [ ] **Step 4: FE gate** — `tsc --noEmit` + `npm run build` clean.

- [ ] **Step 5: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add web/src/components/fleet/launcher.tsx web/src/components/fleet/worker-cards.tsx web/src/routes/fleet.tsx
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): launcher (prepare + server-derived tray) + worker cards (Phase 3)"
```

---

### Task 4: Batch funnel + rollup bar + lesson drill-in

**Files:** Create `web/src/components/fleet/rollup-bar.tsx`, `batch-funnel.tsx`, `batch-lesson-list.tsx`; Modify `web/src/routes/fleet.tsx`

- [ ] **Step 1: `rollup-bar.tsx`** — a flex segmented bar from a `BatchRollup`. **Render EVERY status the rollup can carry** (spec §4c): order + color map
  ```
  done → green oklch(0.78 0.10 145) · running → #4d8dff · cancelling → amber oklch(0.80 0.12 85)
  pending → white/14 · cancelled → white/30 (muted) · failed → red oklch(0.70 0.16 25)
  ```
  Each segment `flex: count`. Below: a legend of the non-zero statuses + "covered / total · pct". Iterate the rollup object (don't hardcode 4) so cancelled/cancelling always appear when present.
- [ ] **Step 2: `batch-lesson-list.tsx`** — given `batchId`, `useQuery(["batch-jobs", batchId], () => api.batchJobs(batchId), { refetchInterval: 3500, enabled })`. Render a row per `BatchLessonRow`: `order_index`. `section_title`, a status chip (color per the map), attempts; row actions — **Cancel** (`useMutation(() => api.cancelJob(job_id))`, shown when `status` is `pending`/`running`) / **Retry** (`api.retryJob`, when `failed`) / **Open** (`Link to /job/{job_id}`). On mutation success: toast + invalidate `["batch-jobs", batchId]` and `["batches"]`. This component is also reused by the launcher's subset picker (Task 5) with a `selectable` mode (checkboxes, no row actions).
- [ ] **Step 3: `batch-funnel.tsx`** — one `CARD` per `BatchSummary`: `subjectLabel(subject)` · grade, `<RollupBar rollup={b.rollup} covered={b.lessons_covered} />`, `complete` chip, and an expand toggle that mounts `<BatchLessonList batchId={b.batch_id} enabled={expanded} />`.
- [ ] **Step 4: Wire `<BatchFunnel batches={batches.data} />`** into `fleet.tsx` below the launcher/worker row.
- [ ] **Step 5: FE gate** — `tsc --noEmit` + `npm run build` clean.
- [ ] **Step 6: Commit**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add web/src/components/fleet/rollup-bar.tsx web/src/components/fleet/batch-funnel.tsx web/src/components/fleet/batch-lesson-list.tsx web/src/routes/fleet.tsx
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): batch funnel + all-status rollup bar + lesson drill-in (Phase 3)"
```

---

### Task 5: Subset launch + acceptance + worklog 0051

**Files:** Modify `web/src/components/fleet/launcher.tsx` (+ `batch-lesson-list.tsx` selectable mode); `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`

- [ ] **Step 1: Subset picker** — the ready-row's **Launch** defaults to all lessons; a secondary "choose lessons" toggle reveals `<BatchLessonList selectable>` (for a not-yet-launched book this lists its `toc_entries` via `api.getBook(id)`/existing TOC read, since there's no batch yet) → launches `toc_entry_ids`. Keep it secondary; "Launch all" is the primary path. FE gate clean.
- [ ] **Step 2: Commit the subset work.**

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add web/src/components/fleet/launcher.tsx web/src/components/fleet/batch-lesson-list.tsx
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "feat(fleet): optional lesson-subset launch (Phase 3)"
```

- [ ] **Step 3: Acceptance — real click-through.** `cd web && npm run build`, then bring up the API + a worker container against a throwaway PG (extend the Phase-2 acceptance stack: API `WORKER_CONCURRENCY=0`, one worker, `AUTH_TOKEN` set). In the browser at the API origin, log in, open `/fleet`: prepare a subject (or seed a `toc_ready` book) → it appears ready in the tray → Launch → the funnel shows a batch filling → expand it → the drill-in lists lessons with live status → Cancel/Retry a lesson and watch it reflect. Record the result. (Full Notion `prepare` needs `.env`; if absent, seed a `toc_ready` book directly and exercise launch→funnel→drill-in.)

- [ ] **Step 4: Full suite + FE gate** — `<venv> -m pytest tests/ -q` at baseline; `cd web && npx tsc -p tsconfig.app.json --noEmit` + `npm run build` clean.

- [ ] **Step 5: Worklog 0051 + INDEX row**, then commit.

```bash
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git -C "C:/Users/Recruiter/Desktop/homework-fleet-engine" commit -m "docs(memory): worklog 0051 — fleet Phase 3 (operations dashboard)"
```

---

## Self-Review

**Spec coverage:** §3 endpoint → Task 1 (with the per-lesson-latest + 404 test). §4a launcher + server-derived tray → Tasks 3/5. §4b PC cards → Task 3. §4c funnel + all-status rollup bar + drill-in (cancel/retry) → Task 4. §5 polling (react-query `refetchInterval`) → Tasks 2–4. §6 theme (`SpaceBackdrop` + `lib/ui.ts` + `subjectLabel`) → throughout. §7 file map ≡ the tasks. §8 testing (real-DB backend + tsc/build + click-through) → Tasks 1/5.

**Placeholder scan:** Backend + api/types are complete code. The React components are specified as structure + exact data-logic (the derived tray, the all-status rollup map, the drill-in mutations are given verbatim) + the exact `lib/ui.ts` classes, endpoints, and reused method names (`listNotionGrades`/`listNotionSubjects`/`getAgentModels`/`fetchBookFromNotion`/`getBook` — all verified in `api.ts`) — the realistic granularity for a FE plan with no unit harness (the gate is `tsc` + build + the click-through).

**Type consistency:** `BatchSummary`/`BatchRollup`/`BatchLessonRow`/`Worker` (Task 2) match the backend payloads (`_rollup_payload` + `list_jobs` + `GET /workers`) and are consumed by Tasks 3–4. `api.launchBatch` body matches the Phase-2 endpoint. `error_message` (not `last_error`) per the spec fix. The new endpoint's row count == the rollup denominator (same DISTINCT-ON set) → funnel and drill-in never disagree.

**Pre-flight for the implementer:** confirm the throwaway PG on 5436 is up + migrated to `a1b2c3d4e5f6` before Task 1; confirm `cd web && npm install` is current before the FE tasks.
