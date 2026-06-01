# Code Map

What each source file does, grouped by layer. Tags: **[active]** = on the live Flow v2 path · **[legacy]** = on disk but off the current flow (kept for back-compat / future override).

> Pipeline at a glance: **upload PDF → extract TOC → pick a section → generate** runs `extract → source-map → DAG-parallel content phases → assemble`. Every LLM call shells out to a CLI provider via `agent.py`. See `CLAUDE.md` for the architecture narrative.

---

## Entry point
- **`main.py`** — FastAPI app factory + `lifespan` (mounts API + SPA, starts the embedded worker, sweeps stuck jobs to `failed` on boot).

## `app/` root
- **`config.py`** — `Settings` (env/`.env`): DB URL, auth tokens, `extract_provider`/`extract_model` pin, worker concurrency, per-provider usage caps.
- **`db.py`** — async SQLAlchemy engine + `SessionLocal`.
- **`auth.py`** — Bearer-token / `?token=` dependency. Empty `AUTH_TOKEN` ⇒ everyone is `anonymous`.
- **`log.py`** — loguru setup.

## `app/api/v1/` — HTTP surface
- **`books.py`** — upload/list/get/delete books; TOC extraction SSE stream; delete a TOC entry.
- **`jobs.py`** — the core surface: `POST …/sections/{toc}/generate`, job get/retry, `…/stream` (SSE progress), `…/download` (packet), `GET /agent/models` (manifest), `GET /agent/stats` (usage windows).
- **`health.py`** — `/health`.
- **`__init__.py`** — assembles `/api/v1` router.

## `app/services/` — the engine
- **`agent.py`** — **CLI router & primary LLM surface.** `run_phase` / `extract_toc` / `extract_lesson_context` / `extract_source_map`; `_resolve_model` (provider→default; **only claude has one**); `STRUCTURED_PHASE_SCHEMAS` (phase→Pydantic, JSON-mode + retry); usage recording.
- **`agent_models.py`** — `MODEL_MANIFEST`: the single source of truth for valid `(provider, model)` pairs; enforced on generate, served to the frontend.
- **`pipeline.py`** — **per-job state machine.** Head (`extract`→source-map) → DAG-parallel content phases → assembly. Holds `_synth_md_for_structured` (structured→markdown, incl. the §8 teacher/student split) and `_render_homework_md` (final packet).
- **`flows.py`** — `SUBJECTS`, `flow_for(subject)` (8-phase Flow v2), `SUBJECT_GAME` map, `PHASE_DEPS` (DAG deps), prior-output filtering + SVG stripping.
- **`prompts.py`** — prompt resolver: `get_prompt(subject, phase)` reads `prompts/_general/<phase>.md`, substitutes `{{SUBJECT}}` + `{{LANGUAGE_RULES}}` (Uzbek default / English-target). `USE_SUBJECT_PROMPTS` switch for the per-subject override layer.
- **`worker.py`** — Postgres-backed queue (`FOR UPDATE SKIP LOCKED`); restart-safe; embedded in the API or run standalone.
- **`toc_extractor.py`** — PDF→TOC parsing helpers (glyph-subset decode, front/tail/reverse-tail scan) feeding gemini extraction.
- **`events_bus.py`** — in-process pub/sub backing the job SSE streams.

### `app/services/providers/` — one adapter per CLI
- **`base.py`** — `Provider` ABC: `build_argv`, `parse_envelope`, `format_attachments`, `prompt_suffix`; `get_provider()` registry.
- **`claude.py` · `kimi.py` · `codex.py` · `gemini.py`** — the four core CLIs. (gemini = pinned extractor; kimi = no token counts / shells out for PDF.)
- **`opencode.py`** — 5th provider; **requires** an explicit `provider/model` (can't run bare).

## `app/schemas/` — Pydantic contracts
- **[active]** `flow_v2.py` (CaseBasedPreview + learning blocks), `practice_games.py` (RLC, ErrorDetection, compact `CbpModeGame` + game payloads), `flashcards.py`, `memory_check.py`, `boss_arena.py`, `toc.py`, `book.py`, `job.py`, `events.py`.
- **[legacy]** `classify.py`, `reading.py`, `memory_sprint.py`, `games.py`, `final_challenge.py` — superseded by the Flow v2 reshape; kept on disk, off the flow.

## `app/models/` — SQLAlchemy ORM (tables)
- **`book.py`** (PDF on disk + legacy gemini columns), **`toc_entry.py`**, **`homework_job.py`** (one per request; status + structured-output JSONB columns), **`phase_output.py`** (one row/phase, `uq_phase_output_job_order`), **`agent_usage.py`** (one row/CLI call), `base.py`.

## `app/repositories/` — DB access (one per table)
- **`books.py` · `toc_entries.py` · `jobs.py` · `phase_outputs.py`** (use `create_or_reset`, not `create`) **· `agent_usage.py`**.

## `alembic/` — migrations
- `env.py` + `versions/0001…0015` — schema history (latest adds source-map, boss-arena, learning-sections, practice-arc JSON columns).

## `prompts/` — prompt templates (`.md`)
- **[active]** `prompts/_general/*.md` — the single set serving every subject (CBP, flashcards, memory-check, practice-rlc, practice-error-detection, the 4 `practice-*` games, boss-arena, reflection).
- **[legacy/override]** `prompts/<subject>/*.md` — per-subject prompts + `flow.md`/`classify.md`; **dead while `USE_SUBJECT_PROMPTS=False`**, kept as the future override layer.

## `web/src/` — React SPA (⚠ pre-Flow-v2; frontend transformation in progress)
- **`main.tsx` / `App.tsx`** — bootstrap + routing.
- **`routes/`** — `login`, `library`, `upload`, `book`, `section`, `job`, `preview`, `usage` (one screen each).
- **`lib/`** — `api.ts` (fetch client), `auth.ts` (token), `types.ts` (DTOs), `utils.ts`.
- **`hooks/use-event-source.ts`** — SSE subscription for live job progress.
- **`components/`** — `layout`, `protected-route`, `rich-text`, `ui/*` (button/card/input/…), plus **renderers**: `flashcards/`, `boss-fight/`, `games/` (`adaptive-quiz`, `memory-match`, `sentence-fill`, `tile-match`, `game-card`), `memory-sprint/`, `reading/`. ⚠ Several map to the **old** flow (memory-sprint, reading, adaptive-quiz) — these are the targets of the frontend transformation.

## `tests/`
- Mirrors `app/` — `tests/schemas/`, `tests/services/` (flows, prompts, pipeline synth, agent router), etc. ~227 tests; run `uv run python -m pytest tests/ -q`.
