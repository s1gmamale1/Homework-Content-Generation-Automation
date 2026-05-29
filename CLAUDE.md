# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI + React app that turns a textbook PDF into a multi-phase homework packet (preview, flashcards, memory sprint, mini-games, boss-fight quiz, reading, reflection). Background workers run a DAG-parallel pipeline that drives **CLI subprocesses** of one of four LLM providers — `claude`, `kimi`, `codex`, `gemini` — chosen per job by the user.

Everything LLM-facing goes through `app/services/agent.py` (the CLI router); there is no Gemini SDK, OpenAI SDK, or Anthropic SDK in the runtime path. The four CLIs must be installed on `PATH`.

## Commands

```powershell
# Backend
uv sync                                 # install Python deps
uv sync --extra dev                     # incl. pytest, pytest-asyncio
uv run alembic upgrade head             # apply migrations
uv run alembic revision -m "describe"   # new migration
uv run uvicorn main:app --host 0.0.0.0 --port 8000   # API + SPA + embedded worker

# Tests
uv run python -m pytest tests/ -q                    # all (~41 tests)
uv run python -m pytest tests/services/test_agent.py -q     # single file
uv run python -m pytest tests/services/test_agent.py::test_resolve_model_no_default_leak -v   # one test

# Frontend (web/)
cd web && npm install
cd web && npm run dev                   # Vite dev server (proxies /api to :8000)
cd web && npm run build                 # writes web/dist/, served by FastAPI on :8000
cd web && npx tsc -p tsconfig.app.json --noEmit       # typecheck only

# Postgres (local dev)
docker run -d --name edu-postgres -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
  -e POSTGRES_DB=edu_homework -p 5433:5432 -v edu_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine

# Inspect DB
docker exec edu-postgres psql -U edu -d edu_homework -c "<sql>"
```

Local dev uses port **5433** for Postgres, not 5432, because the Windows host typically has its own Postgres on 5432. `.env` reflects this.

## Architecture

### Provider router (`app/services/providers/` + `app/services/agent.py`)

`Provider` is an abstract base (`base.py`) with one subclass per CLI (`claude.py`, `kimi.py`, `codex.py`, `gemini.py`). Each provider implements:
- `build_argv(...)` — argv vector for `asyncio.create_subprocess_exec`. Adds `--model X` only when truthy. Adds attachment scope flags (`--add-dir`, `--include-directories`) per CLI.
- `parse_envelope(stdout, last_msg_path)` — returns `(text, usage)` where usage has normalized keys `prompt_tokens`, `output_tokens`, `cached_tokens`, `total_tokens`, `raw`.
- `format_attachments(paths)` — provider-specific prompt preamble that names attached files. Claude returns `""` (consumes attachments via positional `@<path>` argv); the others return text instructing the CLI which tool to use to read the file.
- `prompt_suffix(ctx)` — visual-policy suffix (e.g., "use $imagegen for raster, SVG inline").

`agent.py` exposes:
- `run_phase`, `extract_toc`, `extract_lesson_context` — primary call surface used by the pipeline.
- `_resolve_model(provider, model)` — provider→default-model lookup. **Critical invariant**: `_resolve_model("gemini", None) is None` (and same for kimi/codex). Only `claude` has a default. This guards a real regression where a single shared default once leaked across providers; there is a unit test for it.
- `_PROVIDER_DEFAULT_MODEL` — the table the resolver reads.
- `STRUCTURED_PHASE_SCHEMAS` — phase name → Pydantic class for JSON-mode phases. JSON Schema is embedded into the prompt and the response is `model_validate_json`'d; on `ValidationError` we retry once with the error appended.

### MODEL_MANIFEST (`app/services/agent_models.py`)

The single source of truth for which `(provider, model)` pairs the API and frontend will accept. The `/api/v1/agent/models` endpoint serves it; `is_valid()` enforces it on `POST /generate`. Update here when adding/removing models.

### Pipeline (`app/services/pipeline.py`)

Per-job state machine:

1. **Head (sequential)**: `extract` → `classify` (if `flow.has_classify`).
2. **Tail (DAG-parallel)**: every phase declares its deps in `flows.PHASE_DEPS`; a wave-based scheduler launches phases concurrently when their deps are met. Typical 2× speedup over sequential.
3. **Assembly**: combines phase outputs into a single markdown packet plus structured JSON columns for interactive renders.

