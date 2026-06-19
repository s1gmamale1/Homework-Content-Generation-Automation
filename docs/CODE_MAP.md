# Code Map

What each source file does, grouped by layer. `transport=cli` (default): every model call shells out to a CLI provider via `agent.py`. `transport=api` (claude+gemini): model calls go to the provider SDKs directly via `app/services/api_transport.py`.

> Pipeline at a glance: **upload PDF → extract TOC → pick a section → generate**. A job runs `per-section extract → DAG-parallel content phases`; each phase emits **markdown** stored on its own `phase_outputs` row. There is no source-map step and no assembly step — the per-phase markdown is the deliverable. See `CLAUDE.md` for the architecture narrative.

---

## Entry point
- **`main.py`** — FastAPI app factory + `lifespan` (mounts API + SPA, starts the embedded worker when `worker_concurrency > 0`; on boot sweeps stuck books/phases to `failed` and resets orphaned `running` jobs to `pending` — a single-host sweep; fleet pods rely on the TTL reclaim instead).

## `app/` root
- **`config.py`** — `Settings` (env/`.env`): DB URL, auth tokens, queue/worker knobs, resilience (heartbeat / reclaim / per-attempt timeout / `failover_provider_order`), the `extract_provider`/`extract_model` pin, the `judge_provider`/`judge_model` LLM-judge pin, extract gates, Notion config, per-provider usage caps. `gemini_api_key`/`gemini_model` are **vestigial** (unread; leftover from the removed SDK).
- **`db.py`** — async SQLAlchemy engine + `SessionLocal`.
- **`auth.py`** — Bearer-token / `?token=` dependency. Empty `AUTH_TOKEN` ⇒ everyone is `anonymous`.
- **`log.py`** — loguru setup (stderr + rotating `var/server.log`).

