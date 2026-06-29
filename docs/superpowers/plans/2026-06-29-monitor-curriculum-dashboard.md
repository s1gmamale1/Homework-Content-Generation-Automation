# Monitor Curriculum-Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the Monitor page from one long grade-grouped scroll into a curriculum-level dashboard — `Language → Grade → Subject → Lessons` — so it stays usable at full campaign scale (3 languages × 11 grades × 10+ subjects = hundreds of batches).

**Architecture:** One small backend field (`output_language` on the batches-list response) unblocks a Language dimension; everything else is FE on the existing Monitor (`monitor.tsx` = `MonitorStats` + `WorkerCards` + `BatchFunnel`). We add **language tabs (scoped)**, a **status filter bar defaulting to "Needs attention"**, a **grade filter strip (with "All grades")**, and move lesson lists from inline-expand into a **hand-rolled right-side drawer**. Built on the helpers already shipped (`monitor-grouping.ts`, `batch-status.ts`) + one new pure `monitor-filters.ts`.

**Tech Stack:** FastAPI + SQLAlchemy async (one serializer line); React + TS + react-query + Radix. No FE test runner — acceptance is `tsc --noEmit` + `npm run build` + `npx tsx` for pure helpers + the gate's in-browser eyeball. Phased: each phase ships independently as its own PR + gate.

---

## Approach & key decisions

- **Locked with the user (this brainstorm):**
  - **Grade nav = filter strip + "All grades"** — a horizontal strip of grades-that-have-batches; pick one → only its cards render; "All grades" restores the #59 grouping. Generalizes #59 (All) and the proposal (one grade).
  - **Language = top tabs, scoped** — `Uzbek | English | Russian` tabs; the active language scopes the stat tiles, grade strip, AND cards; each tab shows summary counts. Only languages with batches render.
  - **Scope = full, phased** — Phases 1→4 below, each independently shippable; user gates each.
