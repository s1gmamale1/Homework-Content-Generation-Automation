# Code Map

What each source file does, grouped by layer. The runtime makes **no LLM SDK calls** — every model call shells out to a CLI provider via `agent.py`.

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
- **`books.py`** — upload book, **`/from-notion`** (pull a textbook from the Notion lessons tree), list/get/delete, TOC extraction SSE stream, delete a TOC entry.
- **`jobs.py`** — the core surface: `POST …/sections/{toc}/generate`, `GET /jobs/{id}`, `POST /jobs/{id}/retry`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/stream` (SSE progress), `GET /jobs/{id}/download` (ZIP of per-phase markdown), `GET /agent/models` (manifest), `GET /agent/stats` (usage windows + per-model breakdown + sparkline series).
- **`batch.py`** — fleet batch surface: `POST /jobs/batch` (fan out one job per lesson of a `toc_ready` book; skip/adopt existing), `GET /jobs/batches[/{id}]` (computed-on-read rollups), `GET /jobs/batches/{id}/jobs` (per-lesson-latest drill-in).
- **`workers.py`** — `GET /workers`: fleet liveness (every registered worker + derived `online` flag).
- **`notion.py`** — `/notion/grades` + `/notion/grades/{id}/subjects`: browse the Notion lessons tree for the Fetch-From-Notion flow.
- **`health.py`** — `/health`.
- **`__init__.py`** — assembles `/api/v1` router. **Order matters:** `batch` registers before `jobs` so static `/jobs/batches*` isn't swallowed by dynamic `/jobs/{job_id}`.

## `app/services/` — the engine
- **`agent.py`** — **CLI router & primary LLM surface.** `run_phase` / `run_phase_prompt` (markdown phases, with the per-phase JSON-`schema` mode + one retry), `extract_toc`, `summarize_lesson` + `read_whole_book_text` (whole-book-text extract), `extract_lesson_context` (PDF-attach extract path), `record_cached_lesson_extract`; `_resolve_model` / `_PROVIDER_DEFAULT_MODEL` (only `claude` + `opencode` carry a default); `_PLACEHOLDER_RULES` + `_VISUAL_PHASES` (visuals are described placeholders, never `<svg>`); concurrency semaphore + usage recording.
- **`agent_models.py`** — `MODEL_MANIFEST`: the single source of truth for valid `(provider, model)` pairs; enforced on generate, served to the frontend.
- **`pipeline.py`** — **per-job state machine.** Head: per-section `extract` (pinned cheap model, with Gate-A/Gate-B + failover). Tail: DAG-parallel content phases (wave scheduler off `flows.PHASE_DEPS`). Each phase runs `_run_with_failover` and the `phase_judge` LLM judge, then stores `output_md` on its `phase_outputs` row. No assembly stage.
- **`flows.py`** — `SUBJECTS`, `flow_for(subject)` (the 8-phase sequence), `SUBJECT_GAME` map, `PHASE_DEPS` (DAG deps) + `filter_prior_outputs`/`resolve_phase_deps`, per-phase output caps, SVG-stripping of prior outputs.
- **`prompts.py`** — prompt resolver: `get_prompt(subject, phase)` reads `prompts/_general/<phase>.md`, substitutes `{{SUBJECT}}`, `{{LANGUAGE_RULES}}` (Uzbek default / English-target) and `{{FAMILY_RULES}}` (CBP + flashcards, per subject-family). `USE_SUBJECT_PROMPTS=False` ⇒ the per-subject dirs are dormant.
- **`phase_judge.py`** — the LLM judge: grades each phase against its own prompt contract (cite-then-refute), tags failures `major`/`minor`, severity-gates regeneration. Pinned to `judge_provider`/`judge_model` (default `claude-opus-4-7`).
- **`model_tiers.py`** — judge model selection / self-fallback.
- **`failure_classifier.py`** — classifies a phase failure (transient / wall / model-not-found) to drive retry-vs-failover.
- **`worker.py`** — Postgres-backed queue (`FOR UPDATE SKIP LOCKED`); per-job heartbeat + lease-reclaim; a **dedicated registry-heartbeat task** (beats the `workers` table every 30s even when all slots are busy); stale-`cancelling` sweep; embedded in the API or run standalone per fleet PC (`python -m app.services.worker`).
- **`toc_extractor.py`** — PDF→TOC text helpers (glyph-subset decode, front/tail/reverse-tail scan) feeding gemini extraction.
- **`grade.py`** — `derive_grade_from_filename` (sinf/klass/класс → 1–11) for Notion archive keying.
- **`notion_archive.py`** — push a finished job's markdown into the Notion "Homework" tree (`_HOMEWORK_LAYOUT`, `PHASE_TITLES`, subject-page resolution, skip-reason stamping).
- **`notion_fetch.py`** — download a textbook PDF from a Notion page (size-capped to `max_file_mb`).
- **`events_bus.py`** — in-process pub/sub backing the job SSE streams.
- **`proc_tree.py`** — `kill_tree` (psutil) to reap a provider CLI's whole process tree on cancel/timeout.

### `app/services/providers/` — one adapter per CLI
- **`base.py`** — `Provider` ABC: `build_argv`, `parse_envelope`, `format_attachments`, `prompt_suffix`; `get_provider()` registry.
- **`claude.py` · `gemini.py` · `codex.py` · `kimi.py` · `opencode.py`** — the five providers. gemini = pinned extractor; kimi = no token counts / shells out for PDF; opencode **requires** an explicit `provider/model` (can't run bare).

### `app/services/notion/` — Notion REST client
- **`client.py`** (read/write), **`blocks.py`** (markdown→Notion blocks + file-upload blocks), **`page_creator.py`** (find-or-create pages), **`lesson_match.py`** (match a job to its lesson page under "Generated Lessons").

## `app/schemas/` — Pydantic contracts
- `book.py`, `toc.py` (`ExtractedTOC`), `job.py` (`GenerateRequest`, `JobOut`, `PhaseOut`), `classify.py` (`Difficulty`, `ClassifyDecision`), `events.py` (SSE event models). *(Content phases are markdown, not structured — the old per-phase JSON schemas were removed in the md-per-phase reshape.)*

## `app/models/` — SQLAlchemy ORM (tables)
- **`book.py`** (PDF on disk + legacy gemini columns + `grade`), **`toc_entry.py`**, **`homework_job.py`** (one per request: `status`, `provider`/`model`, `current_phase`, queue columns `priority`/`scheduled_at`/`claimed_at`/`claimed_by`/`attempts`, `batch_id`, `notion_archived_at`/`notion_skip_reason` — **no structured-output JSON columns**), **`phase_output.py`** (one row/phase: `output_md` + `provider`, `uq_phase_output_job_order`), **`agent_usage.py`** (one row/CLI call), **`batch.py`** (one per book, `uq_batches_book_id`, no counters), **`worker.py`** (`WorkerNode`: `pc_id` PK + `last_heartbeat`), `base.py` (UUIDPK/Timestamps mixins — Python-side defaults; Core inserts must supply them explicitly).

## `app/repositories/` — DB access (one per table)
- **`books.py` · `toc_entries.py` · `jobs.py` · `phase_outputs.py`** (use `create_or_reset`, not `create`) **· `agent_usage.py` · `batches.py`** (race-safe find-or-create + `DISTINCT ON` rollups/drill-in) **· `workers.py`** (heartbeat upsert + derived liveness, DB-clock).

## `alembic/` — migrations
- `env.py` + `versions/0001…0023` — schema history. `0018` dropped the old structured-output JSON columns (md-per-phase reshape); `0019` added `phase_outputs.provider`; `0020` backfills `book.grade`; `0021` adds `homework_jobs.notion_skip_reason`; `0022` adds the `workers` registry; `0023` adds `batches` + `homework_jobs.batch_id` (head `a1b2c3d4e5f6`). Full chain table in `docs/DATABASE.md`.

## `prompts/` — prompt templates (`.md`)
- **[live]** `prompts/_general/*.md` — the single set serving every subject (case-based-preview, flashcards, memory-check, practice-rlc, practice-error-detection, the four `practice-*` games, boss-arena, reflection).
- **[dormant]** `prompts/<subject>/*.md` — per-subject prompts + `flow.md`; off while `USE_SUBJECT_PROMPTS=False`, kept as the future override layer.

## `web/src/` — React SPA (operator console)
- **`main.tsx` / `App.tsx`** — bootstrap + routing.
- **`routes/`** — `login`, `library`, `upload`, `book`, `section`, `job`, `preview`, `usage`, `fleet` (one screen each). `job`/`preview` render each phase's markdown via `RichText`; `fleet` is the batch launcher + funnel + worker-liveness hub.
- **`lib/`** — `api.ts` (fetch client), `auth.ts` (token), `types.ts` (DTOs), `utils.ts`, plus UI helpers (`motion`, `subjects`, `ui`).
- **`hooks/use-event-source.ts`** — SSE subscription for live job progress.
- **`components/`** — `layout`, `protected-route`, `rich-text` (markdown + placeholder-card renderer), `space-backdrop`, `ui/*`, **`fleet/`** (`launcher`, `worker-cards`, `batch-funnel`, `rollup-bar`, `batch-lesson-list` — poll `/jobs/batches*` + `/workers` every ~3.5s), plus interactive renderers (`flashcards/`, `boss-fight/`, `games/`, `memory-sprint/`, `reading/`) — some predate the markdown-per-phase flip and are not all on the live render path.

## `tests/`
- Mirrors `app/` — `tests/schemas/`, `tests/services/` (flows, prompts, pipeline, agent router, judge, failover), etc. Run `uv run python -m pytest tests/ -q` (~330 tests).
