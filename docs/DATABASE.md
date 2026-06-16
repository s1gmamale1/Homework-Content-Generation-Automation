# Database & Engine Infrastructure — The Deep Reference

> The complete, verified reference for the Postgres schema, the queue semantics, and the
> fleet layer. `HOW_IT_WORKS.md` is the plain-English tour; this is the precise map.
> Every claim here was re-verified against branch `Nggaev-v2`, head `a8c7e6d5f4b3`
> (0026), 2026-06-16. When this doc and the code disagree, the code wins — fix the doc.

---

## 1. Topology

One **Postgres head** serves everything: application data, the job queue, the worker
registry, and usage accounting. There is no Redis, no message broker — the queue *is*
Postgres (`SELECT … FOR UPDATE SKIP LOCKED`), which means one fewer moving part and
transactional consistency between "claim a job" and "see its data."

| Environment | Container | Port | Database |
|---|---|---|---|
| Local dev | `edu-postgres` | **5433** (host has its own PG on 5432) | `edu_homework` |
| Fleet test env | `fleet-pg` (throwaway) | **5436** | `edu_copy` (clone of dev) |
| Guarded integration tests | any throwaway | 5436/5437 | migrated to head first |

**Engine / session config** (`app/db.py`):
- `create_async_engine` (asyncpg) with `pool_size=20`, `max_overflow=30`,
  `pool_pre_ping=True` (revalidates checked-out connections), `pool_recycle=1800`.
- `SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)` —
  `expire_on_commit=False` is load-bearing: response payloads are built from ORM objects
  *after* `commit()`, which would otherwise raise in async contexts.

**Migrations**: Alembic, applied with `uv run alembic upgrade head` (the Docker entrypoint
also runs it on deploy). Current head: **`a8c7e6d5f4b3`** (`0026_drop_difficulty`). Full chain in §7.

---

## 2. The two clocks — read this before touching queue code

The single most important discipline in this schema, born from a real bug (worklog 0049):

- **DB clock (`func.now()`)** — used for everything that *orders or compares* time across
  processes: `scheduled_at` filtering and stamping in the claim query, claim-lease TTLs,
  retry-backoff scheduling, worker heartbeats, liveness evaluation. All of these compare
  DB-written timestamps against the **same** DB clock, so they are immune to host↔DB clock
  skew (Docker Desktop / WSL2 drifts ±0.7 s, which once made a freshly created job look
  "scheduled in the future" and flake the claim).
- **Host clock (`datetime.now(timezone.utc)`)** — allowed only for *record-only* terminal
  stamps that nothing compares across processes: `completed_at` on done/failed/cancelled.

**Rule:** if a timestamp is ever compared to another timestamp in a WHERE clause, both
sides must come from `func.now()`. SQLAlchemy gotcha: `func.make_interval(secs=N)` raises
`TypeError` — use the positional form `func.make_interval(0, 0, 0, 0, 0, 0, N)`.

---

## 3. Schema — table by table

Seven application tables (plus `alembic_version`). Mixins from `app/models/base.py`:

- **`UUIDPK`** — `id UUID` PK, `default=uuid4` (**Python-side**).
- **`Timestamps`** — `created_at` / `updated_at`, `default=_utcnow` (**Python-side**),
  `updated_at` also `onupdate=_utcnow`.

> ⚠️ **The Core-insert gotcha:** because those defaults are Python-side (not
> `server_default`), a SQLAlchemy **Core** insert (e.g. `pg_insert(...).on_conflict_do_update`)
> does **not** fire them — you hit NOT-NULL/PK violations unless you supply `id=uuid4()`,
> `created_at=func.now()`, `updated_at=func.now()` explicitly. `batches_repo.get_or_create_for_book`
> is the in-repo example of doing this correctly.

### 3.1 `books` — one row per uploaded PDF

