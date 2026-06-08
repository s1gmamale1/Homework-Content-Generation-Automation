# Fleet Phase 2 — Batch Automation (design)

**Status:** approved design (brainstormed + 4-round reviewed). Supersedes the Phase 2 sketch in `2026-06-06-autonomous-fleet-design.md` §5/§6 (which is narrowed to match — see "Deviation from the master spec" below).

**One-liner:** Turn *generate-one-lesson-at-a-time* into *launch a whole textbook's lessons as one tracked batch* — one `pending` job per lesson into the shared Postgres queue the fleet already drains, with a drift-free rollup. **API + DB only; no UI** (the dashboard is Phase 3).

---

## 1. Scope & the fan-out-only decision

A "batch" is a **label + a tally** over jobs. It only ever sees an **already-`toc_ready` book** and fans out one job per lesson. It does **not** drive the fragile download→ingest→TOC-extraction pipeline — that already exists (`POST /books/from-notion` → `ingest_pdf` → fire-and-forget `toc_extractor.run`) and already handles failure first-class (`book.status='failed'` + `error_message` + SSE). Re-wrapping it would duplicate that lifecycle for marginal gain; the batch staying a tally is what keeps rollups and idempotency trivially clean.

"Walk away" = the **generation** being hands-off (≥10 PCs × `worker_concurrency` lessons, each lesson internally DAG-phase-parallel). Prep (fetch one textbook → `toc_ready`) stays a single quick operator action; Phase 3's dashboard stitches the two clicks into one button (a UI concern, deferred).

**Out of scope (later phases):** any UI/dashboard (Phase 3); `generation_mode` cli/api + the Gemini-API provider (Phase 4 — until it exists, a batch just carries a `provider`/`model`, no mode enum); host-affinity (cut permanently — whole-job work-stealing).

