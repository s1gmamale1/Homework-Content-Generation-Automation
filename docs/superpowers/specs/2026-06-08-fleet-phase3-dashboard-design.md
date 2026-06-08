# Fleet Phase 3 — Operations Dashboard (design)

**Status:** approved design (brainstormed 2026-06-08 with the visual companion; mockups under `.superpowers/brainstorm/`). Realizes spec `2026-06-06-autonomous-fleet-design.md` §6 Phase 3, **scoped down** to a shippable MVP (see Deviations).

**One-liner:** A new **`/fleet`** operator screen — launch a whole subject as a batch, watch the fleet chew through it (rollups + live PC liveness), and drill into a batch to act on individual lessons. Reuses the existing space-dashboard theme. **Frontend + exactly one new read endpoint.**

---

## 1. Scope

**In:**
- A new **`/fleet`** route (operations hub). The existing content-pipeline pages (Upload / Library / Book / Section / Job / Preview / Usage) are untouched.
- **Non-blocking launcher** — pick a Notion subject → "Prepare" (background download + TOC extraction, ~1–3 min) → a **tray** shows each subject's live state → **Launch** when ready.
- **Batch funnel** — each batch as a live rollup bar + counts (poll `GET /jobs/batches`).
- **Fleet PC cards** — worker liveness + current state (poll `GET /workers`).
- **Batch drill-in** — click a batch → expand to its **individual lessons** (per-lesson status + Cancel / Retry + link to the job page).

**The ONE new backend piece:** `GET /jobs/batches/{batch_id}/jobs` — the per-lesson list (the rollup returns counts only; the drill-in needs rows). Everything else reuses existing endpoints.

**Out of scope (deferred — captured in `WISHLIST.md` under "Fleet"):** batch-level Cancel-all / Retry-failed (`fleet-ctrl-1/2`), Pause / Resume (`fleet-ctrl-3`), PC drain (`fleet-ctrl-4`), live SSE dashboard (`fleet-ui-2`), historical-batches view (`fleet-ui-3`), richer PC cards (`fleet-ui-4`), the CLI/API launch toggle + cost/credential features (Phase 4).

### Deviations from the master spec (§6 Phase 3)
- The master spec lists **pause / cancel / retry / drain** controls and a **CLI/API toggle**. Phase 3 ships **per-lesson Cancel/Retry only** (reusing existing endpoints) and **no toggle** (Gemini-API is Phase 4). Pause/drain/batch-level controls are deferred (Wishlist). Intentional — keeps Phase 3 to one new endpoint.
- Auth: the master spec floated "reads public, single admin token." Phase 3 keeps the app's **existing token gate** (`get_current_user` on all `/api/v1`) — no new auth model.

---

## 2. Reused endpoints (no change)

| Purpose | Endpoint | Notes |
|---|---|---|
| Notion grade list | `GET /api/v1/notion/grades` | subject picker |
| Notion subjects (per grade) | `GET /api/v1/notion/grades/{id}/subjects` | gives `page_id`, `app_subject`, `has_textbook` |
| Prepare a subject | `POST /api/v1/books/from-notion` `{subject_page_id, grade}` | download + ingest + fire async TOC extraction → book `toc_extracting` → `toc_ready`/`failed` |
| Live extraction status | `GET /api/v1/books/{book_id}/toc/stream` (SSE) | optional snappier "prepare done" signal |
| List books (tray source) | `GET /api/v1/books` | filter to `toc_extracting`/`toc_ready`/`failed` for the tray |
| Launch a batch | `POST /api/v1/jobs/batch` `{book_id, toc_entry_ids?, provider?, model?}` | Phase 2 |
| Batch rollups | `GET /api/v1/jobs/batches` + `/{id}` | Phase 2 — funnel bars + counts |
| Fleet liveness | `GET /api/v1/workers` | Phase 1 — PC cards |
| Provider list | `GET /api/v1/agent/models` | provider/model picker (default `claude`) |
| Per-lesson cancel / retry | `POST /api/v1/jobs/{id}/cancel` · `/retry` | drill-in row actions |
| Per-lesson detail link | existing `/job/:id` route | drill-in "open" |

All of the above are already wired in `web/src/lib/api.ts` (`listBooks`, `fromNotion`, `cancelJob`, `retryJob`, notion helpers, …) or trivially added there.

---

## 3. The one new endpoint — `GET /jobs/batches/{batch_id}/jobs`