Three things this pipeline does that aren't obvious from a single file:
- **`extract` phase is pinned** to `settings.extract_provider` / `settings.extract_model` (default `gemini` / `gemini-2.5-flash`) regardless of which provider the user picked for the job. Extract is high-input/low-value (whole-PDF read → flat factual summary), so paying smart-tier rates buys nothing. All other phases honor `job.provider` / `job.model`.
- **Cross-job extract reuse**: if the same `(toc_entry_id, prompt_hash)` was already extracted in another job, the existing output is reused and a free `agent_usages` row is written via `agent.record_cached_lesson_extract`.
- **`phase_repo.create_or_reset`** (not `create`) is used because the orphan sweep in `main.lifespan` only marks stale phase rows `failed` — it doesn't delete them. Naive INSERT clashes with `uq_phase_output_job_order` on retry.

### Subject flows (`app/services/flows.py` + `prompts/<subject>/`)

Each supported subject (biology, english, geometriya-g7-11, history, kimyo-g7-11, math-algebra, physics) has:
- An entry in `SUBJECT_FLOWS` (easy / hard sequences, whether `classify` runs).
- A directory `prompts/<subject>/` with one `.md` per phase plus `flow.md` (documentation only).

`flows.PHASE_DEPS` declares which prior phase outputs each phase consumes; the parallel scheduler reads it. SVG blocks in prior outputs are stripped with `_strip_svgs` before injection (they cost ~800 input tokens each and downstream phases need the concept, not the picture).

### Queue + worker (`app/services/worker.py`)

Postgres-backed via `SELECT … FOR UPDATE SKIP LOCKED`. The API process embeds a worker (`worker_concurrency` in settings, default 4); set to 0 to run workers as separate pods. Restart-safe: `lifespan` sweeps stuck rows to `failed` so the worker can re-claim and `create_or_reset` rebuilds phase rows in place.

### Token / usage tracking

Every CLI call writes one row to `agent_usages` with `provider`, `model_name`, normalized token counts, `duration`, `success`, `raw_envelope`. The `/api/v1/agent/stats` endpoint aggregates by provider over rolling 1h/24h/7d windows; the `/usage` SPA route renders progress bars against per-provider caps configured via `AGENT_LIMIT_<PROVIDER>_<WINDOW>` env vars. These are local consumption, not real provider quotas — the four CLIs don't expose quota in headless mode.

**Kimi gap**: kimi 1.30 stream-json doesn't report token counts; rows have `prompt_tokens=0`, `output_tokens=0`, `cached_tokens=0`. Duration and call counts still work.

## Database (key tables)

- `homework_jobs` — one row per generation request. Has `provider`, `model`, `attempts`, `current_phase`, `status` (`pending`/`running`/`done`/`failed`), structured-output JSON columns.
- `phase_outputs` — one row per phase per job (`uq_phase_output_job_order` enforces no duplicates). Use `phase_repo.create_or_reset`, not `create`.
- `agent_usages` — one row per CLI subprocess call. The token-summary log at end-of-job reads these.
- `books` — has legacy `gemini_file_uri` / `gemini_cache_*` columns that are unused but kept nullable for backwards-compat. The PDF lives on disk at `var/books/<book_id>/source.pdf`.

## PDF handling caveats

- Stored on disk, not in Gemini Files API (the SDK is gone). Path is deterministic: `Path(settings.var_dir) / "books" / str(book_id) / "source.pdf"`.
- **Gemini CLI rejects files > 20 MB**. TOC extraction is hardcoded through Gemini and will fail for larger PDFs with a sandbox error. Pre-shrink, or change `settings.extract_provider`.
- **Kimi has no native PDF support**. The kimi prompt preamble instructs the model to shell out to Python (`pdfplumber` preferred, `pypdf`/`PyPDF2` fallback). If those aren't installed on the host, kimi will report extraction failure rather than fabricate content.

## Auth

Token-based via `Authorization: Bearer <token>` (REST) or `?token=<>` query param (SSE / downloads, since `EventSource` can't set headers). Comma-separated list in `AUTH_TOKEN`. Empty disables auth (everything is `user="anonymous"`).

## Things not to do

- Don't reintroduce a Gemini / Anthropic / OpenAI SDK call. Everything goes through the CLI router. `google-genai` was deliberately removed from `pyproject.toml`.
- Don't hardcode model names in `pipeline.py` — they belong in `agent_models.MODEL_MANIFEST` (frontend manifest) or `_PROVIDER_DEFAULT_MODEL` (server-side fallback).
- Don't bypass `phase_repo.create_or_reset` with raw `phase_repo.create` for retried jobs — you'll trip `uq_phase_output_job_order`.
- Don't add per-call provider/model overrides anywhere except where they already exist (extract pin via `settings.extract_*`); keeping job-level provider stable across the rest of the pipeline is what makes `agent_usages` and the UI badge mean something.
- Don't `unlink` the PDF after TOC extraction — every subsequent phase re-reads it.
