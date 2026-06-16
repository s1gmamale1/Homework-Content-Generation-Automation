# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI + React app that turns a textbook PDF into a multi-phase homework packet (preview, flashcards, memory sprint, mini-games, boss-fight quiz, reading, reflection). Background workers run a DAG-parallel pipeline that drives **CLI subprocesses** of one of five LLM providers — `claude`, `kimi`, `codex`, `gemini`, `opencode` — chosen per job by the user.

Everything LLM-facing goes through `app/services/agent.py` (the CLI router); there is no Gemini SDK, OpenAI SDK, or Anthropic SDK in the runtime path. The five CLIs must be installed on `PATH`. Each job (and batch) carries a **`transport`** enum — `cli` (default: each CLI's own subscription/OAuth login, $0 marginal) or `api` (pay-per-token, claude+gemini only) — see "Transport toggle" below.

## Implementation workflow

**Substantial or ambiguous** changes go through the gated pipeline below — **no code before an approved design** — using the `superpowers` skills at each stage.

**Exception — small, clear fixes:** when the root cause is already **verified and clear** and the change is low-risk, make it controller-direct: read the real code → fix → prove it (tests / `tsc --noEmit` / build), skipping brainstorm/spec/plan. The standing rules below still apply, and still log a worklog entry. If a "small" fix turns out to be ambiguous or risky once you're in it, stop and fall back to the full pipeline.

1. **Brainstorm** *(hard gate)* — explore the real code, propose 2–3 approaches per decision with a recommendation, lock each choice with the user. Output a spec to `docs/superpowers/specs/YYYY-MM-DD-*.md`, commit it, and **let the user review it** before continuing.
2. **Write the plan** — turn the approved spec into an ordered, **TDD-per-task** plan: exact file paths, real code, real tests, exact commands, **commit per task**, no placeholders. Self-review (spec coverage + type consistency). Save to `docs/superpowers/plans/…`, commit, **user approves**.
3. **Subagent-driven execution** — one **fresh subagent per task** (give it full text + scene-setting; it does TDD → commits). **The controller personally stress-tests every commit** before moving on: read the diff AND re-run the tests — never just trust the subagent's report. Track tasks with TaskCreate/TaskUpdate.
4. **Acceptance gate for anything that affects generation** — a **real CLI smoke** (actual `claude`/`gemini` call, in-process, no server needed) is the proof. Fact over theory: if a claim is about model behavior, run it; don't assert from prompt structure.
5. **Finish** — full suite green (`uv run python -m pytest tests/ -q`), then `finishing-a-development-branch` (push/merge/keep/discard — user decides; usually push to the working branch). Then write a **worklog** entry to `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`, and close shipped items in `docs/memory/ROADMAP.md`.

**Standing rules underneath all of it:**
- **90% bar** — push back when the user, a spec, or a plan is wrong, and explain why. Don't follow instructions blindly.
- **Never claim what you haven't run.** Verify against real code/output, re-check for staleness, and distinguish verified fact from prediction.
- **Stage only the files each task lists** — other sessions may be committing to the same branch (e.g. `web/`); never `git add -A`.
- **Backlog discipline:** raw ideas → `docs/memory/WISHLIST.md` (one line, no analysis); worked-up (issue → root cause+refs → deliverable) → `docs/memory/ROADMAP.md`; shipped → worklog. All `docs/memory/` files are tracked in git (committed alongside the work).

## Commands

```powershell
# Backend
uv sync                                 # install Python deps
uv sync --extra dev                     # incl. pytest, pytest-asyncio
uv run alembic upgrade head             # apply migrations
uv run alembic revision -m "describe"   # new migration
uv run uvicorn main:app --host 0.0.0.0 --port 8000   # API + SPA + embedded worker

# Tests
uv run python -m pytest tests/ -q                    # all (~380 tests; real-DB ones need RUN_DB_INTEGRATION=1 + DATABASE_URL)
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

`Provider` is an abstract base (`base.py`) with one subclass per CLI (`claude.py`, `kimi.py`, `codex.py`, `gemini.py`, `opencode.py`). Each provider implements:
- `build_argv(...)` — argv vector for `asyncio.create_subprocess_exec`. Adds `--model X` only when truthy. Adds attachment scope flags (`--add-dir`, `--include-directories`) per CLI.
- `parse_envelope(stdout, last_msg_path)` — returns `(text, usage)` where usage has normalized keys `prompt_tokens`, `output_tokens`, `cached_tokens`, `total_tokens`, `raw`.
- `format_attachments(paths)` — provider-specific prompt preamble that names attached files. Claude returns `""` (consumes attachments via positional `@<path>` argv); the others return text instructing the CLI which tool to use to read the file.
- `prompt_suffix(ctx)` — extra per-CLI policy text appended to the prompt. Claude and gemini return `""`; codex/kimi carry a short visual-policy line. Visual policy itself (described placeholders, never inline `<svg>`) lives in the prompts + `agent._PLACEHOLDER_RULES`.

`agent.py` exposes:
- `run_phase_prompt` (content phases → markdown), `extract_toc`, and `summarize_lesson` + `read_whole_book_text` (the extract path since the worklog-0035 local-text rewrite) — the primary call surface used by the pipeline. `run_phase` is the lower-level CLI wrapper underneath `run_phase_prompt`; `extract_lesson_context` is legacy and no longer called by the pipeline (dead since 0035).
- `_resolve_model(provider, model)` — provider→default-model lookup. **Critical invariant**: `_resolve_model("gemini", None) is None` (and same for kimi/codex). Only `claude` and `opencode` have defaults; kimi/codex/gemini stay `None` (opencode carries `opencode/deepseek-v4-flash-free`). This guards a real regression where a single shared default once leaked across providers; there is a unit test for it.
- `_PROVIDER_DEFAULT_MODEL` — the table the resolver reads.
- `_auth_env(provider_name, transport, base_env)` — pure per-spawn env shaping at the single `child_env` build inside `_spawn`, so no spawn path can bypass it. **cli is the unconditional baseline for EVERY spawn** (scrubs `GEMINI_API_KEY`/`ANTHROPIC_API_KEY`/vertex selectors; gemini gets `GOOGLE_GENAI_USE_GCA=true`) — including book-upload TOC extraction, which has no job. `transport=api` injects exactly the active provider's credential: claude → `ANTHROPIC_API_KEY`; gemini → `GEMINI_API_KEY`, or the Vertex service-account pair (`GOOGLE_APPLICATION_CREDENTIALS`+`GOOGLE_CLOUD_PROJECT`, location defaults `global`) when no key — missing credentials **raise loudly** (never inject `""`: an empty key silently falls back to OAuth and mis-bills).
- **Content phases are markdown-only.** The md-per-phase flip removed the old `STRUCTURED_PHASE_SCHEMAS` (phase→Pydantic) JSON-mode table; each phase's markdown output is graded by the LLM judge (`phase_judge.py`, worklog 0037), which regenerates once on a MAJOR verdict. `run_phase` still has a `schema=` JSON-validation mode (`model_validate_json` + one retry) used by structured callers like the judge — content phases no longer use it.

### MODEL_MANIFEST (`app/services/agent_models.py`)

The single source of truth for which `(provider, model)` pairs the API and frontend will accept. The `/api/v1/agent/models` endpoint serves it (plus an `api_supported` map); `is_valid()` enforces it on `POST /generate`. Also home to `API_PROVIDERS` (frozenset `{claude, gemini}`), `api_supported()`, and `validate_transport()` — the shared validator both `/generate` and `/jobs/batch` call (`transport=api` requires an api-supported provider AND an explicit manifest model; `model=None` would let the CLI pick a default that differs between auth modes). Update here when adding/removing models or api providers.

### Transport toggle (`transport`: `cli` | `api` — Phase 4, worklog 0053)

- **Scope:** the job's transport applies to **every spawn belonging to the job** — extract (provider/model stay pinned; only auth follows), content phases, and the judge. Enum (not bool) so a future `batch` transport slots in.
- **Worker fail-fast:** `claim_next_job(..., has_api_keys)` — a worker only claims `transport=api` jobs when `worker._compute_has_api_keys` is true at startup: `ANTHROPIC_API_KEY` AND (gemini key OR the Vertex SA pair). Half-configured workers warn and stay cli-only.
- **Failover:** `transport=api` restricts `_run_with_failover` to the requested provider only (no cross-provider legs — a fallback would run `model=None`, violating the explicit-model rule and mispricing). Same-provider retry budgets still apply.
- **Judge:** auth/401-class errors on an api job **re-raise** (job-level failure) instead of degrading to `judge-unavailable` — both the initial and post-regen judge calls. `phase_judge._AUTH_SIGNALS` covers claude AND gemini/Vertex shapes; deliberately no bare `"403"` (exception strings can embed generated output).
- **Batch key:** `batches` is `UNIQUE(book_id, transport)` (spec §9) — same-book re-launch on a *different* transport forks a new batch (clean per-transport rollup for benchmarking); same transport reuses. Per-section dedup/adoption is also transport-scoped (`find_active_for_section(transport=)`), else an api launch over a cli-generated book would no-op (§9a).
- **Attribution & $:** every `agent_usages` row records `auth_mode`; `app/services/pricing.py` holds the verified price map + `cost_usd` and `/agent/stats` emits per-provider-per-transport `$`. **Cached-token semantics are per-provider**: gemini's `promptTokenCount` INCLUDES cached (bill `prompt−cached`), claude's `input_tokens` is disjoint. Gemini "thoughts" bill as output (folded in by the gemini provider's `parse_envelope`). Known bias: claude cache *writes* (1.25×) are unpriced — no column yet (`pricing-1`).
- **Env plumbing:** the api credentials are read from `os.environ`; `config.py` calls `load_dotenv(override=False)` at import so `.env` works identically on bare metal and compose. A persisted `security.auth.selectedType` in `~/.gemini/settings.json` **overrides env-based gemini auth** and silently defeats the toggle — remove it on workers (the worker warns at startup). Operator gates: `docs/runbooks/phase4-transport-operator-acceptance.md`.

### Pipeline (`app/services/pipeline.py`)

Per-job state machine:

1. **Head (sequential)**: `extract` only (`pipeline.py:150`). `classify` / easy-hard was **removed** — there is a single flow per subject (`flows.flow_for`); `difficulty` is pinned `None` (`pipeline.py:123`).
2. **Tail (DAG-parallel)**: every phase declares its deps in `flows.PHASE_DEPS`; a wave-based scheduler launches phases concurrently when their deps are met. Typical 2× speedup over sequential.
3. **No assembly**: per-phase markdown in `phase_outputs` **is** the deliverable, graded by the LLM judge. (The old assembly + structured-JSON-columns step was removed with the md-per-phase flip.)

Three things this pipeline does that aren't obvious from a single file:
- **`extract` phase is pinned** to `settings.extract_provider` / `settings.extract_model` (default `gemini` / `gemini-2.5-flash`) regardless of which provider the user picked for the job. Extract is high-input/low-value (whole-PDF read → flat factual summary), so paying smart-tier rates buys nothing. All other phases honor `job.provider` / `job.model`. The **provider/model** pin is what's fixed — the **auth mode** follows `job.transport`.
- **Cross-job extract reuse**: if the same `(toc_entry_id, prompt_hash)` was already extracted in another job, the existing output is reused and a free `agent_usages` row is written via `agent.record_cached_lesson_extract`.
- **`phase_repo.create_or_reset`** (not `create`) is used because the orphan sweep in `main.lifespan` only marks stale phase rows `failed` — it doesn't delete them. Naive INSERT clashes with `uq_phase_output_job_order` on retry.

### Subject flows (`app/services/flows.py` + `prompts/<subject>/`)

Each supported subject (biology, english, geometriya-g7-11, history, kimyo-g7-11, math-algebra, physics — `flows.SUBJECTS`) has:
- A single phase sequence from `flows.flow_for(subject)` (`flows.py:39`): `_BASE_PHASES` + one subject-matched game (`flows.SUBJECT_GAME`) + `boss-arena` + `reflection`. **No `SUBJECT_FLOWS`, no easy/hard, no `classify`** — MVP single flow (`flows.py:1`).
- A directory `prompts/<subject>/` with one `.md` per phase plus `flow.md` (documentation only).

The **live runtime prompt set is `prompts/_general/`** (served via `get_prompt` with `{{SUBJECT}}` substitution, `USE_SUBJECT_PROMPTS=False`); the per-subject dirs above are a dormant override layer, not used at generation time.

`flows.PHASE_DEPS` declares which prior phase outputs each phase consumes; the parallel scheduler reads it. SVG blocks in prior outputs are stripped with `_strip_svgs` before injection (they cost ~800 input tokens each and downstream phases need the concept, not the picture).

### Queue + worker (`app/services/worker.py`)

Postgres-backed via `SELECT … FOR UPDATE SKIP LOCKED`. The API process embeds a worker (`worker_concurrency` in settings, default 4); set to 0 to run workers as separate pods. Restart-safe: `lifespan` sweeps stuck rows to `failed` so the worker can re-claim and `create_or_reset` rebuilds phase rows in place.

### Token / usage tracking

Every CLI call writes one row to `agent_usages` with `provider`, `model_name`, **`auth_mode`** (`cli`|`api`), normalized token counts, `duration`, `success`, `raw_envelope`. The `/api/v1/agent/stats` endpoint aggregates by provider over rolling 1h/24h/7d windows — including a per-transport breakdown with `cost_usd` (cli rows price $0; api rows via `pricing.cost_usd`); the `/usage` SPA route renders progress bars against per-provider caps configured via `AGENT_LIMIT_<PROVIDER>_<WINDOW>` env vars plus the api `$` rollup. The call-count caps are local consumption, not real provider quotas — the CLIs don't expose quota in headless mode.

**Kimi gap**: kimi 1.30 stream-json doesn't report token counts; rows have `prompt_tokens=0`, `output_tokens=0`, `cached_tokens=0`. Duration and call counts still work.

## Database (key tables)

- `homework_jobs` — one row per generation request. Has `provider`, `model`, **`transport`** (`cli`|`api`, default `cli`), `attempts`, `current_phase`, `status` (`pending`/`running`/`done`/`failed`/`cancelling`/`cancelled`), structured-output JSON columns.
- `batches` — one row per (book, transport) launch: **`UNIQUE(book_id, transport)`** (`uq_batches_book_id_transport`) — a different-transport re-launch forks a new batch; same-transport reuses (`batches_repo.get_or_create_for_book`, conflict target `["book_id","transport"]`). Rollups computed on read (DISTINCT ON over member jobs).
- `phase_outputs` — one row per phase per job (`uq_phase_output_job_order` enforces no duplicates). Use `phase_repo.create_or_reset`, not `create`.
- `agent_usages` — one row per CLI subprocess call, incl. **`auth_mode`**. The token-summary log at end-of-job and the per-transport `$` stats read these.
- `books` — has legacy `gemini_file_uri` / `gemini_cache_*` columns that are unused but kept nullable for backwards-compat. The PDF lives on disk at `var/books/<book_id>/source.pdf`.

## PDF handling caveats

- Stored on disk, not in Gemini Files API (the SDK is gone). Path is deterministic and honors `settings.var_dir` (`VAR_DIR`): use the single helper `app.services.storage.book_pdf_path(book_id)` → `<var_dir>/books/<book_id>/source.pdf` (both the writer `books.upload_book` and the pipeline reader call it; point `VAR_DIR` at a shared volume for multi-PC fleets — ROADMAP R13).
- **Gemini CLI rejects files > 20 MB**. TOC extraction is pinned to `settings.extract_provider` (default gemini — NOT hardcoded; `toc_extractor.py` reads the same setting as the lesson extract) and will fail for larger PDFs with a sandbox error. Pre-shrink, or change `settings.extract_provider` (claude consumes the PDF natively via positional `@path` argv).
- **Kimi has no native PDF support**. The kimi prompt preamble instructs the model to shell out to Python (`pdfplumber` preferred, `pypdf`/`PyPDF2` fallback). If those aren't installed on the host, kimi will report extraction failure rather than fabricate content.

## Auth

Token-based via `Authorization: Bearer <token>` (REST) or `?token=<>` query param (SSE / downloads, since `EventSource` can't set headers). Comma-separated list in `AUTH_TOKEN`. Empty disables auth (everything is `user="anonymous"`).

## Things not to do

- Don't reintroduce a Gemini / Anthropic / OpenAI SDK call. Everything goes through the CLI router. `google-genai` was deliberately removed from `pyproject.toml`.
- Don't hardcode model names in `pipeline.py` — they belong in `agent_models.MODEL_MANIFEST` (frontend manifest) or `_PROVIDER_DEFAULT_MODEL` (server-side fallback).
- Don't bypass `phase_repo.create_or_reset` with raw `phase_repo.create` for retried jobs — you'll trip `uq_phase_output_job_order`.
- Don't add per-call provider/model overrides anywhere except where they already exist (extract pin via `settings.extract_*`); keeping job-level provider stable across the rest of the pipeline is what makes `agent_usages` and the UI badge mean something.
- Don't `unlink` the PDF after TOC extraction — every subsequent phase re-reads it.
- Don't build a spawn env anywhere except through `agent._auth_env` — the cli baseline (key scrub + gemini GCA) must hold for **every** spawn, or a cli job can silently bill an API account (and vice versa).
- Don't "simplify" `pricing.cost_usd`'s cached-token handling into one formula — the semantics are **per provider** (gemini `prompt` INCLUDES `cached`; claude's is disjoint). Collapsing them re-introduces a verified double-bill.
- Don't read the api-transport credentials through pydantic `settings` — they are deliberately `os.environ` (threaded into child envs by `_auth_env`); `config.py`'s `load_dotenv(override=False)` is what makes `.env` reach them on bare metal.


<!-- ruflo-memory-convention:start -->
## Ruflo Memory Convention

**Store memories** with namespace `"patterns"` (the canonical namespace):
```
memory_store(key, value, namespace: "patterns", upsert: true)
```

**Retrieve memories** with `memory_search_unified` — it sweeps ALL namespaces
(`default`, `pattern`, `patterns`, `feedback`, …) and returns relevant results:
```
memory_search_unified(query)
```

> Do NOT use plain `memory_search` without a namespace — it defaults to the
> near-empty `"default"` namespace and returns ~nothing useful.

After completing a task, store a short verdict (what worked / what to apply next)
to namespace `"patterns"` with key `verdict:<taskId>` or `verdict:<sessionId>`.
<!-- ruflo-memory-convention:end -->