## `app/api/v1/` — HTTP surface
- **`books.py`** — upload book, **`/from-notion`** (pull a textbook from the Notion lessons tree), list/get/delete, TOC extraction SSE stream, **`POST /{id}/toc/retry`** (re-run prep for a failed/stuck book; worklog 0073), delete a TOC entry. (`_start_toc_extraction` is the shared fire-and-forget trigger used by upload/from-notion/retry.)
- **`jobs.py`** — the core surface: `POST …/sections/{toc}/generate`, `GET /jobs/{id}`, `POST /jobs/{id}/retry`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/stream` (SSE progress), `GET /jobs/{id}/download` (ZIP of per-phase markdown), `GET /agent/models` (manifest), `GET /agent/stats` (usage windows + per-model breakdown + sparkline series).
- **`batch.py`** — fleet batch surface: `POST /jobs/batch` (fan out one job per lesson; **resume-aware** — a failed/cancelled section is resumed not recreated; `preview` mode returns disposition with zero writes; `relaunch_mode` resume|discard), `POST /jobs/batch/{id}/cancel` (cancel-all), `POST /jobs/batch/{id}/resume` (resume failed/cancelled), `GET /jobs/batches[/{id}]` (computed-on-read rollups; includes `paused_at`/`paused_reason` C4 fields), `GET /jobs/batches/{id}/jobs` (per-lesson-latest drill-in), `GET /jobs/batch/{id}/cost` (C4 observability: `batch_api_cost_usd`, `paused_at`/`paused_reason`, `fleet_api_paused_at`/`fleet_api_paused_reason`).
- **`workers.py`** — `GET /workers`: fleet liveness (every registered worker + derived `online` flag).
- **`notion.py`** — `/notion/grades` + `/notion/grades/{id}/subjects`: browse the Notion lessons tree for the Fetch-From-Notion flow.
- **`health.py`** — `/health`.
- **`__init__.py`** — assembles `/api/v1` router. **Order matters:** `batch` registers before `jobs` so static `/jobs/batches*` isn't swallowed by dynamic `/jobs/{job_id}`.

## `app/services/` — the engine
- **`agent.py`** — **LLM surface & primary orchestrator.** `run_phase` / `run_phase_prompt` (markdown phases, with the per-phase JSON-`schema` mode + one retry), `extract_toc` (+ `_toc_source_pdf` front+back vision-window fallback for scanned TOCs, worklog 0072), `summarize_lesson` + `read_whole_book_text` (whole-book-text extract), `summarize_lesson_vision` + `read_page_range_text` + `extract_text_is_too_sparse`/`pdf_page_count` (worklog 0070 extract fallbacks — vision page-window for scanned/sparse books, text subset for oversize), `extract_lesson_context` (legacy PDF-attach path, not called by the pipeline), `record_cached_lesson_extract`; `_resolve_model` / `_PROVIDER_DEFAULT_MODEL` (only `claude` + `opencode` carry a default); `_PLACEHOLDER_RULES` + `_VISUAL_PHASES` (visuals are described placeholders, never `<svg>`); concurrency semaphore + usage recording. `_spawn` dispatches `transport=api` gemini/claude calls to `api_transport.generate` (early, before the binary lookup, still inside the semaphore); all other providers go through the CLI subprocess path.
- **`api_transport.py`** — **direct provider-SDK generation for `transport=api`** (gemini `google-genai`, claude `anthropic`); returns the same `(rc, text, usage, stderr)` 4-tuple as `_spawn`; dispatched from `_spawn`. Mirrors `_auth_env` credential logic; loud truncation (claude `stop_reason==max_tokens` / gemini `MAX_TOKENS`); per-provider cached-token semantics. Text-only v1 (attachments → `NotImplementedError`).
- **`agent_models.py`** — `MODEL_MANIFEST`: the single source of truth for valid `(provider, model)` pairs; enforced on generate, served to the frontend.
- **`pricing.py`** — verified per-token `PRICE_MAP` + `cost_usd(provider, model, usage)`; feeds the per-transport `$` rollup on `/agent/stats`. Per-provider cached-token semantics (gemini prompt-inclusive vs claude disjoint).
- **`pipeline.py`** — **per-job state machine.** Head: per-section `extract` (pinned cheap model, with Gate-A/Gate-B + failover). Tail: DAG-parallel content phases (wave scheduler off `flows.PHASE_DEPS`). Each phase runs `_run_with_failover` and the `phase_judge` LLM judge, then stores `output_md` on its `phase_outputs` row. No assembly stage.
- **`subjects.py`** — **single source of truth for supported subjects** (full Uzbek curriculum, grades 1–11). `SubjectDef`(code, label, family, game, language, Notion keywords) + `REGISTRY`/`SUBJECT_CODES` + `notion_keyword_pairs()` (longest-first) + `history_variant()` (derives the **Jahon / O'zbekiston** variant from a book filename — same apostrophe-fold as the Notion archive split; feeds the computed `BookOut.subject_variant` + batch payload, which the FE renders as "History · Jahon · grade N"). `flows`, `prompts`, `notion_fetch`, and the FE all derive from this. Add a subject here (+ mirror in `web/src/lib/types.ts`/`subjects.ts`).
- **`flows.py`** — `SUBJECTS` + `SUBJECT_GAME` (derived from `subjects.REGISTRY`; `SUBJECT_GAME` is now a recommendation-only hint, not consumed by the flow), `flow_for(subject)` (the **11-phase** sequence — generates all four interaction mini-games every job, the full Gamified Practices set), `PHASE_DEPS` (DAG deps) + `filter_prior_outputs`/`resolve_phase_deps`, per-phase output caps, SVG-stripping of prior outputs.
- **`prompts.py`** — prompt resolver: `get_prompt(subject, phase)` reads `prompts/_general/<phase>.md`, substitutes `{{SUBJECT}}`, `{{LANGUAGE_RULES}}` (Uzbek default / English & Russian L2) and `{{FAMILY_RULES}}` (CBP + flashcards, per subject-family). `SUBJECT_LABELS`/`_SUBJECT_FAMILY` derive from `subjects.REGISTRY`. `USE_SUBJECT_PROMPTS=False` ⇒ the per-subject dirs are dormant.
- **`phase_judge.py`** — the LLM judge: grades each phase against its own prompt contract (cite-then-refute), tags failures `major`/`minor`, severity-gates regeneration. Pinned to `judge_provider`/`judge_model` (default `claude-opus-4-7`). `_FIDELITY_RULE` instructs the judge to treat the `--- LESSON CONTEXT ---` block as the ground-truth source for fact-checking. `_fidelity_flags` is a conservative warning-only deterministic year-signal (cross-checks years in output vs source; skips math/exercise lines; never gates regen).
- **`model_tiers.py`** — judge model selection / self-fallback.
- **`failure_classifier.py`** — classifies a phase failure (transient / wall / model-not-found) to drive retry-vs-failover.
- **`worker.py`** — Postgres-backed queue (`FOR UPDATE SKIP LOCKED`); per-job heartbeat + lease-reclaim; a **dedicated registry-heartbeat task** (beats the `workers` table every 30s even when all slots are busy); stale-`cancelling` sweep; embedded in the API or run standalone per fleet PC (`python -m app.services.worker`). `_compute_capabilities` now resolves per-role API capability against the **job's** `judge_provider`/`extract_provider` (`COALESCE(job column, settings default)`), so a gemini-judge api job is correctly claimable on a Vertex-only (no ANTHROPIC_API_KEY) worker. **`_budget_monitor`** (C4): a background loop (period: `COST_CHECK_INTERVAL_SECONDS`, default 60s) reading the cost ledger and gating api-spending jobs by pausing batches (`batches_repo.pause_batch` → `batches.paused_at`/`paused_reason`) or the fleet (`budget_repo.set_api_paused` → `budget_state.api_paused_at`) when spend exceeds `COST_CAP_BATCH_USD` / `COST_CAP_FLEET_DAILY_USD`. Disabled when both caps are 0.
- **`toc_extractor.py`** — PDF→TOC text helpers (glyph-subset decode, front/tail/reverse-tail scan) feeding gemini extraction.
- **`grade.py`** — `derive_grade_from_filename` (sinf/klass/класс → 1–11) for Notion archive keying.
- **`notion_archive.py`** — push a finished job's markdown into the Notion "Homework" tree (`_HOMEWORK_LAYOUT`, `PHASE_TITLES`, subject-page resolution, skip-reason stamping).
- **`notion_fetch.py`** — download a textbook PDF from a Notion page (size-capped to `max_file_mb`).
- **`storage.py`** — `book_pdf_path(book_id)`: the single deterministic on-disk PDF path helper (`<var_dir>/books/<id>/source.pdf`), used by both the writer and the pipeline reader.
- **`book_fetch.py`** — R13 fleet pull-on-demand: `ensure_book_pdf_sync(book_id)` returns the local PDF, or fetches it once from the head (`FLEET_HEAD_URL` → `GET /books/{id}/source.pdf`) and caches it when missing.
- **`events_bus.py`** — in-process pub/sub backing the job SSE streams.
- **`proc_tree.py`** — `kill_tree` (psutil) to reap a provider CLI's whole process tree on cancel/timeout.

### `app/services/providers/` — one adapter per CLI
- **`base.py`** — `Provider` ABC: `build_argv`, `parse_envelope`, `format_attachments`, `prompt_suffix`. (The `get_provider()` registry lives in the package `__init__.py`.)
- **`claude.py` · `gemini.py` · `codex.py` · `kimi.py` · `opencode.py`** — the five providers. gemini = pinned extractor; kimi = no token counts / shells out for PDF; opencode **requires** an explicit `provider/model` (can't run bare).

### `app/services/notion/` — Notion REST client
- **`client.py`** (read/write), **`blocks.py`** (markdown→Notion blocks + file-upload blocks), **`page_creator.py`** (find-or-create pages), **`lesson_match.py`** (match a job to its lesson page under "Generated Lessons").

## `app/schemas/` — Pydantic contracts
- `book.py`, `toc.py` (`ExtractedTOC`), `job.py` (`GenerateRequest`, `JobOut`, `PhaseOut`), `events.py` (SSE event models). *(Content phases are markdown, not structured — the old per-phase JSON schemas were removed in the md-per-phase reshape; the dead `classify.py` difficulty cluster was removed in worklog 0061/api-3.)*

## `app/models/` — SQLAlchemy ORM (tables)
- **`book.py`** (PDF on disk + legacy gemini columns + `grade`), **`toc_entry.py`**, **`homework_job.py`** (one per request: `status`, `provider`/`model`, `transport`/`extract_transport`/`judge_transport`, per-role `extract_provider`/`extract_model`/`judge_provider`/`judge_model` (migration 0027), `current_phase`, queue columns `priority`/`scheduled_at`/`claimed_at`/`claimed_by`/`attempts`, `batch_id`, `notion_archived_at`/`notion_skip_reason` — **no structured-output JSON columns**), **`phase_output.py`** (one row/phase: `output_md` + `provider` + `judge_status` (`ok`/`major_shipped`/`major_regen_failed`/`unavailable`/NULL), `uq_phase_output_job_order`), **`agent_usage.py`** (one row/CLI call; C4 adds `cache_creation_tokens` migration 0030), **`batch.py`** (one per `(book, transport)`, `uq_batches_book_id_transport`, carries transport/extract_transport/judge_transport, no counters; C4 adds `paused_at`/`paused_reason` migration 0031), **`budget_state.py`** (C4 singleton `id=1`, `api_paused_at`/`api_paused_reason`, migration 0032), **`worker.py`** (`WorkerNode`: `pc_id` PK + `last_heartbeat`), `base.py` (UUIDPK/Timestamps mixins — Python-side defaults; Core inserts must supply them explicitly).

## `app/repositories/` — DB access (one per table)
- **`books.py` · `toc_entries.py` · `jobs.py` · `phase_outputs.py`** (use `create_or_reset`, not `create`) **· `agent_usage.py` · `batches.py`** (race-safe find-or-create + `DISTINCT ON` rollups/drill-in; C4 batch-pause primitive: `pause_batch`/`unpause_batch`/`unpause_by_reason`/`paused_batch_ids_by_reason`) **· `workers.py`** (heartbeat upsert + derived liveness, DB-clock) **· `cost.py`** (C4 cost ledger — read-only queries: `batch_api_cost_usd`, `fleet_api_cost_usd`, `section_prior_api_cost`; sums `pricing.cost_usd` in Python, never SQL aggregation, because the per-provider cached-token semantics are non-trivial) **· `budget.py`** (C4 `budget_state` singleton: `get_state`, `set_api_paused`, `clear_api_paused`).

## `alembic/` — migrations
- `env.py` + `versions/0001…0029` — schema history. `0018` dropped the old structured-output JSON columns (md-per-phase reshape); `0019` added `phase_outputs.provider`; `0020` backfills `book.grade`; `0021` adds `homework_jobs.notion_skip_reason`; `0022` adds the `workers` registry; `0023` adds `batches` + `homework_jobs.batch_id`; `0024` adds `transport` (jobs+batches) + `agent_usages.auth_mode` + the `(book_id, transport)` batch key; `0025` adds `extract_transport`/`judge_transport` (jobs+batches); `0026_drop_difficulty` drops the dead `homework_jobs.difficulty` column; `0027_per_role_provider_model` adds nullable `extract_provider`/`extract_model`/`judge_provider`/`judge_model` to jobs+batches; `0028` adds CHECK constraints on status/transport enums; `0029_judge_status` adds `phase_outputs.judge_status` (nullable — head). Full chain table in `docs/DATABASE.md`.

## `prompts/` — prompt templates (`.md`)
- **[live]** `prompts/_general/*.md` — the single set serving every subject (case-based-preview, flashcards, memory-check, practice-rlc, practice-error-detection, the four `practice-*` games, boss-arena, reflection).
- **[dormant]** `prompts/<subject>/*.md` — per-subject prompts + `flow.md`; off while `USE_SUBJECT_PROMPTS=False`, kept as the future override layer.

## `web/src/` — React SPA (operator console)
- **`main.tsx` / `App.tsx`** — bootstrap + routing.
- **`routes/`** — `login`, `library`, `upload`, `book`, `section`, `job`, `preview`, `usage`, `fleet`, `monitor` (one screen each). `job`/`preview` render each phase's markdown via `RichText`; `fleet` is the batch launcher + an `OnlineStrip` worker-liveness line; `monitor` is the batch funnel + worker-liveness cards (moved off `/fleet` in the chunk-3 restructure).
- **`lib/`** — `api.ts` (fetch client), `auth.ts` (token), `types.ts` (DTOs), `utils.ts`, plus UI helpers (`motion`, `subjects`, `ui`).
- **`hooks/use-event-source.ts`** — SSE subscription for live job progress.
- **`components/`** — `layout`, `protected-route`, `rich-text` (markdown + placeholder-card renderer), `space-backdrop`, `ui/*`, **`fleet/`** (`launcher` + `online-strip` render on `/fleet`; `worker-cards`, `batch-funnel`, `rollup-bar`, `batch-lesson-list` render on `/monitor` — poll `/jobs/batches*` + `/workers` every ~3.5s), plus interactive renderers (`flashcards/`, `boss-fight/`, `games/`, `memory-sprint/`, `reading/`) — some predate the markdown-per-phase flip and are not all on the live render path.

## `tests/`
- Mirrors `app/` — `tests/schemas/`, `tests/services/` (flows, prompts, pipeline, agent router, judge, failover), etc. Run `uv run python -m pytest tests/ -q` (~500 tests).