| Column | Type | Notes |
|---|---|---|
| `id`, `created_at`, `updated_at` | mixins | |
| `subject` | String(64) NOT NULL | one of `flows.SUBJECTS` |
| `grade` | String(32) NULL | e.g. `"9-sinf"` |
| `original_filename` | String(512) NOT NULL | |
| `content_sha256` | String(64) NOT NULL | upload de-dup key; `ix_books_content_sha256` |
| `file_size_bytes` | BigInteger NOT NULL | |
| `gemini_file_uri`, `gemini_file_expires_at`, `gemini_cache_name`, `gemini_cache_expires_at` | NULL | **legacy/unused** — kept nullable for backwards-compat from the removed Gemini SDK era |
| `status` | String(32) NOT NULL | lifecycle: `uploading → toc_extracting → toc_ready \| failed` — note there is **no** `"ready"` status |
| `error_message` | Text NULL | set when `failed` |

The PDF itself lives **on disk**, not in the DB: `var/books/<book_id>/source.pdf`
(deterministic path; never delete after TOC extraction — every phase re-reads it).
Relationship: `toc_entries` (cascade delete-orphan, ordered by `order_index`).

### 3.2 `toc_entries` — one row per chapter section (a "lesson")

| Column | Type | Notes |
|---|---|---|
| `id`, `created_at`, `updated_at` | mixins | |
| `book_id` | FK → books **ondelete=CASCADE** NOT NULL | |
| `chapter_number` / `chapter_title` | String(32) / Text, NULL | |
| `section_number` | String(32) NULL | nullable since migration 0011 |
| `section_title` | Text NOT NULL | |
| `page_start` / `page_end` | Integer NULL | |
| `order_index` | Integer NOT NULL | display + drill-in sort key; `ix_toc_entries_book_id_order (book_id, order_index)` |
| `notion_homework_page_id` | String(128) NULL | set when the homework was archived to Notion |

### 3.3 `homework_jobs` — one row per generation request (also the queue)

| Column | Type | Notes |
|---|---|---|
| `id`, `created_at`, `updated_at` | mixins | |
| `book_id` / `toc_entry_id` | FKs NOT NULL | `ix_homework_jobs_book_toc (book_id, toc_entry_id)` |
| `subject` | String(64) NOT NULL | denormalized from book |
| `status` | String(32) NOT NULL | `pending → running → done \| failed \| cancelling → cancelled`; `ix_homework_jobs_status` |
| `provider` | String(32) NOT NULL, server_default `'gemini'` | the user's pick; honored by every phase except the extract pin |
| `model` | String(128) NULL | NULL = provider default (`_resolve_model`) |
| `batch_id` | FK → batches NULL | fleet membership (migration 0023); `ix_homework_jobs_batch_id` |
| `transport` | String(16) NOT NULL, server_default `'cli'` | Phase 4 (migration 0024): `cli` (subscription CLI auth, $0 marginal) vs `api` (pay-per-token keys); validation requires api ⇒ provider ∈ {claude, gemini} + explicit model |
| `extract_transport` / `judge_transport` | String(16) NOT NULL, server_default `'inherit'` | Phase 4.1 (migration 0025): per-role billing override, `cli \| api \| inherit`; `inherit` follows `transport` (`resolve_role_transport`) |
| `current_phase` | String(64) NULL | live progress marker |
| `error_message` | Text NULL | |
| `started_at` / `completed_at` | NULL | `completed_at` is host-clock (record-only, see §2) |
| `notion_archived_at` / `notion_skip_reason` | NULL | archive bookkeeping |

**Queue bookkeeping columns** (migration 0009):

| Column | Type | Notes |
|---|---|---|
| `priority` | Integer NOT NULL, server_default `0` | higher first |
| `scheduled_at` | NOT NULL, **server_default `NOW()`** (DB clock) | claim eligibility + backoff re-scheduling |
| `claimed_at` / `claimed_by` | NULL | lease timestamp (DB clock) + `"hostname:pid"` of the worker |
| `attempts` | Integer NOT NULL, server_default `0` | incremented on every claim |
| `last_attempt_at` / `last_error` | NULL | |

**Partial queue index** — the claim query's index:
`ix_homework_jobs_queue (scheduled_at, priority DESC) WHERE status = 'pending'`.

### 3.4 `phase_outputs` — one row per phase per job (the deliverable)

UUIDPK only — **no** `created_at`/`updated_at`; it has `started_at`/`completed_at` instead.

