# How This System Works — A Plain-English Guide for New Coders

> This is the "read me first" tour. It explains **what every part of the app does and why**,
> in plain language, before you go reading code. `CLAUDE.md` is the terse rulebook;
> this file is the friendly walkthrough. When something here disagrees with the code,
> the code wins — tell us so we can fix this doc.

---

## 1. The one-sentence version

**You upload a textbook PDF, pick a chapter section, and the app turns that section into a
full interactive homework packet** — flashcards, a memory check, conceptual mini-games, a
"boss fight" quiz, and a reflection — by driving real **command-line AI tools** (the same
`claude`, `gemini`, `codex`, `kimi`, `opencode` CLIs you'd run in a terminal) as
background subprocesses.

That's the whole thing. Everything below is *how* that happens.

---

## 2. The single most important idea: we shell out to CLIs, not SDKs

Most AI apps import a library (`openai`, `anthropic`, `google-genai`) and call an API over
HTTPS with an API key. **This app does not.** There is no LLM SDK anywhere in the runtime.

Instead, when the app needs the AI to do something, it literally runs a command like you
would type in a terminal:

```
gemini -m gemini-2.5-flash      # (prompt piped into the program's stdin)
claude --model claude-sonnet-4-6
```

It spawns that program as a **child process**, pipes the prompt into its standard input,
reads the answer back from standard output, and parses it. Each of the five CLIs must be
installed and logged-in on the machine's `PATH`.

**Why do it this weird way?**
- Each CLI handles its own login/billing. The app never holds an API key. (There's a
  leftover `GEMINI_API_KEY` field in config — it's *vestigial*, nothing reads it.)
- It's free-tier friendly: e.g. `gemini` is free with a Google login, no card.
- One uniform interface ("run a program, pipe text") covers five very different vendors.

> ⚠️ When someone asks *"why is the API still being used?"* — the **FastAPI HTTP server**
> (the app's own web API that the browser talks to) is a totally different thing from an
> **LLM API**. We run an HTTP server; we do **not** call any LLM API. Don't confuse the two.

**The golden rule:** never reintroduce an LLM SDK. Everything AI-facing goes through the
CLI router (`app/services/agent.py`). This is settled infrastructure — don't rebuild it.

---

## 3. The big picture, end to end

```
  ┌────────────┐   upload PDF    ┌──────────────────┐
  │  Browser   │ ───────────────▶│  FastAPI server  │
  │ (React SPA)│                 │   (main.py)      │
  └────────────┘                 └────────┬─────────┘
        ▲                                  │ 1. save PDF to disk
        │ live progress (SSE)              │ 2. extract Table of Contents (gemini CLI)
        │                                  ▼
        │                         ┌──────────────────┐
        │                         │  Postgres DB     │  books, toc_entries,
        │                         │  (the queue too) │  homework_jobs, phase_outputs,
        │                         └────────┬─────────┘  agent_usages
        │                                  │
        │   user picks a section,          │ job row inserted with status='pending'
        │   clicks "Generate"              ▼
        │                         ┌──────────────────┐
        │                         │  Worker          │  polls DB for pending jobs,
        │                         │ (worker.py)      │  claims one, runs the pipeline
        │                         └────────┬─────────┘
        │                                  ▼
        │                         ┌──────────────────┐
        │                         │  Pipeline        │  extract → classify →
        └─────────────────────────│ (pipeline.py)    │  content phases (parallel) →
            phase-by-phase events  └────────┬─────────┘  assemble final packet
                                            ▼
                                   ┌──────────────────┐
                                   │  Agent router    │  builds argv, spawns the CLI,
                                   │  (agent.py +     │  pipes prompt, parses answer,
                                   │   providers/)    │  logs token usage
                                   └────────┬─────────┘
                                            ▼
                                   the actual CLI subprocess
                                   (claude / gemini / codex / kimi / opencode)
```

The flow in words:
1. **Upload.** Browser sends a PDF + subject. Server saves the PDF to disk and kicks off
   Table-of-Contents extraction in the background.
2. **TOC.** The `gemini` CLI reads the PDF and returns the chapter/section list. Those
   become editable rows the user can see.
3. **Generate.** User picks a section and a provider/model, clicks Generate. The server
   inserts a `homework_jobs` row with `status='pending'` and returns immediately. **No work
   happens in the web request** — it just enqueues.
4. **Worker claims it.** A background worker polls the DB, locks the pending row, and runs
   the pipeline.
5. **Pipeline runs the phases.** Extract the lesson text → classify difficulty → generate
   all the content phases (many in parallel) → assemble everything into one markdown packet.
6. **Live updates.** Throughout, the browser is subscribed to a Server-Sent-Events stream
   and shows each phase lighting up as it completes.
7. **Download / play.** When done, the packet is downloadable as markdown + a ZIP of JSON,
   and the interactive pieces (flashcards, games, quiz) render in the browser.

---

## 4. The data model (what's stored, and why)

Everything lives in **Postgres** (locally on port **5433**, not the usual 5432, because
Windows often already runs its own Postgres on 5432). Five tables matter:

| Table | One row per… | Holds |
|-------|--------------|-------|
| `books` | uploaded PDF | subject, filename, file hash, status. The PDF itself lives on **disk** at `var/books/<book_id>/source.pdf`, not in the DB. |
| `toc_entries` | chapter section | chapter/section number + title, page range. This is what the user picks to generate homework from. |
| `homework_jobs` | generation request | the chosen `provider`/`model`, `status` (pending/running/done/failed), `current_phase`, the final `assembled_md`, and a column of structured JSON per interactive phase (`flashcards_json`, `cbp_json`, `boss_arena_json`, the `practice_*_json` games, etc.). |
| `phase_outputs` | one phase of one job | the phase name, order, status, its markdown output, token counts. A unique constraint (`uq_phase_output_job_order`) forbids two rows for the same (job, order). |
| `agent_usages` | one CLI subprocess call | provider, model, normalized token counts, duration, success/failure, and the raw envelope. This is how the usage dashboard and the end-of-job cost table are built. |

Two things people trip on:
- **The PDF is on disk, not in the DB.** The path is deterministic. Every phase re-reads it
  from there. Don't delete it after TOC extraction — later phases need it again.
- **Retries reuse the job row.** Because phase rows survive a crash (the orphan sweep only
  marks them `failed`, doesn't delete them), the pipeline uses
  `phase_repo.create_or_reset`, never a raw `create` — otherwise the unique constraint trips.

---

## 5. The queue and the worker — why generation is a background job

When you click Generate, the server **does not** run the AI right there in the HTTP request.
A full HARD packet is ~10 AI calls and can take many minutes — far too long to hold a web
request open. So instead:

- The `/generate` endpoint just writes a `pending` row to `homework_jobs` and returns.
- A **worker** (`app/services/worker.py`) loops forever, asking Postgres for the next
  pending job using `SELECT … FOR UPDATE SKIP LOCKED`. That SQL trick lets multiple workers
  grab *different* jobs safely without stepping on each other.
- The worker holds N "slots" (a semaphore, default 4) so it runs at most N jobs at once.

The worker can run **two ways**:
- **Embedded** (default): it runs *inside* the FastAPI process, started in `main.py`'s
  lifespan. Good for a single machine. Set `WORKER_CONCURRENCY=0` to turn it off.
- **Standalone**: `python -m app.services.worker` runs only the worker, no web server — for
  scaling out to separate worker machines/pods.

**Crash safety:** if a worker dies mid-job, the row is stuck in `running`. On startup and
every 60s, the worker sweeps for `running` rows older than 2× the timeout and resets them to
`pending`, so another worker re-claims them. Nothing is silently lost.

There's also retry-with-backoff (up to `queue_max_attempts`), a per-job timeout, and
**backpressure**: if more than ~50 jobs are already waiting, `/generate` returns `503` instead
of letting the queue grow forever.

---

## 6. The pipeline — how one job becomes a packet

`app/services/pipeline.py`'s `run(job_id)` is the heart of the system. It's a small state
machine with three stages:

### Stage 1 — Head (runs in strict order, because each step feeds the next)
1. **`extract`** — read the chosen pages of the PDF and produce a flat factual summary of
   the lesson ("lesson_context"). This is **pinned to a cheap model** (`gemini` /
   `gemini-2.5-flash`) regardless of which provider the user picked, because it's a
   high-input / low-creativity task — paying premium rates here buys nothing.
   *(Also: results are cached across jobs. If the same section was already extracted, the
   prior output is reused for free.)*
2. **`source-map`** — from that summary, build a structured list of the ~30 key concepts
   (each with an id, label, and statement). This "source map" is later injected into every
   content phase so the AI stays faithful to the actual textbook instead of inventing
   material. *(Best-effort: if it fails the job still continues.)*
3. **`classify`** (only some subjects) — decide if this lesson is **EASY** or **HARD**. That
   choice picks which sequence of content phases runs. (Some subjects like English and
   History are *always* HARD and skip this step.)

### Stage 2 — Tail (content phases, run in PARALLEL)
This is where the actual homework gets generated. Each subject defines an ordered list of
phases (see §7). But they don't all run one-after-another — that would be slow. Instead each
phase declares **what earlier phases it depends on** (in `flows.PHASE_DEPS`), and a
**wave-based scheduler** launches every phase whose dependencies are already done,
concurrently. As each finishes, newly-unblocked phases launch. Typically ~2× faster than
sequential. If any phase fails, in-flight peers are cancelled and the job is marked failed.

Each phase's result is saved two ways: a human-readable markdown blurb (`output_md` on the
phase row) **and**, for interactive phases, a structured JSON blob in its dedicated column on
the job (so the browser can render the real interactive widget).

### Stage 3 — Assembly
`_render_homework_md` stitches everything into one markdown document with a fixed structure:
title → book/chapter/section → extracted summary → source map → **Learning Sections** →
**Practice Arc** → **Boss Arena** → **Reflection**. It's a *pure* function (data in,
string out, no DB) so it's easy to test. The result is saved to `homework_jobs.assembled_md`
and the job flips to `done`.

---

## 7. Subjects, flows, and phases

Supported subjects: **biology, english, geometriya-g7-11, history, kimyo-g7-11,
math-algebra, physics**.

Each subject has an entry in `SUBJECT_FLOWS` (`app/services/flows.py`) that lists its
**easy** and **hard** phase sequences and whether it runs `classify`. Each subject also has
a `prompts/<subject>/` folder with one `.md` prompt file per phase.

The Flow v2 packet is organized into four "divisions":

- **Learning Sections** — `case-based-preview` (a scenario where the student plays a role and
  makes decisions), `flashcards`, `memory-check`, and for English a `reading` passage.
- **Practice Arc** — 2-3 *typed conceptual games* curated per subject (not a random grab-bag).
  There are six game types backed by just **three schemas**:
  - `practice-rlc` → **Real-Life Challenge** (own schema)
  - `practice-error-detection` → **Error Detection** (own schema)
  - `practice-memory-match`, `practice-tictactoe`, `practice-jigsaw`, `practice-sentence`
    → all four are "interaction modes" sharing one **CbpModeGame** schema (a Case-Based
    Preview plus an `interaction_mode` tag). One schema, four games.
- **Boss Arena** — `boss-arena`, a Why→How→What reasoning "boss fight" quiz with HP/damage.
- **Reflection** — a short `reflection` debrief.

The point of the typed-game design: the New_Flow spec forbids "random disconnected games"
that don't match the target skill, so each subject runs only the games that fit it (e.g.
math runs error-detection + tictactoe + jigsaw; biology runs RLC + error + memory-match).

### Two token-saving tricks worth knowing
- **Dependency filtering:** a phase only receives the prior outputs it actually declared a
  dependency on — not the whole pile. Keeps prompts small.
- **SVG stripping:** before feeding an earlier phase's text into a later one, inline `<svg>`
  diagrams are replaced with `[diagram omitted]`. Downstream phases need the *concept*, not
  an ~800-token picture they'll never look at.

---

## 8. The provider router — the part that actually talks to the AI

This is the layer that makes "run a CLI" uniform across five very different tools.

### `app/services/providers/`
There's an abstract `Provider` base class (`base.py`) and one subclass per CLI: `claude.py`,
`gemini.py`, `codex.py`, `kimi.py`, `opencode.py`. Each provider is a **pure strategy object**
— it doesn't spawn anything itself; it just knows two things about its CLI:

- **`build_argv(...)`** — what command-line arguments to use. (e.g. add `--model X` only when
  a model is specified; how to point the CLI at attached files.)
- **`parse_envelope(stdout, …)`** — how to dig the actual answer text and the token counts
  out of whatever that CLI prints. It returns a normalized dict with the same keys for every
  provider: `prompt_tokens`, `output_tokens`, `cached_tokens`, `total_tokens`, `raw`.

Plus two prompt-shaping helpers: `format_attachments` (how to tell *this* CLI about attached
files — Claude takes them as `@path` arguments and returns `""`; others get a text
instruction to read the file) and `prompt_suffix` (visual/SVG policy text).

Because providers are pure, they're trivially unit-testable: feed in a fake stdout string,
assert on the parsed result. No subprocess needed in tests.

### `app/services/agent.py` — the driver
This is the orchestrator that the pipeline calls. Its job per call:
1. Resolve which model string to use (`_resolve_model`).
2. Find the CLI binary on `PATH`.
3. Ask the provider to build the argv.
4. Spawn the subprocess (`_spawn`), pipe the prompt to **stdin**, force UTF-8 (so Windows
   doesn't crash on Uzbek/math characters), read stdout/stderr back.
5. Ask the provider to parse the envelope.
6. Write one `agent_usages` row recording the cost.

A **process-wide semaphore** caps how many CLI subprocesses run at once across the whole app
(worker slots × per-job parallelism could otherwise fan out and trip rate limits).

The functions the pipeline actually calls: `extract_toc`, `extract_lesson_context`,
`extract_source_map`, `run_phase` / `run_phase_prompt[_structured]`.

### A critical invariant (there's a test guarding it)
`_resolve_model(provider, None)` returns a default model **only** for `claude` (and now
`opencode`, which literally can't run without a model). For `gemini`, `kimi`, and `codex` it
returns `None` — meaning "let the CLI pick its own default," no `--model` flag injected.
This guards a real past bug where one provider's default leaked into another's. **Do not**
give gemini/kimi/codex a hardcoded default here.

### Structured (JSON) phases
Many phases need machine-readable output (so the browser can render a real flashcard deck,
not a paragraph). For those, `STRUCTURED_PHASE_SCHEMAS` maps the phase name to a Pydantic
class. The JSON Schema is embedded into the prompt, the response is validated with
`model_validate_json`, and on a validation error it retries **once** with the error appended.

### `agent_models.py` — the menu
`MODEL_MANIFEST` is the single source of truth for which `(provider, model)` pairs are
allowed. The `/agent/models` endpoint serves it to the frontend dropdown; `is_valid()`
enforces it when a job is created. **Add or remove models here**, never by hardcoding names
in the pipeline.

---

## 9. The web API (FastAPI) and the live updates

`main.py` builds the FastAPI app, mounts the API under `/api/v1`, and — if the React app has
been built into `web/dist` — serves that too (with a catch-all so client-side routes work on
refresh). On startup it loads prompts, sweeps orphaned rows, and starts the embedded worker.

Key endpoints:
- `POST /books` — upload a PDF (+ subject). Saves to disk, starts TOC extraction in the
  background. De-dupes by file hash so re-uploading the same book is free.
- `GET /books/{id}` / `GET /books/{id}/toc/stream` — fetch the book + its TOC, or stream TOC
  progress live.
- `PATCH/DELETE .../toc/{entry}` — edit/fix a section's title or page range by hand (useful
  when auto-extraction is imperfect).
- `POST /books/{book}/sections/{section}/generate` — enqueue a homework job. Has **three
  layers of idempotency** so a double-click or network retry can't create duplicate jobs:
  an `Idempotency-Key` header cache, a natural-key check (reuse the existing active job for
  this section unless `force=true`), and a Postgres advisory lock to serialize races.
- `GET /jobs/{id}` — job status + all its phases.
- `GET /jobs/{id}/stream` — **Server-Sent Events**. First it replays whatever already
  happened (so a late-joining browser catches up), then streams new phase events live until
  the job completes or fails.
- `POST /jobs/{id}/retry` — re-run a *failed* job in place (same row, same provider).
- `GET /jobs/{id}/download?format=zip|md` — download the packet. ZIP = the markdown plus a
  JSON file per interactive phase.
- `GET /agent/models` / `GET /agent/stats` — the model menu, and per-provider rolling usage
  stats for the `/usage` dashboard.

**Why SSE and not WebSockets?** Progress is one-directional (server → browser) and SSE is
simpler. One quirk: the browser's `EventSource` can't send auth headers, so the stream/
download routes accept the token as a `?token=` query param instead of a `Bearer` header.

### Auth
Bearer token (`Authorization: Bearer <token>`), or `?token=` for the streaming/download
routes. Valid tokens are a comma-separated list in the `AUTH_TOKEN` env var. **Empty
`AUTH_TOKEN` disables auth entirely** (everything becomes `user="anonymous"`) — fine for
local dev.

---

## 10. The frontend (React + Vite, in `web/`)

A single-page app. In dev it runs on Vite's `:5173` and proxies `/api` to the backend on
`:8000`; in prod it's built to `web/dist` and served by FastAPI itself on `:8000`.

The routes mirror the user journey:
- `login` → paste a token (stored in sessionStorage, attached to every call).
- `upload` → drop a PDF, choose subject.
- `library` → all uploaded books.
- `book` → a book's TOC; pick a section.
- `section` → choose provider/model, click Generate.
- `job` → live phase-by-phase progress via the SSE hook (`use-event-source.ts`).
- `preview` → renders the finished interactive pieces — flashcard deck, memory match,
  tile/sentence games, the boss fight, the reading experience — using the structured JSON
  the pipeline produced (each has a component under `components/`).
- `usage` → the per-provider consumption dashboard.

`lib/api.ts` is the typed client, `lib/types.ts` mirrors the backend schemas.

---

## 11. Usage / cost tracking

Every CLI call writes an `agent_usages` row (provider, model, token counts, duration,
success). Two things read it:
- The **end-of-job token table** logged to the terminal — a tidy ASCII table showing per-call
  prompt/cached/fresh/output tokens so you can see caching working.
- The **`/usage` dashboard** — aggregates by provider over rolling 1h / 24h / 7d windows and
  shows progress bars against caps you set via `AGENT_LIMIT_<PROVIDER>_<WINDOW>` env vars.

⚠️ These are **local** counts — what *this app* has spent — not the providers' real quota.
The headless CLIs don't expose quota, so we track our own consumption and compare to limits
you configure to match your plan.

> **Kimi caveat:** kimi's stream-json output doesn't report token counts, so its rows show
> zeros for tokens. Call counts and durations still work.

---

## 12. PDF handling gotchas (real ones that have bitten us)

- **Gemini CLI rejects PDFs > 20 MB.** TOC extraction is hardcoded through gemini, so a
  bigger PDF fails with a sandbox error. Pre-shrink it or change `EXTRACT_PROVIDER`.
- **Kimi can't read PDFs natively.** Its prompt tells the model to shell out to Python
  (`pdfplumber`, falling back to `pypdf`). If those aren't installed, kimi reports failure
  rather than hallucinate.
- **Claude refuses copyrighted textbooks.** Claude Code's copyright filter will reject
  extracting from a real published textbook. That's *why* extraction is pinned to gemini —
  claude is only used for the *derived* content, never the raw textbook read.
- **TOC currently reads only the first ~10 pages / ~60k chars** of the PDF. If a book puts
  its contents at the *back* (common in some Uzbek textbooks), auto-extraction returns zero
  entries — you then add the section by hand. (Known follow-up: widen that window.)

---

## 13. How to run it (cheat sheet)

```powershell
# --- Backend ---
uv sync                                   # install Python deps
uv run alembic upgrade head               # apply DB migrations
uv run uvicorn main:app --host 0.0.0.0 --port 8000   # API + SPA + embedded worker

# --- Tests ---
uv run python -m pytest tests/ -q

# --- Frontend (web/) ---
cd web && npm install
cd web && npm run dev                     # dev server, proxies /api to :8000
cd web && npm run build                   # builds web/dist (then FastAPI serves it)

# --- Postgres (local dev, note port 5433) ---
docker run -d --name edu-postgres -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu `
  -e POSTGRES_DB=edu_homework -p 5433:5432 -v edu_pgdata:/var/lib/postgresql/data `
  postgres:16-alpine
```

You also need the CLIs you intend to use installed and logged-in on `PATH`
(`gemini` at minimum, since extraction depends on it).

---

## 14. The short list of "don'ts" (these will bite you)

1. **Don't add an LLM SDK.** Everything goes through the CLI router. `google-genai` was
   deliberately removed.
2. **Don't hardcode model names in the pipeline.** They belong in `MODEL_MANIFEST`
   (frontend menu) or `_PROVIDER_DEFAULT_MODEL` (server fallback).
3. **Don't give gemini/kimi/codex a default model** in `_PROVIDER_DEFAULT_MODEL` — the
   `None` is load-bearing and there's a test for it.
4. **Don't use raw `phase_repo.create`** for phases — use `create_or_reset`, or retried jobs
   trip the unique constraint.
5. **Don't add per-call provider/model overrides** anywhere except the existing extract pin.
   Keeping the job-level provider stable across the pipeline is what makes the usage stats and
   the UI badge mean something.
6. **Don't delete the PDF after TOC extraction** — every later phase re-reads it from disk.
7. **Don't commit secrets or the textbook PDFs.** `.env` and `var/` are gitignored for a
   reason (a past inline-comment bug in `.gitignore` once almost staged a copyrighted PDF —
   double-check before `git add -A`).

---

## 15. Where to look when…

| You want to… | Go to |
|--------------|-------|
| Understand the per-job flow | `app/services/pipeline.py` (`run`) |
| Change which phases a subject runs | `app/services/flows.py` (`SUBJECT_FLOWS`, `PHASE_DEPS`) |
| Add/remove a selectable model | `app/services/agent_models.py` (`MODEL_MANIFEST`) |
| Change how a CLI is invoked or parsed | `app/services/providers/<cli>.py` |
| Touch the spawn/usage logic | `app/services/agent.py` |
| Add an API endpoint | `app/api/v1/jobs.py` or `books.py` |
| Change the final packet layout | `pipeline._render_homework_md` |
| Edit what the AI is told to do per phase | `prompts/<subject>/<phase>.md` |
| Tweak queue/worker/timeout behavior | `app/config.py` + `app/services/worker.py` |
| See the project's terse rules | `CLAUDE.md` |
| Read the running worklog/history | `docs/memory/MASTER_MEMORY.md` |
