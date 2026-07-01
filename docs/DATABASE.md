# Database & Engine Infrastructure — The Deep Reference

> The complete, verified reference for the Postgres schema, the queue semantics, and the
> fleet layer. `HOW_IT_WORKS.md` is the plain-English tour; this is the precise map.
> Last updated: branch `feat/sa-key-web-distribution`, head `0041_sa_keys`
> (0041), 2026-06-30. When this doc and the code disagree, the code wins — fix the doc.

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
also runs it on deploy). Current head: **`0041_sa_keys`** (0028 = enum CHECK constraints,
0029 = `phase_outputs.judge_status`, 0030 = `agent_usages.cache_creation_tokens`,
0031 = `batches.paused_at`/`paused_reason`, 0032 = `budget_state` singleton,
0033 = `custom_prompts`/`selected_phases` JSONB on `homework_jobs`+`batches`,
0034 = widen `phase_outputs.prompt_hash` 64→128 for `custom:sha256:<hex>` provenance,
0035 = `workers.capabilities` JSONB, 0036 = `batches.session_limit_strategy` + CHECK,
0037 = `launch_defaults` singleton + seed + NULL-column backfill on `homework_jobs`,
0038 = `output_language` on `homework_jobs`+`batches`+`launch_defaults` + batch UNIQUE swap,
0039 = `content_provider`/`content_model`/`content_transport` on `launch_defaults`,
0040 = `books.source_language` String(8) NOT NULL server_default `'uz'` + CHECK `uz|ru|en`,
0041 = `sa_keys` table + `sa_key_assignments` table).
Full chain in §7. (Revision IDs stay ≤32 chars — `alembic_version.version_num` is VARCHAR(32).)

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
| `source_language` | String(8) NOT NULL, server_default `'uz'` | migration 0040: the language of the source textbook — `uz` (Uzbek), `ru` (Russian), `en` (English); **DB CHECK `uz\|ru\|en`**. Set at ingest time (upload form, Notion fetch language picker, or `book_from_notion` `language` field). Controls the Notion fetch tree (`uz` → `N - sinf`, `ru` → `N - класс`/`klass`, `en` → named english/inglizcha/ingliz container); also the default `output_language` for jobs launched over this book (overridable = translation mode). Dedup stays `(content_sha256, subject)` — a different-language PDF for the same subject is a distinct book. |
| `toc_validation` | String(16) NULL | migration 0042: post-extract vision-validator verdict — `NULL` (validator disabled or not yet run), `verified` (TOC matches the book's printed contents page), `mismatch` (vision model flagged a discrepancy → book held in `toc_review`), `skipped` (validator ran but skipped — no usable page window, spawn error, or parse failure; treated as `verified` for generation). **DB CHECK `NULL \| verified \| mismatch \| skipped`**. |
| `toc_validation_detail` | Text NULL | migration 0042: human-readable explanation from the vision call (issues list when `mismatch`, confirmation when `verified`, reason when `skipped`). Preserved even after operator accept (audit trail). |
| `status` | String(32) NOT NULL | lifecycle: `uploading → toc_extracting → toc_ready \| toc_review \| failed`. `toc_review` means the vision validator flagged a mismatch — TOC entries are persisted but generation is blocked until an operator accepts or retries. Note there is **no** `"ready"` status. |
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
| `status` | String(32) NOT NULL | `pending → running → done \| failed \| cancelling → cancelled`; `ix_homework_jobs_status`; **DB CHECK constraint** (migration 0028) restricts to this set |
| `provider` | String(32) NOT NULL, server_default `'gemini'` | the user's pick; honored by every phase except the extract pin |
| `model` | String(128) NULL | NULL = provider default (`_resolve_model`) |
| `batch_id` | FK → batches NULL | fleet membership (migration 0023); `ix_homework_jobs_batch_id` |
| `transport` | String(16) NOT NULL, server_default `'cli'` | Phase 4 (migration 0024): `cli` (subscription CLI auth, $0 marginal) vs `api` (pay-per-token keys); validation requires api ⇒ provider ∈ {claude, gemini} + explicit model; **DB CHECK `cli\|api`** (migration 0028) |
| `extract_transport` / `judge_transport` | String(16) NOT NULL, server_default `'inherit'` | Phase 4.1 (migration 0025): per-role billing override, `cli \| api \| inherit`; `inherit` follows `transport` (`resolve_role_transport`); **DB CHECK `cli\|api\|inherit`** (migration 0028) |
| `extract_provider` / `extract_model` / `judge_provider` / `judge_model` | String(32 / 128 / 32 / 128) NULL | migration 0027: per-role provider/model; stamped at launch from an explicit pick or the `launch_defaults` global default (migration 0037 backfilled existing NULLs with the seed values) |
| `output_language` | String(16) NOT NULL, server_default `'uz'` | migration 0038: medium of instruction for generated content — `uz` (Uzbek), `en` (English), `ru` (Russian); **DB CHECK `uz\|en\|ru`**; resolved at launch from the per-launch override or the `launch_defaults.output_language` global default; threaded by `pipeline.run` to `get_prompt` (generator) and `phase_judge.judge` (judge) so both always use the same-language contract; extract is language-neutral (untouched). L2 language-class subjects (English/Russian class `subjects.language ∈ {english,russian}`) always use their Uzbek-bridged L2 rule regardless of this column. Default `'uz'` is byte-identical to pre-0038 behavior. |
| `custom_prompts` | JSONB NULL | migration 0033 (PR37): `{phase: markdown}` per-phase prompt overrides replacing the built-in contract; NULL = built-in for all phases. Also seen by the judge as `contract_override`. |
| `selected_phases` | JSONB NULL | migration 0033 (PR37): subset of content phases to run (dependency-closure-expanded at launch); NULL = full subject flow |
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
| `prompt_hash` | String(128) NOT NULL | keys the cross-job extract cache; widened 64→128 in 0034 to hold a `custom:sha256:<hex>` custom-prompt provenance hash (78 chars) |
| `model_name` | String(128) NOT NULL | |
| `provider` | String(32) NULL | who actually produced it (failover may differ from the job's provider; migration 0019) |
| `output_md` | Text NULL | **the deliverable** — per-phase markdown; there is no assembly step and no structured-JSON columns (dropped in 0018) |
| `tokens_input` / `tokens_output` | Integer NULL | |
| `status` | String(32) NOT NULL | pending/running/done/failed |
| `error_message` | Text NULL | |
| `validation_warnings` | JSONB NULL | LLM-judge warnings (migration 0017) |
| `judge_status` | String(24) NULL | judge outcome (migration 0029): `ok` / `major_shipped` / `major_regen_failed` / `unavailable` / NULL (pre-0029 rows or extract phase) |
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
| `cache_creation_tokens` | Integer NOT NULL default 0 | claude-only cache-write tokens (migration 0030 C4); priced at 1.25× input via the `cache_write` rate (pricing-1b); the pricing-1 gap is CLOSED. |
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
| `book_id` | FK → books NOT NULL, part of **UNIQUE (`uq_batches_book_id_transport_output_language`)** | one batch per `(book, transport, output_language)` since migration 0038 — a different-transport OR different-language re-launch forks a new batch; same transport+language reuses. The old `uq_batches_book_id_transport` constraint was dropped and replaced. |
| `transport` | String(16) NOT NULL, server_default `'cli'` | launch-time transport (also on every member job); **DB CHECK `cli\|api`** (migration 0028) |
| `output_language` | String(16) NOT NULL, server_default `'uz'` | migration 0038: medium of instruction — `uz` / `en` / `ru`; **DB CHECK `uz\|en\|ru`**; part of the batch UNIQUE key (`uq_batches_book_id_transport_output_language`) so an EN re-launch never adopts a UZ batch. `batches_repo.get_or_create_for_book`, `find_active_for_section`, and `latest_for_section` are all language-scoped. |
| `extract_transport` / `judge_transport` | String(16) NOT NULL, server_default `'inherit'` | Phase 4.1 launch-default labels stamped onto created jobs — **jobs carry the truth**; on re-launch these labels can go stale; **DB CHECK `cli\|api\|inherit`** (migration 0028) |
| `extract_provider` / `extract_model` / `judge_provider` / `judge_model` | String(32 / 128 / 32 / 128) NULL | migration 0027: per-role provider/model launch labels (NULL = role default); jobs carry the truth |
| `subject` / `grade` | NOT NULL / NULL | denormalized for display |
| `provider` / `model` | NOT NULL / NULL | the launch-time pick |
| `notion_source` | String(512) NULL | |
| `custom_prompts` / `selected_phases` | JSONB NULL | migration 0033 (PR37): launch-default labels (same shape as on `homework_jobs`); **jobs carry the truth**. On a plain same-transport re-launch with no prompts these are NOT overwritten (the ON-CONFLICT only sets them when the launch carries them — a COALESCE would fail because Python None serializes to JSONB `'null'`, not SQL NULL). |
| `paused_at` | DateTime NULL | C4 batch-pause primitive (migration 0031): set by the budget monitor when this batch's api spend exceeds `COST_CAP_BATCH_USD`; also reused by C5 fleet-ctrl-3 for manual/fleet-gate pauses. NULL = not paused. |
| `paused_reason` | String(64) NULL | Machine-readable reason string (e.g. `"batch-cap"`, `"manual"`). Used by the budget monitor to reconcile its own pauses without touching batches paused by a different reason. |
| `session_limit_strategy` | String(16) NOT NULL, server_default `'inherit'` | C5 (migration 0036): what a worker does when a Claude session-limit hits a job in this batch — `pause` (requeue + worker self-cooldown until reset) / `switch` (fail the limited role over to a non-limited model) / `inherit` (follow `SESSION_LIMIT_STRATEGY` env via `resolve_session_limit_strategy`). **DB CHECK `pause\|switch\|inherit`** (`ck_batches_session_limit_strategy`). |

Design: **no stored counters**. Progress is computed on read (`rollup_for_batch`):
`DISTINCT ON (toc_entry_id) … ORDER BY toc_entry_id, created_at DESC` scoped to the batch,
then `GROUP BY status` — one vote per lesson (its newest job), so retries/top-ups can never
inflate the tally, and the denominator is `sum(tally.values())`. `UNIQUE(book_id, transport, output_language)` makes
find-or-create race-safe via `ON CONFLICT DO UPDATE … RETURNING` (conflict target `["book_id", "transport", "output_language"]`; Core insert — see the
mixin gotcha in §3). Launch semantics: fan-out one job per lesson; an existing *active*
job (pending/running/done) is skipped, and an orphan (`batch_id IS NULL`) is **adopted**.

### 3.7 `workers` — the fleet registry (Phase 1)

No mixins — tiny by design:

| Column | Type | Notes |
|---|---|---|
| `pc_id` | String(128) **PK** | `"hostname:pid"` |
| `last_heartbeat` | NOT NULL | always stamped with `func.now()` (DB clock) |
| `status` | String(32) NOT NULL, server_default `'online'` | `online` / `draining`; **enforced (C5/P1):** the worker reads its own status each registry beat and self-drains when `draining` (stops claiming + lets in-flight finish) via `_drain_check_and_beat`. `claim_next_job` doesn't filter on it — the worker self-stops instead |
| `notes` | Text NULL | |
| `capabilities` | JSONB NULL (migration 0035) | the worker's published capability blob `{"cli": {<5 providers>: bool}, "api": {"claude": bool, "gemini": bool}}` — which provider CLIs are installed on this PC and which api creds are present. Written on every registry beat (`upsert_heartbeat(..., capabilities=)`, no-clobber on status-only beats). NULL = legacy/never-published. The head unions it over **online** workers (`aggregate_fleet_capability`) to tell the launcher which `(provider × transport)` picks the fleet can actually serve (`launcher-capability-gate-1`, worklog 0085). |

**Liveness is derived, never stored**: `GET /workers` fetches `SELECT now()` and computes
`online = last_heartbeat >= db_now - worker_registry_stale_seconds` (default 90 s = 3 missed
30-s beats). Both sides of that comparison are DB-clock — see §2. The same staleness predicate
drives `aggregate_fleet_capability` (the launcher's fleet-serveability view) — zero online
workers ⇒ fail-open `{"online": false}` so the launcher shows a "no workers" banner instead of
greying everything.

### 3.8 `budget_state` — fleet-level API pause singleton (C4)

Exactly **one row** (`id=1`) enforced by `CHECK(id = 1)`, seeded by migration 0032.
No UUIDPK/Timestamps mixins — intentionally minimal.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | always `1` (singleton) |
| `api_paused_at` | DateTime NULL | set by the budget monitor when the fleet-daily api spend cap is exceeded; `claim_next_job` checks this and skips all api-transport jobs while non-NULL |
| `api_paused_reason` | String(64) NULL | e.g. `"fleet-daily-cap"` |

`budget_repo.get_state(session)` returns the singleton row; raises `RuntimeError` if the row is missing (indicates a broken migration state — run `alembic upgrade head`). The budget monitor clears the fleet pause if spend drops back below cap (e.g. after UTC midnight resets the 24h window). The singleton's pause state is distinct from per-batch `batches.paused_at` — an operator can check both via `GET /jobs/batch/{id}/cost` (returns `fleet_api_paused_at`/`fleet_api_paused_reason` alongside the per-batch fields).

### 3.9 `launch_defaults` — global model-selection singleton (migration 0037)

Exactly **one row** (`id=1`) enforced by `CHECK(id = 1)`, seeded by migration 0037.
No UUIDPK/Timestamps mixins — intentionally minimal.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | always `1` (singleton) |
| `judge_provider` / `judge_model` | String(32/128) NOT NULL | provider·model used by the LLM judge when the job's per-job override is Auto (NULL explicit pick); seed: `gemini`/`gemini-2.5-flash` |
| `judge_transport` | String(16) NOT NULL | `cli` \| `api` \| `inherit`; seed: `inherit` (follows the job's transport) |
| `extract_provider` / `extract_model` | String(32/128) NOT NULL | provider·model for lesson extract; seed: `gemini`/`gemini-2.5-flash` |
| `extract_transport` | String(16) NOT NULL | `cli` \| `api` \| `inherit`; seed: `inherit` |
| `toc_transport` | String(16) NOT NULL | transport for job-less book-upload TOC extraction: `cli` (seed, uses OAuth) or `api` (uses Vertex SDK — required on an all-Vertex head where gemini CLI OAuth is unavailable) |
| `output_language` | String(16) NOT NULL, server_default `'uz'` | migration 0038: the global default medium of instruction — `uz` / `en` / `ru`; **DB CHECK `uz\|en\|ru`**. Per-launch `output_language` overrides this; `null` inherits it. Resolved by `agent_models.resolve_output_language(explicit, global_default)`. Seed: `'uz'` (byte-identical to pre-0038 behavior). |

**Write surface:** `PUT /api/v1/settings/launch-defaults` (validates non-null provider/model + valid output_language; HTTP 422 on null — prevents a launch-bricking footgun; validated against `agent_models.MODEL_MANIFEST` + `OUTPUT_LANGUAGES`). **Read surface:** `GET /api/v1/settings/launch-defaults` + `launch_defaults_repo.get_or_create()` (called by launch endpoints, `toc_extractor`, and the claim gate). **No credentials here** — those live in `.env`/`os.environ`.

**⚠ Operator note (all-Vertex head):** the seed `toc_transport='cli'` means a worker with only Vertex SA creds and no gemini CLI OAuth will fail TOC extraction. After first deploy on an all-Vertex head, flip `toc_transport→api` at `/settings`.

### 3.10 `sa_keys` — pool of uploaded GCP service-account keys (migration 0041)

The DB row holds **metadata only**; the raw JSON bytes live on disk at `<var_dir>/sa_keys/<id>.json` (never in the DB).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID **PK** | |
| `original_filename` | Text NOT NULL | the uploaded file's name |
| `project_id` | Text NOT NULL | GCP project id auto-extracted from the uploaded JSON |
| `client_email` | Text NOT NULL | service-account email auto-extracted from the uploaded JSON |
| `sha256` | Text **UNIQUE** NOT NULL | content hash of the uploaded bytes; re-uploading identical bytes dedups to the existing row |
| `byte_size` | Integer NOT NULL | size of the uploaded JSON |
| `label` | Text NULL | optional operator nickname |
| `created_at` | timestamptz NOT NULL | |

Uploaded via `POST /sa-keys` (multipart; validated by `sa_key_validate.parse_and_validate_sa_key` — must be `type=="service_account"` with non-empty `project_id`/`client_email`/`private_key`, else 422). Downloaded via `GET /sa-keys/{id}/download` (**header-auth only** — rejects `?token=`; 503 when `AUTH_TOKEN` is empty; raw bytes). Listed via `GET /sa-keys` (metadata + `worker_count`; never returns `private_key`). Deleted via `DELETE /sa-keys/{id}` (409 if still assigned to any host).

### 3.11 `sa_key_assignments` — per-hostname key assignment

| Column | Type | Notes |
|---|---|---|
| `hostname` | Text **PK** | bare hostname (`socket.gethostname()`) of the worker PC — stable across restarts (unlike `workers.pc_id` = `hostname:pid`) |
| `key_id` | UUID FK → sa_keys **ondelete=RESTRICT** NULL | which key this host should use; NULL together with `scrub_requested_at` set = an active "clear this host's key" signal |
| `scrub_requested_at` | timestamptz NULL | set by the scrub/revoke endpoint |
| `updated_at` | timestamptz NOT NULL | |

Many hostnames may point at one key (shared-key case). Upsert on `PUT /sa-keys/assignments/{hostname}`; `DELETE /sa-keys/assignments/{hostname}` removes the row (non-destructive — the worker keeps its applied key); `POST /sa-keys/assignments/{hostname}/scrub` requests an active clear. On startup-before-claim and on a throttled main-loop tick, a worker calls `sa_keys_repo.get_assignment_with_key(hostname)`; when the assignment's `sha256` differs from what it last applied (and the worker is idle, `len(self._tasks)==0`), `worker._sync_sa_key` pulls the bytes (`sa_key_apply.pull_key_bytes`), writes `<var_dir>/sa_keys/active.json` atomically (`write_active_key` → temp + `os.replace`), sets `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` in `os.environ` and upserts them into the worker's `.env` (UTF-8, line-preserving), and calls `worker._rebind_capabilities()` to reassign the frozen `CAPABILITIES`/`CAPABILITY_BLOB` globals so a freshly-keyed idle worker begins claiming gemini-api jobs. The scrub path clears `os.environ`/`.env`, deletes `active.json`, and rebinds capabilities down.

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
  (reads `job.judge_provider`/`job.judge_model` — stamped at launch from the explicit pick
  or `launch_defaults` global default; seed gemini/gemini-2.5-flash) grading every content
  phase — regen on MAJOR verdict
  (cap = `settings.max_judge_regens`, default 1); outcome recorded as
  `phase_outputs.judge_status`. Job `done` → fire-and-forget Notion archive.
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
| 26 | 0026_drop_difficulty | `a8c7e6d5f4b3` | drops the dead `homework_jobs.difficulty` column |
| 27 | 0027_per_role_provider_model | `0027_per_role_provider_model` | adds nullable `extract_provider`/`extract_model`/`judge_provider`/`judge_model` to `homework_jobs` + `batches` (NULL = role default) |
| 28 | 0028_enum_check_constraints | `0028_enum_check_constraints` | CHECK constraints on `status` and `transport` enum columns (cluster-1 hardening) |
| 29 | 0029_judge_status | `0029_judge_status` | adds `phase_outputs.judge_status` (nullable String, no CHECK) |
| 30 | 0030_agent_usages_cache_creation | `0030_agent_usages_cache_creation` | adds `agent_usages.cache_creation_tokens` (C4 cost-safety) |
| 31 | 0031_batch_pause_columns | `0031_batch_pause_columns` | adds `batches.paused_at` / `paused_reason` (C4 batch-pause primitive; reused by C5) |
| 32 | 0032_budget_state | `0032_budget_state` | adds `budget_state` singleton table (id=1 CHECK) + seeds the row (C4 fleet-level pause) |
| 33 | 0033_custom_prompts_phases | `0033_custom_prompts_phases` | adds nullable `custom_prompts`/`selected_phases` JSONB to `homework_jobs` + `batches` (PR37 custom-prompt upload + phase-picker) |
| 34 | 0034_widen_prompt_hash | `0034_widen_prompt_hash` | widens `phase_outputs.prompt_hash` 64→128 for `custom:sha256:<hex>` provenance (PR37) |
| 35 | 0035_workers_capabilities | `0035_workers_capabilities` | adds `workers.capabilities` JSONB nullable (launcher-capability-gate-1, worklog 0085) |
| 36 | 0036_batch_session_limit_strategy | `0036_batch_session_limit_strategy` | adds `batches.session_limit_strategy` NOT NULL server_default `'inherit'` + CHECK `pause\|switch\|inherit` (C5 session-limit autopause, worklog 0089) |
| 37 | 0037_launch_defaults | `0037_launch_defaults` | creates `launch_defaults` singleton table (id=1 CHECK), seeds the row (judge/extract = gemini/gemini-2.5-flash/inherit, toc_transport=cli), and unconditionally backfills NULL `judge_provider`/`judge_model`/`extract_provider`/`extract_model` on `homework_jobs` |
| 38 | 0038_output_language | `0038_output_language` | adds `output_language` String(16) NOT NULL server_default `'uz'` + DB CHECK `uz\|en\|ru` to `homework_jobs`, `batches`, and `launch_defaults`; drops `uq_batches_book_id_transport` and creates `uq_batches_book_id_transport_output_language` (`book_id`, `transport`, `output_language`) |
| 39 | 0039_launch_defaults_content | `0039_launch_defaults_content` | adds `content_provider`, `content_model`, `content_transport` to `launch_defaults`; seeds `gemini`/`gemini-2.5-pro`/`inherit` (deliberately different from judge default to avoid self-grade guard on all-gemini fleet) |
| 40 | 0040_books_source_language | `0040_books_source_language` | adds `books.source_language` String(8) NOT NULL server_default `'uz'` + DB CHECK `ck_books_source_language IN ('uz','ru','en')` |
| 41 | 0041_sa_keys | `0041_sa_keys` | adds `sa_keys` table (UUID PK, `original_filename`, `project_id`, `client_email`, `sha256` UNIQUE, `byte_size`, `label` NULL, `created_at`) + `sa_key_assignments` table (`hostname` PK, `key_id` FK→sa_keys ondelete=RESTRICT NULL, `scrub_requested_at` NULL, `updated_at`) |
| 42 | 0042_books_toc_validation | `0042_books_toc_validation` | adds `books.toc_validation` String(16) NULL + DB CHECK `NULL\|verified\|mismatch\|skipped` and `books.toc_validation_detail` Text NULL — vision-validator verdict + explanation columns (worklog 0108) — **HEAD** |

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
| `failover_provider_order` | codex, gemini, kimi, opencode | per-phase failover (no claude) |
| `agent_limit_<provider>_<1h\|24h\|7d>` | per provider | local call-count caps for `/usage` bars |

> **Judge/extract model selection** is NOT in `config.py` — it lives in the DB `launch_defaults` singleton (§3.9), edited at the `/settings` page. `EXTRACT_PROVIDER`/`EXTRACT_MODEL`/`JUDGE_PROVIDER`/`JUDGE_MODEL`/`EXTRACT_TOC_TRANSPORT` env vars are **deleted** since migration 0037.

---

## 9. Operational notes

- **Inspect dev DB:** `docker exec edu-postgres psql -U edu -d edu_homework -c "<sql>"`.
- **Startup sweep caveat (multi-pod):** the API's `reclaim_stuck_jobs(stale_after_seconds=0)`
  at startup assumes it owns all running jobs. Safe single-host; in a fleet, rely on the
  TTL sweep instead (a restarting head would otherwise reset live workers' jobs).
- **`workers.status` drain is live (C5/P1)** — `POST /workers/{pc_id}/drain` sets `status="draining"`;
  the worker reads its own status each registry beat and self-drains (stops claiming, lets in-flight
  finish), skipping the `online` re-upsert so the signal isn't clobbered (`worker._drain_check_and_beat`).
- **Books table fake-status trap:** tests once seeded `status="ready"`, which is not a real
  status — batch readiness keys on `toc_ready`.
- **Don't `git add -A`** — `var/` holds copyrighted PDFs and `.env` holds secrets; both are
  gitignored, but stage explicitly anyway.