The drill-in needs **one row per lesson** (its latest job), consistent with the rollup's per-lesson-latest semantics. Implemented with the same `DISTINCT ON (toc_entry_id)` pattern as `batches_repo.rollup_for_batch` / `jobs.latest_by_section`, joined to `toc_entries` for the lesson title.

**Response:** `{ "batch_id": "...", "jobs": [ { toc_entry_id, section_title, order_index, job_id, status, attempts, current_phase, last_error } , … ] }` — ordered by `order_index`. 404 if the batch doesn't exist.

- New repo fn `batches_repo.list_jobs(session, batch_id)` — `DISTINCT ON (toc_entry_id) … WHERE batch_id = X ORDER BY toc_entry_id, created_at DESC` selecting the job + its `TOCEntry.section_title`/`order_index`, then re-sorted by `order_index`.
- New route in `app/api/v1/batch.py` (auth-gated, same router).
- The row count == the rollup denominator == lessons covered (same DISTINCT-ON set) — so drill-in and funnel never disagree.

---

## 4. The `/fleet` page

Single route `/fleet`, added to `web/src/App.tsx` (protected, under the existing layout) + a **"Fleet" nav link** in `components/layout.tsx`. Index stays Upload. Three stacked zones inside the standard `SpaceBackdrop` + `relative z-10` wrapper:

### 4a. Launcher (non-blocking) — top-left card + tray
- **Prepare card:** Grade select (`/notion/grades`) → Subject select (`/notion/grades/{id}/subjects`, disabled if `!has_textbook`) → **Prepare** = `POST /books/from-notion {subject_page_id, grade}`. Non-blocking: returns immediately; the book begins extracting.
- **Tray** (driven by polling `GET /books`, filtered to fleet-relevant statuses): one row per recently-prepared subject:
  - `toc_extracting` → "extracting lessons… ~1–3 min" + spinner (**preparing**).
  - `toc_ready` (no batch yet) → "N lessons" + provider picker + **Launch** (`POST /jobs/batch`).
  - `failed` → the book's `error_message` inline + **Retry** (re-prepare) / **Dismiss**.
  - An **already-`toc_ready`** subject (reused book) appears straight in the ready state — no wait.
- **Durability:** tray state is derived from book status in the DB, so it survives a refresh and is the same for any operator (not just client-side). *(Which books count as "in the tray" = a frontend filter over `GET /books`; see §6 open detail.)*
- **Subset (optional):** the ready row's primary action is **Launch all N**; a secondary "choose lessons" reveals the lesson-list component (§4c) with checkboxes → launches `toc_entry_ids`. Cheap because the lesson-list component already exists for the drill-in.

### 4b. Fleet PC cards
Grid of worker cards from `GET /workers`: `pc_id`, online/offline dot (from the `online` flag), `last_heartbeat` ("3m ago"), and a header "online X / N". (Current-job/throughput enrichment = deferred `fleet-ui-4`.)