### Deviation from the master spec (explicit, intentional)
`2026-06-06-autonomous-fleet-design.md` §5 says *"TOC-extraction failure is a first-class state the launcher handles"* and pitches *"paste a subject, walk away."* Phase 2 **narrows** this: the launcher only ever sees `toc_ready` books; TOC-extraction failure is owned by the **book** flow (which already surfaces it), not the launcher. The master §5 is edited to point here. This narrowing is deliberate (don't duplicate an existing, working lifecycle) — flagged so a future review doesn't read it as the plan missing the spec.

---

## 2. Data model

### New table `batches`
Immutable facts only — **no materialized status counters** (they drift the moment a lesson is retried/topped-up; the tally is computed on read instead).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `book_id` | UUID FK → books | **`UNIQUE`** — enforces *one logical batch per textbook* at the DB and makes find-or-create race-safe (§4). |
| `subject` | String(64) | denormalized from book |
| `grade` | String(32) nullable | denormalized from book |
| `provider` | String(32) | **launch-default label only** — per-job `provider` is authoritative (a mixed-provider top-up leaves this as the first-launch value; reads must not imply the whole batch ran on one provider). |
| `model` | String(128) nullable | launch-default label only (same caveat). |
| `notion_source` | String(512) nullable | traceability label (e.g. the subject page id/title), optional. |
| `created_at`, `updated_at` | DateTime(tz) | |

**No stored lesson count — the rollup denominator is *derived on read* (§4).** An earlier draft stored `lessons_targeted`; it's dropped because it's redundant *and* drift-prone: adoption guarantees every targeted lesson ends up with a `batch_id=X` job, so the set of lessons the batch covers == the distinct `toc_entry_id`s with `batch_id=X` — exactly what the per-lesson-latest rollup already enumerates. Storing + "expanding on top-up" would reintroduce the maintained value decision #3 exists to avoid. **Correctness condition:** the launch reconcile (§3) runs in **one atomic transaction**, so there is never a partial-enqueue state where the derived count under-reports the requested set.

### Modified table `homework_jobs`
- Add `batch_id` — **nullable** UUID FK → `batches`, **indexed**. Nullable because single-lesson `/generate` jobs have no batch. One job ≤ one batch → a plain FK, **no junction table**.

### Migration
One Alembic revision (down_revision = the current head `d5e9f1a2b3c4`, the Phase 1 workers table): `create table batches` (with `UNIQUE(book_id)`) + `add column homework_jobs.batch_id` + index on `batch_id` + FK. No data backfill (existing jobs keep `batch_id = NULL`).

---

## 3. Endpoint `POST /jobs/batch`

New router `app/api/v1/batch.py`, registered in `app/api/v1/__init__.py` under `Depends(get_current_user)` like the others.

**Body:** `{ book_id: UUID, toc_entry_ids?: list[UUID] (default = all of the book's lessons), provider?: str, model?: str, force?: bool = False }`

**Validation / readiness guard** (the launcher's precondition — uses the REAL statuses, never the test-seed fake `"ready"`):
1. Book exists (else 404).
2. `book.status == "toc_ready"` → proceed. `book.status in ("uploading","toc_extracting")` → **409** "book still extracting — lessons available once TOC extraction completes." `book.status == "failed"` → **409** + surface `book.error_message`.
3. `toc_entries_repo.list_for_book(book_id)` non-empty (defensive even on `toc_ready`) — else **422** "no lessons found for this book."
4. If `toc_entry_ids` given: every id must belong to this book (else **422**).
5. **Provider/model defaults + validation.** `provider` defaults to **`claude`** when omitted — the fleet's cli-first provider (master §1a); a deliberate divergence from `/generate`'s `gemini` default, justified by the fleet's cli-first intent. `model` defaults to **`None`** (→ provider default via `_resolve_model`, which only `claude` carries a default for). Validate via `agent_models.is_valid(provider, model)` (else **400**) — **verified:** `is_valid("claude", None)` is `True` (model=None is the provider-default when the manifest list is non-empty, `agent_models.py:43`), and `is_valid(provider=None, …)` is `False`, so provider is never null.

**Reconcile (find-or-create-per-book + adopt) — runs in ONE atomic transaction** (so the derived denominator in §4 can never see a partial-enqueue state):
1. **Find-or-create THE batch for this book**, race-safe via `INSERT INTO batches (...) ON CONFLICT (book_id) DO UPDATE …` (the `UNIQUE(book_id)` guarantees one row even under two concurrent launches). **No stored lesson count to maintain** — the batch's coverage is simply whichever jobs carry its `batch_id` (derived on read, §4).
2. **Per requested lesson**, reusing the existing idempotency (`jobs_repo.lock_section_for_generate` advisory lock `jobs.py:73` + `find_active_for_section` `jobs.py:51`, which dedups on `pending`/`running`/`done` only — so a `failed`/`cancelled` lesson correctly yields a fresh job):
   - active job exists & `batch_id IS NULL` → **adopt** (set `batch_id` = this batch). *No poaching:* never re-tag a job already owned by a batch — under `UNIQUE(book_id)` that can only be THIS batch, so it's a skip.
   - active job exists & `batch_id` = this batch → **skip**.
   - no active job (new / failed / cancelled), or `force=True` → **create** a `pending` job via `jobs_repo.create(... batch_id=batch.id, provider=…, model=…)`.

**Response:** `{ batch_id, jobs_created, jobs_adopted, jobs_skipped, rollup }` (rollup snapshot per §4; `jobs_created + jobs_adopted + jobs_skipped` = the lessons this call covered).

**Why find-or-create-per-book (not a new row per call):** a "new batch per POST" mints a 0/N ghost when you re-launch a fully-covered book (every lesson skipped, nothing to adopt). One-batch-per-book makes re-launch a clean top-up (re-enqueue only the failed/missing lessons into the same batch) and makes the "probe 2–3 lessons, then launch all" flow roll the probe lessons into the full effort instead of stranding them.

---

## 4. Reads & the rollup (drift-free, per-lesson-latest)

**The rollup must be computed over the latest job per lesson, scoped to the batch — NOT over all jobs.** A top-up/`force` retry leaves the OLD `failed` job *and* a NEW `pending` job both carrying `batch_id=X`; a naive `GROUP BY status WHERE batch_id=X` would count both and exceed the lesson count (a 50-lesson batch with 5 retries → 55 rows). 

**Pattern (direct precedent: `latest_by_section`, `jobs.py:181`, which does exactly this scoped to `book_id` via `.distinct(toc_entry_id)` `:193`):** `DISTINCT ON (toc_entry_id) … WHERE batch_id = X ORDER BY toc_entry_id, created_at DESC` → one row per lesson (its latest job) → then tally by status. **The denominator is this set's size (= sum of the tally), computed on read — not stored.** The tally is internally consistent by construction (one row per lesson) regardless of retries, top-ups, or `force`.

- `GET /jobs/batches/{id}` → batch facts + the per-lesson-latest tally `{pending, running, done, failed, cancelled, cancelling}` + derived `complete` (no `pending`/`running`/`cancelling` remain) + the denominator (= sum of the tally = distinct lessons covered).
- `GET /jobs/batches` → list of batches, each with the same computed tally (most-recent first).

---

## 5. Testing strategy

DB-free unit tests can't catch the SQL that matters here (DISTINCT-ON rollup, `ON CONFLICT`, the guard), so the proof is **guarded real-DB integration tests** (`RUN_DB_INTEGRATION=1` + throwaway Postgres, the Phase-0/0.5 pattern), plus DB-free unit tests for the pure pieces (request validation, guard-state→HTTP mapping).

Key real-DB cases:
1. **Happy fan-out:** `toc_ready` book, launch all → one `pending` job per lesson, `batch_id` set, the derived denominator = lesson count, rollup = `{pending: N}`.
2. **Readiness guard:** `uploading`/`toc_extracting` → 409 retry-message; `failed` → 409 surfacing `error_message`; `toc_ready` but zero `toc_entries` → 422.
3. **Subset:** `toc_entry_ids` of 3 → exactly 3 jobs, derived denominator = 3; an id not belonging to the book → 422.
4. **Idempotent re-launch (the reconciliation proof):** launch all (50) → mark 5 jobs `failed` → re-launch all → **same batch** (`UNIQUE(book_id)`), 5 new jobs created, and the **per-lesson-latest rollup returns exactly 50 rows** (not 55) — the 5 retried lessons show their latest (`pending`) job, not both; the derived denominator stays 50.
5. **Adopt orphan:** a lesson pre-run via single `/generate` (`batch_id NULL`, `done`) → launch all → that job is adopted (`batch_id` set), the rollup counts it once, and the denominator (distinct lessons covered) includes it.
6. **Concurrent-launch race:** two simultaneous "launch all" for one book → exactly **one** `batches` row (no ghost), no duplicate jobs (advisory lock + `ON CONFLICT`).
7. **`force=True`:** regenerates fresh jobs for targeted lessons; rollup still per-lesson-latest, still reconciles.

**Plan hygiene pre-task (first task in the plan):** change the fake `status="ready"` → `status="toc_ready"` in `tests/integration/test_claim_contention.py:43` and `tests/integration/test_clock_skew.py:34` so the made-up value stops being a copy-paste hazard for the readiness guard. (`"ready"` is not a real book status — app code uses `uploading`/`toc_extracting`/`toc_ready`/`failed` exclusively.)

---

## 6. Acceptance gate

A guarded real-DB integration run that exercises cases 1–7 above green, **plus** a container/process check that a seeded batch's jobs are actually pulled by a worker (reuse the Phase-0 two-worker pattern: launch a small batch → workers claim every lesson → `attempts>0`; jobs fail without a CLI in the image, which is fine — the proof is that the batch's jobs entered the shared queue and were drained). DB-free suite stays at baseline.

---

## 7. File map (for the plan)

- Create: `app/models/batch.py` (`Batch` model, `UNIQUE(book_id)`), register in `app/models/__init__.py`.
- Modify: `app/models/homework_job.py` (+ `batch_id` nullable FK + index).
- Create: `alembic/versions/0023_batches.py` (table + column + index + FK; down_revision `d5e9f1a2b3c4`).
- Create: `app/repositories/batches.py` (`get_or_create_for_book` via `ON CONFLICT`, `rollup_for_batch` via DISTINCT-ON, `list_with_rollups`). The launch reconcile (find-or-create + create/adopt all targeted jobs) is one atomic transaction in the endpoint/service.
- Modify: `app/repositories/jobs.py` (`create` accepts `batch_id`; maybe a `latest_by_section`-style helper scoped to `batch_id` — or put it in `batches.py`).
- Create: `app/api/v1/batch.py` (`POST /jobs/batch`, `GET /jobs/batches`, `GET /jobs/batches/{id}`), register in `app/api/v1/__init__.py`.
- Create: `tests/integration/test_batches.py` (cases 1–7) + a DB-free unit test for request/guard validation.
- Modify (hygiene): the 2 test seeds (`ready`→`toc_ready`).
- Edit: `docs/superpowers/specs/2026-06-06-autonomous-fleet-design.md` §5 (narrow to fan-out-only, link here).

---

## 8. Locked decisions (provenance)

1. **Fan-out only** (not full-auto / not hybrid) — batch sees only `toc_ready` books; reuse the existing fetch/ingest/extract + its first-class failure handling.
2. **Subset supported**, default all (`toc_entry_ids?`) — cheap, enables probe-before-50 cost control.
3. **Fully counter-free** — *no* stored count at all (this supersedes both the original `total_jobs` and the interim `lessons_targeted` field). The denominator is **derived on read** = distinct `toc_entry_id`s with `batch_id=X` (= sum of the per-lesson-latest tally). Adoption guarantees every targeted lesson gets a `batch_id` job and the launch is atomic, so derived == requested with nothing to drift.
4. **Adopt orphans** (`batch_id IS NULL` only, no poaching) — paired with the derived denominator, the only model where the rollup reconciles (every targeted lesson has a `batch_id` job).
5. **Find-or-create-per-book**, race-safe via `UNIQUE(book_id)` + `ON CONFLICT` — kills re-launch ghosts; one logical batch per textbook; guarantees adoption is always clean.
6. **Per-lesson-latest rollup** via the `latest_by_section` DISTINCT-ON pattern scoped to `batch_id` — one row per lesson keeps the tally (and the derived denominator) internally consistent under retries/top-ups/`force`.
7. **`provider`/`model` are per-job authoritative**; the batch row carries only a launch-default label.