| Column | Type | Notes |
|---|---|---|
| `job_id` | FK → homework_jobs **ondelete=CASCADE** NOT NULL | |
| `phase_name` / `phase_order` | String(64) / Integer NOT NULL | |
| `prompt_hash` | String(64) NOT NULL | keys the cross-job extract cache |
| `model_name` | String(128) NOT NULL | |
| `provider` | String(32) NULL | who actually produced it (failover may differ from the job's provider; migration 0019) |
| `output_md` | Text NULL | **the deliverable** — per-phase markdown; there is no assembly step and no structured-JSON columns (dropped in 0018) |
| `tokens_input` / `tokens_output` | Integer NULL | |
| `status` | String(32) NOT NULL | pending/running/done/failed |
| `error_message` | Text NULL | |
| `validation_warnings` | JSONB NULL | LLM-judge warnings (migration 0017) |
| `started_at` / `completed_at` | NULL | |

**`uq_phase_output_job_order (job_id, phase_order)`** — and the reason the pipeline must use
`phase_repo.create_or_reset`, never raw `create`: the startup sweep only marks stale phase
rows `failed`, it doesn't delete them, so a retried job re-INSERTing would trip this constraint.

### 3.5 `agent_usages` — one row per CLI subprocess call

| Column | Type | Notes |
|---|---|---|
| `id`, `created_at`, `updated_at` | mixins | `ix_agent_usages_created_at_desc` |
| `book_id` / `homework_job_id` / `phase_output_id` | FKs, all **ondelete=SET NULL** | usage history survives cleanup; each indexed |
| `provider` | String(32) NOT NULL, server_default `'gemini'` | indexed; cached-extract reuse rows use the sentinel `"<cache>"` |
| `auth_mode` | String(8) NOT NULL, server_default `'cli'` | Phase 4 (migration 0024): the transport the spawn ACTUALLY used; prices the per-transport `$` rollup (`<cache>` rows stay `cli` — free markers) |
| `operation` | String(64) NOT NULL | e.g. `lesson.extract`, `phase.boss-arena`, `judge.<phase>`; indexed |
| `model_name` | String(128) NULL | |
| `prompt_tokens` / `output_tokens` / `cached_tokens` / `total_tokens` | Integer NOT NULL default 0 | normalized across providers; **kimi reports 0s** (stream-json gap); `<cache>` rows are all-zero so $-math never double-counts |
| `raw_envelope` | JSONB NULL | full provider envelope for forensics |
| `duration` | String(50) NULL | |
| `success` | Boolean NOT NULL default true | |
| `error_message`, `started_at`, `completed_at` | NULL | |

Read by the end-of-job token table, `GET /agent/stats` (rolling 1h/24h/7d windows vs
`AGENT_LIMIT_<PROVIDER>_<WINDOW>` caps), and the `/usage` dashboard. These are **local
consumption counts**, not provider quotas.

### 3.6 `batches` — one row per fleet batch (Phase 2)

| Column | Type | Notes |
|---|---|---|
| `id`, `created_at`, `updated_at` | mixins | |
| `book_id` | FK → books NOT NULL, part of **UNIQUE (`uq_batches_book_id_transport`)** | one batch per `(book, transport)` since migration 0024 — a different-transport re-launch forks a new batch (the cli-vs-api benchmark, spec §9); same-transport reuses |
| `transport` | String(16) NOT NULL, server_default `'cli'` | launch-time transport (also on every member job) |
| `extract_transport` / `judge_transport` | String(16) NOT NULL, server_default `'inherit'` | Phase 4.1 launch-default labels stamped onto created jobs — **jobs carry the truth**; on re-launch these labels can go stale |
| `subject` / `grade` | NOT NULL / NULL | denormalized for display |
| `provider` / `model` | NOT NULL / NULL | the launch-time pick |
| `notion_source` | String(512) NULL | |

Design: **no stored counters**. Progress is computed on read (`rollup_for_batch`):
`DISTINCT ON (toc_entry_id) … ORDER BY toc_entry_id, created_at DESC` scoped to the batch,
then `GROUP BY status` — one vote per lesson (its newest job), so retries/top-ups can never
inflate the tally, and the denominator is `sum(tally.values())`. `UNIQUE(book_id)` makes
find-or-create race-safe via `ON CONFLICT DO UPDATE … RETURNING` (Core insert — see the
mixin gotcha in §3). Launch semantics: fan-out one job per lesson; an existing *active*
job (pending/running/done) is skipped, and an orphan (`batch_id IS NULL`) is **adopted**.

### 3.7 `workers` — the fleet registry (Phase 1)

No mixins — tiny by design:

| Column | Type | Notes |
|---|---|---|
| `pc_id` | String(128) **PK** | `"hostname:pid"` |
| `last_heartbeat` | NOT NULL | always stamped with `func.now()` (DB clock) |
| `status` | String(32) NOT NULL, server_default `'online'` | ⚠️ informational label — `claim_next_job` does **not** check it ("draining" is not enforced yet) |
| `notes` | Text NULL | |

**Liveness is derived, never stored**: `GET /workers` fetches `SELECT now()` and computes
`online = last_heartbeat >= db_now - worker_registry_stale_seconds` (default 90 s = 3 missed
30-s beats). Both sides of that comparison are DB-clock — see §2.

---

## 4. Queue semantics (`app/repositories/jobs.py`)

The job status state machine:

```
                    ┌──────────── retry endpoint / backoff ───────────┐
                    ▼                                                 │
  INSERT ──▶ pending ──claim──▶ running ──▶ done                      │
                    ▲              │  └───▶ failed (attempts exhausted)┘
                    │              ├───▶ cancelling ──▶ cancelled
       orphan reclaim└─────────────┘        (or straight pending ▶ cancelled)
```

- **`claim_next_job`** — the heart. `WHERE status='pending' AND scheduled_at <= func.now()
  AND attempts < max_attempts`, `ORDER BY priority DESC, scheduled_at ASC`,
  **`FOR UPDATE SKIP LOCKED`** (concurrent workers can never grab the same row — held under
  contention by a dedicated integration test). Stamps `status='running'`,
  `claimed_at/last_attempt_at/started_at = func.now()`, `claimed_by=worker_id`, `attempts += 1`.
- **`touch_claim`** — the per-job heartbeat: refreshes `claimed_at = func.now()` every
  `heartbeat_seconds` (30 s) while running; no-ops once the job leaves `running`.
- **`reclaim_stuck_jobs`** — orphan recovery: any `running` row whose `claimed_at` is older
  than `reclaim_stale_seconds` (120 s = 4 missed beats) goes back to `pending` (attempts
  preserved, so a poison job still exhausts its budget). Runs periodically in the worker
  and **once at API startup with `stale_after_seconds=0`** — that startup sweep resets *all*
  running jobs and is explicitly **single-host only** (`main.py:50-52`); a restarting pod in a
  multi-pod fleet would steal a live peer's job. Fleet workers rely on the TTL-based sweep.
- **`mark_failed_with_retry`** — on failure: if `attempts >= queue_max_attempts` (3) →
  terminal `failed`; else → back to `pending` with exponential backoff
  `scheduled_at = func.now() + 30s × 2^(attempts-1)` (30s, 60s, 120s…).
- **`reclaim_stale_cancelling`** — a `cancelling` row whose worker died is finalized to
  `cancelled` after the same TTL.
- **`queue_depth`** — counts immediately-claimable pending rows; `POST /generate` returns
  **503** when it exceeds `queue_backpressure_limit` (50).
- **`lock_section_for_generate`** — `pg_advisory_xact_lock` keyed by
  `blake2b("generate:{book_id}:{toc_entry_id}", digest_size=8)` as a signed int8;
  transaction-scoped, serializes concurrent generate/batch-launch for the same section.
- **`find_active_for_section`** — newest job with `status IN (pending, running, done)` for a
  section; the idempotency read behind both `/generate` and batch fan-out.
- **`latest_by_section`** / `batches.rollup_for_batch` / `batches.list_jobs` — the
  per-lesson-latest `DISTINCT ON (toc_entry_id)` family; all three share the same set
  definition so the funnel, drill-in, and section views can never disagree.

**Generate idempotency is three layers**: an `Idempotency-Key` header cache → the
natural-key check (`find_active_for_section`, unless `force=true`) → the advisory lock to
serialize the race between the first two.

---

## 5. The worker (`app/services/worker.py`)

One process, identified as `pc_id = "hostname:pid"`, running:

1. **The main loop** — acquire one of `worker_concurrency` slots (semaphore, default 4) →
   `claim_next_job` → dispatch `_execute_job` as a background task (slot released in its
   `finally`). Empty queue → wait `worker_poll_interval` (2 s) on the stop event.
2. **Per-job heartbeat task** — every 30 s: `touch_claim` + checks the job's status for a
   cross-process **cancel** (`cancelling` set by the API → the task cancels itself locally;
   same-process cancels are instant via the `RUNNING_JOBS` dict).
3. **Registry heartbeat task** (dedicated, *not* in the main loop — a main-loop beat would
   starve while all slots are busy with long jobs and false-offline the worker) — every
   30 s: `workers_repo.upsert_heartbeat` (ON CONFLICT upsert, `func.now()`).
4. **Stuck-job sweep** — periodic `reclaim_stuck_jobs` (TTL-based).

Per-job: `asyncio.wait_for(pipeline.run(job_id), timeout=job_timeout_seconds)` (1800 s).
Timeout/failure → `mark_failed_with_retry`. User cancel → `mark_cancelled` inside
`asyncio.shield` (so a shutdown can't half-finalize it). Shutdown → `stop()` then drain
in-flight tasks (lifespan waits up to 30 s, then force-cancels).

**Two deployment shapes:** embedded in the API process (default; `WORKER_CONCURRENCY=0`
disables) or standalone `python -m app.services.worker` per fleet PC (loads prompts,
installs SIGTERM/SIGINT handlers). All workers point at the same head DB; whole-job
work-stealing, no host affinity.

**Subprocess concurrency:** independent of slots, a process-wide semaphore caps concurrent
CLI subprocesses. ⚠️ The live semaphore reads **`gemini_max_concurrency`** (default 8,
`agent.py:203`) — `agent_max_concurrency` exists in config but is **dead**; tune
`GEMINI_MAX_CONCURRENCY` until the rename lands.

---

## 6. Engine layers above the queue (pointers)

- **Pipeline** (`app/services/pipeline.py`) — head: pinned extract
  (`extract_provider`/`extract_model` = gemini/gemini-2.5-flash) with local-text gates,
  failover (`failover_provider_order = [codex, gemini, kimi, opencode]` — claude
  deliberately absent to protect the Max pool), and cross-job cache reuse keyed on
  `(toc_entry_id, prompt_hash)` (a free `"<cache>"` usage row records the hit). Tail:
  wave scheduler over `flows.PHASE_DEPS`, `create_or_reset` per phase row, LLM judge
  (`judge_provider/judge_model` = claude/claude-opus-4-7) grading every content phase —
  one regeneration on a MAJOR verdict. Job `done` → fire-and-forget Notion archive.
- **Provider router** (`app/services/agent.py` + `providers/`) — argv build, spawn,
  envelope parse, one `agent_usages` row per call. See `HOW_IT_WORKS.md` §8.
- **API surface** — `/api/v1`: books (incl. `POST /books/from-notion`), jobs
  (generate/retry/cancel/stream/download), **batch** (`POST /jobs/batch`,
  `GET /jobs/batches[/{id}][/jobs]`), **workers** (`GET /workers`), notion, health.
  Router order matters: `batch` is registered **before** `jobs` so static `/jobs/batches*`
  isn't swallowed by dynamic `/jobs/{job_id}` (a real 422 bug, fixed `5a7e28d`).

---

## 7. Migration chain

| # | File | Revision | Adds |
|---|---|---|---|
| 1 | 0001_initial | `22c7641cf558` | books, toc_entries, homework_jobs, phase_outputs |
| 2 | 0002_content_type | `59a042ea789f` | |
| 3 | 0003_gemini_usages | `f42a38524dfd` | agent_usages (née gemini_usages) |
| 4 | 0004_gemini_usage_modalities | `92e8c4d10aa1` | |
| 5 | 0005_book_gemini_cache | `7b3091fa44c2` | legacy gemini cache cols |
| 6 | 0006_homework_games_json | `a4e21cf08b73` | (structured cols, later dropped) |
| 7 | 0007_homework_flashcards_json | `c8d54a7f912b` | |
| 8 | 0008_homework_more_structured | `e4a87cd16f02` | |
| 9 | 0009_homework_queue_columns | `a3f5e2d18c44` | priority/scheduled_at/claimed_*/attempts |
| 10 | 0010_provider_agnostic_usage | `b71d3a4f6c20` | provider columns |
| 11 | 0011_toc_section_number_nullable | `c8e1d4b27a91` | |
| 12 | 0012_homework_source_map_json | `e3a7c1d9b4f2` | |
| 13 | 0013_homework_boss_arena_json | `f4b8d2e6a9c1` | |
| 14 | 0014_homework_learning_sections | `a1c7e9d3b5f8` | |
| 15 | 0015_homework_practice_arc | `b6d2f8a4c3e9` | |
| 16 | 0016_notion_archive | `c9e3f1a07b62` | notion_archived_at + toc page id |
| 17 | 0017_phase_validation_warnings | `d1f4a9b3c7e2` | phase_outputs.validation_warnings |
| 18 | 0018_drop_structured_columns | `e2a5b8c4f1d9` | **drops** the per-phase JSON columns (md-per-phase flip) |
| 19 | 0019_phase_provider | `a7c1e9d2b4f8` | phase_outputs.provider |
| 20 | 0020_backfill_book_grade | `b3f6a1c2d4e5` | |
| 21 | 0021_notion_skip_reason | `c4a7b2d3e6f0` | |
| 22 | 0022_workers_registry | `d5e9f1a2b3c4` | **workers** table (fleet Phase 1) |
| 23 | 0023_batches | `a1b2c3d4e5f6` | **batches** table + homework_jobs.batch_id (fleet Phase 2) |
| 24 | 0024_transport_auth_mode | `f7e6d5c4b3a2` | `transport` (jobs+batches) + `agent_usages.auth_mode` + batch key → `(book_id, transport)` (fleet Phase 4) |
| 25 | 0025_role_transports | `b9d8e7f6a5c4` | `extract_transport`/`judge_transport` (jobs+batches) (fleet Phase 4.1) |
| 26 | 0026_drop_difficulty | `a8c7e6d5f4b3` | drops the dead `homework_jobs.difficulty` column — **HEAD** |

---

## 8. Config quick-reference (queue/engine knobs, `app/config.py`)

| Setting | Default | Means |
|---|---|---|
| `worker_concurrency` | 4 | job slots per worker process (0 = no embedded worker) |
| `worker_poll_interval` | 2.0 s | empty-queue poll |
| `heartbeat_seconds` | 30 | per-job `touch_claim` + registry beat cadence |
| `reclaim_stale_seconds` | 120 | running-job lease TTL (4 missed beats) |
| `worker_registry_stale_seconds` | 90 | worker online/offline threshold (3 missed beats) |
| `job_timeout_seconds` | 1800 | whole-job kill switch |
| `per_attempt_timeout_seconds` | 600 | one failover attempt (one provider try) |
| `queue_max_attempts` | 3 | claim attempts before terminal `failed` |
| `queue_backpressure_limit` | 50 | pending depth → `/generate` 503 (0 disables) |
| `gemini_max_concurrency` | 8 | **the live** process-wide CLI subprocess cap (`agent_max_concurrency` is dead) |
| `extract_provider` / `extract_model` | gemini / gemini-2.5-flash | the extract pin |
| `judge_provider` / `judge_model` | claude / claude-opus-4-7 | the LLM judge |
| `failover_provider_order` | codex, gemini, kimi, opencode | per-phase failover (no claude) |
| `agent_limit_<provider>_<1h\|24h\|7d>` | per provider | local call-count caps for `/usage` bars |

---

## 9. Operational notes

- **Inspect dev DB:** `docker exec edu-postgres psql -U edu -d edu_homework -c "<sql>"`.
- **Startup sweep caveat (multi-pod):** the API's `reclaim_stuck_jobs(stale_after_seconds=0)`
  at startup assumes it owns all running jobs. Safe single-host; in a fleet, rely on the
  TTL sweep instead (a restarting head would otherwise reset live workers' jobs).
- **`workers.status` is unenforced** — pausing/draining a PC needs worker-loop support
  (deferred; `claim_next_job` ignores it today).
- **Books table fake-status trap:** tests once seeded `status="ready"`, which is not a real
  status — batch readiness keys on `toc_ready`.
- **Don't `git add -A`** — `var/` holds copyrighted PDFs and `.env` holds secrets; both are
  gitignored, but stage explicitly anyway.