- **Lesson drawer** (agreed, not contested): inline expand is the worst scroll offender (Chemistry ballooned the page). Move `BatchLessonList` into a right-side drawer so the card grid stays stable.
- **Rejected — adding a Radix Dialog/Sheet dep for the drawer:** the UI kit has only badge/button/card/input/label/select/skeleton (no dialog). Hand-roll a lightweight fixed right panel + overlay (no new dependency) — the drawer is non-modal and simple.
- **Load-bearing facts (verified @ tip `2ea81ad`):**
  - `batches` is `UNIQUE(book_id, transport, output_language)` (#57) → batches ARE per-language; `output_language` is `NOT NULL` (`server_default 'uz'`, CHECK in `('uz','en','ru')`) on `app/models/batch.py`.
  - `BatchSummary` (FE `types.ts:368`) and the serializer `_rollup_payload` (`app/api/v1/batch.py:59`, used by `GET /jobs/batches` AND the launch/preview responses) do **NOT** yet emit `output_language`. That's the only backend gap.
  - `monitor.tsx` polls `["batches"]`/`["workers"]` @3500ms. `BatchFunnel` groups by `book_id` then by grade (`groupBooksByGrade`), each `BookCard` has `TransportRow`s; "Show lessons" currently expands `BatchLessonList` inline (`batch-funnel.tsx`). `BatchLessonList` fetches `GET /jobs/batches/{id}/jobs` (`api.ts:331`), `enabled`-gated.
  - Existing reusable helpers: `monitor-grouping.ts` (`groupBooksByGrade`, `batchActionFlags`), `batch-status.ts` (`transportRowStatus` → `RowStatus` = complete|in_progress|failed|partial). `OutputLanguage` type exists in `types.ts`.

---

## PHASE 1 — Backend: expose `output_language` on the batches list

### Task 1.1: serializer + FE type + test

**Files:**
- Modify: `app/api/v1/batch.py:59` (`_rollup_payload`)
- Modify: `web/src/lib/types.ts:368` (`BatchSummary`)
- Test: `tests/api/test_batches_output_language.py`

- [ ] **Step 1: Write the failing test** (real-DB)

```python
import os, pytest
from httpx import ASGITransport, AsyncClient
pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_INTEGRATION") != "1", reason="real DB")
_HDR = {"Authorization": "Bearer 123"}

@pytest.mark.asyncio
async def test_batches_list_exposes_output_language():
    from main import app
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo
    # Seed one batch (uz default) via the repo helper.
    from app.models.book import Book
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="z"*64, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade="8",
            provider="gemini", model="gemini-2.5-pro", transport="api",
            output_language="en")
        await s.commit(); book_id = book.id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/jobs/batches", headers=_HDR)
        assert r.status_code == 200, r.text
        mine = [b for b in r.json()["batches"] if b["book_id"] == str(book_id)]
        assert mine and mine[0]["output_language"] == "en"
    finally:
        from sqlalchemy import delete
        from app.models.batch import Batch
        async with SessionLocal() as s:
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id)); await s.commit()
```
(Confirm `get_or_create_for_book`'s real signature first — `app/repositories/batches.py` — and match its kwargs; adjust the seed call if names differ.)

- [ ] **Step 2: Run → fails** (`output_language` KeyError).
Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_scratch uv run python -m pytest tests/api/test_batches_output_language.py -q`
(Recreate the scratch DB first per the standing recipe: `createdb -U macmini5 -O edu edu_scratch` then `alembic upgrade head`.)

- [ ] **Step 3: Add the field** — in `_rollup_payload` add `"output_language": batch.output_language,`. In `BatchSummary` add `output_language: OutputLanguage;` (import already present in types.ts).

- [ ] **Step 4: Run → passes.** Then FE: `cd web && npx tsc -p tsconfig.app.json --noEmit` (clean).

- [ ] **Step 5: Commit**
```bash
git add app/api/v1/batch.py web/src/lib/types.ts tests/api/test_batches_output_language.py
git commit -m "feat(monitor): expose output_language on the batches list"
```

> **Ships as its own PR** (Phase 1). Harmless additive field — safe to merge ahead of the FE phases.

---

## PHASE 2 — Language tabs + status filters + Needs-attention default

### Task 2.1: pure `monitor-filters.ts` + tsx test

**Files:** Create `web/src/lib/monitor-filters.ts` + `web/src/lib/monitor-filters.test.ts`

- [ ] **Step 1: Write the failing tsx test** (React-free; run `cd web && npx tsx src/lib/monitor-filters.test.ts`). Cover: `summarizeByLanguage` returns per-language `{lessons, done, running, failed, paused}` counts; `LANGUAGES` order `["uz","en","ru"]`; `bookMatchesStatus(book, filter)` for each of `all|attention|running|failed|paused|complete` (attention = any failed/paused/running across the book's batches). Use `as any` fixtures like `monitor-grouping.test.ts`.

- [ ] **Step 2: Run → fails** (module missing).

- [ ] **Step 3: Implement** — pure functions over `BatchSummary[]` / book-groups, reusing `transportRowStatus` from `./batch-status` and `batchActionFlags` from `./monitor-grouping` where useful:
```ts
import type { BatchSummary } from "./types";
import { transportRowStatus } from "./batch-status";

export const LANGUAGES = ["uz", "en", "ru"] as const;
export type Lang = (typeof LANGUAGES)[number];
export const STATUS_FILTERS = ["attention", "all", "running", "failed", "paused", "complete"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

export interface LangSummary { lang: Lang; lessons: number; done: number; running: number; failed: number; paused: number; }

export function summarizeByLanguage(batches: BatchSummary[]): LangSummary[] {
  return LANGUAGES.map((lang) => {
    const bs = batches.filter((b) => b.output_language === lang);
    const sum = (k: string) => bs.reduce((a, b) => a + ((b.rollup as any)[k] ?? 0), 0);
    return { lang, lessons: bs.reduce((a, b) => a + Object.values(b.rollup).reduce((x, n) => x + (n ?? 0), 0), 0),
      done: sum("done"), running: sum("running") + sum("pending") + sum("cancelling"),
      failed: sum("failed"), paused: bs.filter((b) => b.paused_at != null).length };
  }).filter((s) => s.lessons > 0);
}

/** Does any of a book's batches match the status filter? */
export function bookMatchesStatus(book: BatchSummary[], f: StatusFilter): boolean {
  if (f === "all") return true;
  const r = (b: BatchSummary) => b.rollup as any;
  const anyRunning = book.some((b) => (r(b).pending ?? 0) + (r(b).running ?? 0) + (r(b).cancelling ?? 0) > 0);
  const anyFailed = book.some((b) => (r(b).failed ?? 0) > 0);
  const anyPaused = book.some((b) => b.paused_at != null);
  const allComplete = book.every((b) => transportRowStatus(b) === "complete");
  switch (f) {
    case "attention": return anyFailed || anyPaused || anyRunning;
    case "running": return anyRunning;
    case "failed": return anyFailed;
    case "paused": return anyPaused;
    case "complete": return allComplete;
  }
}
```

- [ ] **Step 4: Run → `OK`** + `npx tsc -p tsconfig.app.json --noEmit` clean.

- [ ] **Step 5: Commit** (`git add web/src/lib/monitor-filters.ts web/src/lib/monitor-filters.test.ts`; `feat(monitor): pure language-summary + status-filter helpers`).

### Task 2.2: language tabs + filter bar + scoped stats (monitor.tsx)

**Files:** Modify `web/src/routes/monitor.tsx`; modify `web/src/components/fleet/monitor-stats.tsx` (accept a pre-filtered batch list); modify `web/src/components/fleet/batch-funnel.tsx` (accept a `statusFilter` + pre-scoped batches).

- [ ] **Step 1:** In `monitor.tsx`: add `activeLang` state (default = first language with batches from `summarizeByLanguage`, fallback `"uz"`) and `statusFilter` state (default `"attention"`). Render a **language tab bar** (one tab per `summarizeByLanguage` entry, showing `lessons · N failed · N running`) and a **status filter bar** (`STATUS_FILTERS`, "attention" first/active). Compute `scoped = batches.filter(b => b.output_language === activeLang)` and pass `scoped` to both `MonitorStats` and `BatchFunnel`.
- [ ] **Step 2:** `MonitorStats` — change to compute its tiles from the passed (already language-scoped) batches; make the **"Needs attention" tile clickable** → sets `statusFilter="attention"` (lift via a prop callback). The other tiles can set their matching filter (Completed→complete, In progress→running). Keep Workers tile global.
- [ ] **Step 3:** `BatchFunnel` — accept a `statusFilter` prop; after grouping by book, drop books where `!bookMatchesStatus(book, statusFilter)` before grade-grouping. (Grade strip comes in Phase 4 — for now keep the #59 grade grouping on the filtered set.)
- [ ] **Step 4:** Verify: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` clean.
- [ ] **Step 5:** Commit (`web/src/routes/monitor.tsx web/src/components/fleet/monitor-stats.tsx web/src/components/fleet/batch-funnel.tsx`; `feat(monitor): language tabs + status filters, default Needs-attention (#proposal)`).

> **Ships as its own PR** (Phase 2). Eyeball: tabs switch language + scope everything; filter bar works; default view = Needs attention.

---

## PHASE 3 — Lesson drawer

### Task 3.1: hand-rolled right-side drawer component

**Files:** Create `web/src/components/fleet/monitor-drawer.tsx`

- [ ] **Step 1:** Build a `MonitorDrawer({ open, title, onClose, children })` — a fixed right-side panel (`fixed right-0 top-0 h-full w-[28rem] max-w-[90vw] z-50` glass styling from `lib/ui`) + a dim overlay (`fixed inset-0 bg-black/40 z-40`) that closes on click; close on `Escape` (a `useEffect` keydown listener while `open`); a header with `title` + a close button. Renders nothing when `!open`. No new dependency.
- [ ] **Step 2:** Verify tsc + build clean (it's unused until 3.2, so just confirm it compiles).
- [ ] **Step 3:** Commit (`feat(monitor): right-side drawer primitive (no new dep)`).

### Task 3.2: open lessons in the drawer instead of inline

**Files:** Modify `web/src/components/fleet/batch-funnel.tsx` (+ likely `monitor.tsx` to host the drawer state)

- [ ] **Step 1:** Lift drawer state to `monitor.tsx` (or a small context in `batch-funnel.tsx`): `{ batchId, title } | null`. In `TransportRow`, change the **"Show lessons"** button so `onClick` sets the drawer target (`batch.batch_id` + a title like `${subject} · grade ${grade} · ${transport}`) instead of toggling the inline `expanded` state. REMOVE the inline `<BatchLessonList .../>` render + its `expanded` state from `TransportRow`.
- [ ] **Step 2:** Render `<MonitorDrawer open={!!target} title={target?.title} onClose={()=>setTarget(null)}>{target && <BatchLessonList batchId={target.batchId} enabled />}</MonitorDrawer>` once, at the Monitor level. `BatchLessonList` is unchanged (still fetches `/jobs/batches/{id}/jobs`).
- [ ] **Step 3:** Verify tsc + build clean.
- [ ] **Step 4:** Commit (`feat(monitor): open lessons in a side drawer; card grid stays stable (#proposal)`).

> **Ships as its own PR** (Phase 3) — the biggest UX win. Eyeball: clicking Show lessons opens the drawer; cards don't stretch; Escape/overlay closes.

---

## PHASE 4 — Grade filter strip

### Task 4.1: grade strip (All + grades-with-batches), scoped to active language

**Files:** Modify `web/src/components/fleet/batch-funnel.tsx` (or lift a `gradeFilter` to `monitor.tsx`); reuse `groupBooksByGrade`.

- [ ] **Step 1:** Derive the grade list from the (language-scoped, status-filtered) books via `groupBooksByGrade` → the `grade` keys, in that order, prefixed with an **"All grades"** chip. Render a horizontal strip; each grade chip shows a small count (e.g. `failed`/`running` from the books in that grade). Add `gradeFilter` state (default `null` = All).
- [ ] **Step 2:** When `gradeFilter` is set, render only that grade's grade-group (no subheader needed since it's the only one); when `null`, keep the #59 grade-grouped sections. Integrate cleanly with the Phase-2 status filter and language scope (all three compose: language → status → grade).
- [ ] **Step 3:** Verify tsc + build clean.
- [ ] **Step 4:** Commit (`feat(monitor): grade filter strip with All-grades (#proposal)`).

> **Ships as its own PR** (Phase 4). Eyeball: grade strip filters to one grade; "All grades" restores grouping; composes with language + status filters.

---

## Self-review notes
- **Coverage:** Language tabs = Phase 1 (field) + 2 (tabs/scope). Filters + Needs-attention default = Phase 2. Lesson drawer = Phase 3. Grade filter = Phase 4. Compact/stable cards = inherent (drawer keeps the grid stable; #59 already compacted cards).
- **Type consistency:** `output_language` is `OutputLanguage` (`"uz"|"en"|"ru"`) end to end; `StatusFilter`/`Lang` are the single source for the filter/tab sets. Pure helpers (`monitor-filters.ts`) are React-free + tsx-tested; components are tsc+build + eyeball.
- **No new deps:** drawer is hand-rolled. Reuses `monitor-grouping.ts`, `batch-status.ts`, `BatchLessonList`, `api.*Batch`.
- **Sequencing:** Phase 1 can merge immediately (additive backend). Phases 2–4 are FE, each on its own branch off the latest base; each does the finish rebase-check. No migration in any phase.
- **Open at review (none blocking):** whether MonitorStats tiles other than "Needs attention" also act as filters (Task 2.2 Step 2 wires Completed/In-progress too — gate can trim); whether the grade-chip counts show failed-only vs failed+running (cosmetic).