### 4c. Batches funnel + drill-in
- **Funnel:** `GET /jobs/batches` → one card per batch: subject·grade, a segmented rollup bar (done/running/pending/failed using the theme status colors), "41 / 50 · 82%", derived `complete`.
- **Drill-in:** clicking a batch card expands (or routes to `/fleet/batch/:id`) and calls the new `GET /jobs/batches/{id}/jobs` → a **lesson list**: per-lesson `section_title`, status chip, attempts, and row actions **Cancel** (`POST /jobs/{id}/cancel`, when pending/running) / **Retry** (`POST /jobs/{id}/retry`, when failed) / **Open** (`/job/:id`). This lesson-list is a reusable component (also used by the launcher's subset picker).

---

## 5. Data refresh
**Polling, not SSE** (live SSE for batches/workers is deferred `fleet-ui-2`). While on `/fleet`, poll on an interval (~3–4 s): `GET /jobs/batches`, `GET /workers`, and `GET /books` (tray). The drill-in lesson list polls `GET /jobs/batches/{id}/jobs` while open. Use a shared `useInterval`/`usePolling` hook (`web/src/hooks`). The book `toc/stream` SSE is available as an optional faster "prepare done" trigger but the `GET /books` poll already covers it — keep MVP poll-only unless a poll feels laggy.

---

## 6. Theme — reuse, don't reinvent
Match the existing space-dashboard kit exactly (already validated in the v2/v3 mockups):
- **Backdrop:** `<SpaceBackdrop/>` (`components/space-backdrop.tsx`) — navy→purple aurora + starfield.
- **Cards / buttons / inputs:** the `lib/ui.ts` class consts — `CARD`, `PRIMARY_BTN` (purple→blue gradient), `GLASS_BTN`, `GHOST_BTN`, `INPUT_GLASS`, `SELECT_TRIGGER`, `BACK_PILL`. Compose with `cn(...)`.
- **Font:** Geist (already the app font). **Status colors:** theme tokens — success/green `oklch(0.78 0.10 145)`, error/red `oklch(0.70 0.16 25)`, running = the blue accent `#4d8dff`, pending = `white/14`.
- **Header/nav:** the existing floating glass header (`components/layout.tsx`) — add the "Fleet" pill.
- Reuse `subjectLabel` (`lib/subjects.ts`) for subject display.

**Open detail to settle in the plan (not blocking):** which books appear in the launcher tray. Default proposal: books with `status = toc_extracting` (always) + `toc_ready` books that have **no batch yet** (launchable) + recently `failed` Notion-fetched books — a client-side filter over `GET /books`. If "no batch yet" needs a flag the list doesn't carry, fall back to: show `toc_extracting` + the most-recently-prepared book this session (client-remembered) until launched.

---

## 7. File map (for the plan)

**Backend (one endpoint):**
- Modify `app/repositories/batches.py` — add `list_jobs(session, batch_id)` (DISTINCT-ON rows + `toc_entries` join).
- Modify `app/api/v1/batch.py` — add `GET /jobs/batches/{batch_id}/jobs`.
- Test `tests/integration/test_batches.py` — add a case: drill-in list returns one row per lesson (latest job), retried lesson shows its newest job, count == rollup denominator.

**Frontend:**
- Create `web/src/routes/fleet.tsx` — the page (launcher + funnel + PC cards), composing the pieces below.
- Create components: `FleetLauncher` (prepare card + tray), `BatchFunnel` (rollup cards), `BatchLessonList` (reusable drill-in + subset picker), `WorkerCards`.
- Modify `web/src/App.tsx` (route) + `components/layout.tsx` (nav link).
- Modify `web/src/lib/api.ts` — add `listBatches`, `getBatch`, `batchJobs(id)`, `launchBatch(...)`, `listWorkers()` (and reuse existing `fromNotion`, `listBooks`, `cancelJob`, `retryJob`, notion helpers).
- Modify `web/src/lib/types.ts` — `Batch`, `BatchRollup`, `BatchLessonRow`, `Worker` types.
- A `usePolling` hook in `web/src/hooks` if one doesn't already exist.

---

## 8. Testing strategy
- **Backend:** the new `list_jobs` endpoint gets a guarded real-DB integration case (per-lesson-latest, count == rollup). The DB-free suite stays at baseline.
- **Frontend:** `npx tsc -p tsconfig.app.json --noEmit` clean + `npm run build` succeeds (the project's standing FE gate — there's no FE unit harness). Manual smoke: prepare a subject → tray shows extracting → ready → launch → funnel fills → drill-in lists lessons → cancel/retry a lesson. (FastAPI serves the built `web/dist`, so a real end-to-end click-through is possible against a worker.)
- **Acceptance:** a click-through against the containerized API + a worker (extend the Phase-2 acceptance stack): prepare → launch → watch the funnel advance + drill-in reflect per-lesson status.

---

## 9. Locked decisions
1. **Bare-minimum scope** — launch + watch + liveness + lesson drill-in; per-lesson Cancel/Retry reuse existing endpoints. No pause/drain/batch-level/toggle (deferred).
2. **One new backend endpoint** — `GET /jobs/batches/{id}/jobs` (per-lesson-latest rows); tray reuses `GET /books`.
3. **Non-blocking launcher** — Prepare fires async extraction (~1–3 min); a DB-status-derived tray shows preparing → ready → failed; Launch when ready; already-prepared subjects are instant.
4. **Polling, not SSE** (SSE deferred `fleet-ui-2`); ~3–4 s while on `/fleet`.
5. **Theme = the existing kit** (`SpaceBackdrop` + `lib/ui.ts` consts + Geist + oklch status colors) — no new design language.
6. **New `/fleet` route + nav pill**; existing pages unchanged; auth = the existing token gate.
