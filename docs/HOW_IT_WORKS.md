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

## 2. The two generation paths: CLI subprocess vs. provider SDK

**`transport=cli` (default):** when the app needs the AI to do something, it literally runs
a command like you would type in a terminal:

```
gemini -m gemini-2.5-flash      # (prompt piped into the program's stdin)
claude --model claude-sonnet-4-6
```

It spawns that program as a **child process**, pipes the prompt into its standard input,
reads the answer back from standard output, and parses it. Each of the five CLIs must be
installed and logged-in on the machine's `PATH`.

**`transport=api` (claude+gemini only):** instead of shelling out to the CLI, the app calls
the provider SDKs directly — `google-genai` for gemini, `anthropic` for claude — via a
single new module `app/services/api_transport.py`. This path returns the same
`(rc, text, usage, stderr)` 4-tuple as the CLI path and is dispatched from `agent._spawn`
(early, before the binary-lookup, but still inside the concurrency semaphore). Credentials
(`GEMINI_API_KEY` / Vertex SA for gemini; `ANTHROPIC_API_KEY` for claude) come from the
worker's process env. `transport=api` was added because the CLI-with-key path bills
~2.6–10× the tokens and is ~1.5–3× slower for equal-quality output (the gemini CLI prepends
a ~9.2k-token agent system prompt on every call). Text-only v1: attachments raise a loud
`NotImplementedError`; TOC extraction stays CLI-pinned.

**Why do it the CLI way for `transport=cli`?**
- Each CLI handles its own login/billing — no API key needed. (Phase 4.1 also added
  per-role billing: `extract_transport`/`judge_transport` (`cli | api | inherit`) let one
  job bill e.g. gemini-api content to a Vertex credit while extract+judge ride the
  subscriptions; the claim gate routes each job only to workers credentialed for its
  resolved combination.)
- It's free-tier friendly: e.g. `gemini` is free with a Google login, no card.
- One uniform interface ("run a program, pipe text") covers five very different vendors.

> ⚠️ When someone asks *"why is the API still being used?"* — the **FastAPI HTTP server**
> (the app's own web API that the browser talks to) is a totally different thing from an
> **LLM API**. We run an HTTP server; we do **not** call any LLM API. Don't confuse the two.

**The golden rule on the `cli` path:** never add SDK calls to the CLI router or anywhere
outside `app/services/api_transport.py`. `app/services/agent.py` is the CLI router for
`transport=cli`; `api_transport.py` is the SDK layer for `transport=api`. Don't mix them.

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
        │                         │  Pipeline        │  extract →
        └─────────────────────────│ (pipeline.py)    │  content phases (parallel);
            phase-by-phase events  └────────┬─────────┘  each phase → markdown
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
5. **Pipeline runs the phases.** Extract the lesson text → generate all the content phases
   (many in parallel), each producing its own markdown. (Every subject runs the same
   sequence — there's no easy/hard split, and no separate assembly step: the per-phase
   markdown *is* the deliverable.)
6. **Live updates.** Throughout, the browser is subscribed to a Server-Sent-Events stream
   and shows each phase lighting up as it completes.
7. **Download / review.** When done, the packet downloads as a ZIP of one markdown file per
   phase, and the operator console renders each phase's markdown for review.

---

## 4. The data model (what's stored, and why)

Everything lives in **Postgres** (locally on port **5433**, not the usual 5432, because
Windows often already runs its own Postgres on 5432). Seven tables matter
(full column-level detail lives in `docs/DATABASE.md`):

| Table | One row per… | Holds |
|-------|--------------|-------|
| `books` | uploaded PDF | subject, filename, file hash, status. The PDF itself lives on **disk** at `var/books/<book_id>/source.pdf`, not in the DB. |
| `toc_entries` | chapter section | chapter/section number + title, page range. This is what the user picks to generate homework from. |
| `homework_jobs` | generation request | the chosen `provider`/`model`, `status` (pending/running/done/failed/cancelling/cancelled), `current_phase`, the queue columns (`attempts`, `claimed_at`, …), an optional `batch_id` (fleet membership), and Notion-archive markers. The generated content lives on `phase_outputs`, **not** here — there are no structured-JSON columns. |
| `phase_outputs` | one phase of one job | the phase name, order, status, its markdown output, token counts. A unique constraint (`uq_phase_output_job_order`) forbids two rows for the same (job, order). |
| `agent_usages` | one CLI subprocess call | provider, model, normalized token counts, duration, success/failure, and the raw envelope. This is how the usage dashboard and the end-of-job cost table are built. |
| `batches` | fleet batch (one per `(book, transport)` since Phase 4 — a cli and an api batch of the same book coexist for benchmarking) | the launch-time subject/grade/provider/model/transport (+ Phase-4.1 role-transport launch defaults; member jobs carry the truth). **No stored counters** — progress is computed on read from member jobs (one vote per lesson, its newest job), so retries can't inflate the tally. |
| `workers` | worker process (a fleet PC) | `pc_id` ("hostname:pid"), `last_heartbeat`, status label. Online/offline is **derived** from heartbeat freshness against the DB clock, never stored. |

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

**Crash safety:** if a worker dies mid-job, the row is stuck in `running`. A live worker
refreshes its claim every `heartbeat_seconds` (30s); any `running` row whose claim is older
than `reclaim_stale_seconds` (120s) is treated as orphaned and reset to `pending`, so another
worker re-claims it. Nothing is silently lost.

There's also retry-with-backoff (up to `queue_max_attempts`), a per-job timeout, and
**backpressure**: if more than ~50 jobs are already waiting, `/generate` returns `503` instead
of letting the queue grow forever.

**One clock to rule them all:** every timestamp the queue *compares* (claim eligibility,
lease staleness, backoff scheduling, worker liveness) is written **and** read with the
database's clock (`func.now()`), never the host's. This matters because Docker/WSL2 clocks
drift relative to the host — mixing clocks once made freshly-created jobs look "scheduled
in the future" and flake the claim. Host-clock timestamps are only used for record-only
stamps like `completed_at`. (Full detail: `docs/DATABASE.md` §2.)

### The fleet layer — many PCs, one head

The same queue scales to a **fleet**: N PCs each run a standalone worker
(`python -m app.services.worker`) pointed at one shared Postgres "head." `FOR UPDATE SKIP
LOCKED` already guarantees two workers can never claim the same job, so scaling out is just
"start more workers." On top of that sit three small pieces:

- **Workers registry** (`workers` table): each worker heartbeats its `pc_id` every 30s on a
  dedicated task (deliberately *not* in the main loop — a busy worker whose slots are full
  would otherwise stop beating and look dead). `GET /workers` derives online/offline from
  heartbeat freshness (90s = 3 missed beats).
- **Batches** (`batches` table + `POST /jobs/batch`): launch a whole book as one batch —
  one job per lesson, fanned into the shared queue. Lessons that already have an active job
  are skipped (or adopted, if they don't belong to a batch yet), so re-launching is a safe
  "top-up." Progress rollups are computed on read, one vote per lesson.
- **The `/fleet` page**: launch a Notion subject end-to-end (fetch → TOC-extract →
  launch), with a one-line worker-liveness strip (`OnlineStrip`).
- **The `/monitor` page**: watch batch funnels fill, see PC liveness cards, and drill
  into a batch's lessons to cancel/retry individual ones.

One caveat worth knowing: the API's *startup* orphan sweep resets **all** `running` jobs to
`pending` (it assumes a single host). In a multi-pod fleet that would steal live workers'
jobs — fleet setups rely on the TTL-based sweep instead.

---

## 6. The pipeline — how one job becomes a packet

`app/services/pipeline.py`'s `run(job_id)` is the heart of the system. It's a small state
machine with two stages (a head and a parallel tail):

### Stage 1 — Head (the `extract` step)
1. **`extract`** — read the chosen lesson and produce a flat factual summary of it
   ("lesson_context"). This is **pinned to a cheap model** (`gemini` / `gemini-2.5-flash`)
   regardless of which provider the user picked, because it's a high-input / low-creativity
   task — paying premium rates here buys nothing. It has its own readability gates and fails
   over if the pinned provider can't read the book.
   *(Also: results are cached across jobs. If the same section was already extracted, the
   prior output is reused for free.)*

   *(Two steps that used to live here are gone: a `classify` step that decided EASY vs HARD,
   and a `source-map` step that built a concept list for injection. Flow v2 runs one sequence
   for every subject, and grounding now comes from the lesson summary itself.)*

### Stage 2 — Tail (content phases, run in PARALLEL)
This is where the actual homework gets generated. Every subject runs the same ordered list of
phases (see §7). But they don't all run one-after-another — that would be slow. Instead each
phase declares **what earlier phases it depends on** (in `flows.PHASE_DEPS`), and a
**wave-based scheduler** launches every phase whose dependencies are already done,
concurrently. As each finishes, newly-unblocked phases launch. Typically ~2× faster than
sequential. If any phase fails, in-flight peers are cancelled and the job is marked failed.

Each phase's result is saved as markdown (`output_md` on its `phase_outputs` row), tagged
with the provider that produced it. There is no per-phase JSON column — the markdown is the
deliverable. Each produced phase is also graded by the LLM judge (see `phase_judge.py`)
before the job moves on.

### (No assembly stage)
There is **no** assembly step that stitches phases into one document. Each phase's markdown
stands alone on its `phase_outputs` row; once every phase is done the job flips to `done`.
The download endpoint zips those per-phase markdown files on demand. *(An earlier design had
a `_render_homework_md` assembler writing `homework_jobs.assembled_md`; both were removed in
the markdown-per-phase reshape.)*

---

## 7. Subjects, flows, and phases

Supported subjects: **26 subjects of the Uzbek curriculum, grades 1–11** — the academic ones plus
the non-academic ones that ship a real textbook in Notion (music, fine arts, technology, upbringing,
pre-conscription training). Excluded: PE (by decision) and the three textbook-less soft subjects
(ethics, spirituality, future-hour). The single source of truth is
the registry in **`app/services/subjects.py`**; `flows`, `prompts`, `notion_fetch`, and the
frontend all derive their subject tables from it. Each subject declares a family
(picks the prompt's family block), a language rule (Uzbek default; English/Russian L2),
and a practice game.

**One flow for every subject (Flow v2 MVP).** There's no easy/hard split and no `classify`
step. `flows.flow_for(subject)` returns the same **11-phase** sequence for every subject —
the full Gamified Practices set is generated, none skipped:

```
case-based-preview → flashcards → memory-check → practice-rlc →
practice-error-detection → practice-memory-match → practice-tictactoe →
practice-jigsaw → practice-sentence → boss-arena → reflection
```

The packet is organized into four "divisions":

- **Learning Sections** — `case-based-preview` (a scenario where the student plays a role and
  makes decisions, with two interleaved "learning blocks"), `flashcards`, `memory-check`.
- **Practice Arc** — all six practice games: `practice-rlc` (**Real-Life Challenge**),
  `practice-error-detection` (**Error Detection**), and **all four interaction mini-games**
  (`memory-match`, `tictactoe`, `jigsaw`, `sentence`) — every job, every subject. The four
  share one **compact `CbpModeGame`** schema — a game board (typed payload) + a single open
  "explain your reasoning" prompt (`why_prompt`). The `SUBJECT_GAME` map
  (`app/services/flows.py`) is now metadata only — the per-subject *recommended* game
  (`memory-match` for biology & history, `tictactoe` for physics/kimyo/math-algebra, `jigsaw`
  for geometriya, `sentence` for english) — a hint for downstream curation; it no longer gates
  generation.
- **Boss Arena** — `boss-arena`, a Why→How→What reasoning "boss fight" quiz.
- **Reflection** — a short `reflection` debrief.

Why all four interaction games now (previously one per subject): we generate the full set so
every game type has content, and *which* game to use is curated downstream rather than fixed by
the generator. This is affordable because the four games were lightened from the **full**
Case-Based-Preview shell (one game once took 20+ minutes) to a board + one reasoning prompt
(~tens of seconds each), and they run **in parallel** in one scheduler wave (shared deps, no
inter-game deps), so the extra games add ~+37% phase count but much less wall-clock and only a
small $ increase (the costly whole-PDF `extract` is unchanged; game outputs are small). The
spec's "no random disconnected games" rule is satisfied by curation, not by skipping
generation.

### Prompts: one general set, parameterized by subject
All phases now read from **`prompts/_general/<phase>.md`** — a single set serving every
subject. `app/services/prompts.py`'s `get_prompt(subject, phase)` substitutes two tokens:
- `{{SUBJECT}}` → the subject label, so the same prompt knows what it's teaching.
- `{{LANGUAGE_RULES}}` → the language directive: for `english`, **English target content with
  Uzbek "Siz" scaffolding** (CEFR-leveled by grade); for every other subject, formal Uzbek.
  There's no model-side language detection — the subject key selects the block at build time.

The old per-subject `prompts/<subject>/` folders still exist but are **dead** — a future
override layer gated behind `USE_SUBJECT_PROMPTS=False`.

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
instruction to read the file) and `prompt_suffix` (extra per-CLI policy text; the claude and
gemini suffixes are currently empty — visual policy lives in the prompts).

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

The functions the pipeline actually calls: `extract_toc` (TOC at upload time),
`summarize_lesson` / `read_whole_book_text` (the per-section extract), and `run_phase` /
`run_phase_prompt` (the content phases).

### A critical invariant (there's a test guarding it)
`_resolve_model(provider, None)` returns a default model **only** for `claude` (and now
`opencode`, which literally can't run without a model). For `gemini`, `kimi`, and `codex` it
returns `None` — meaning "let the CLI pick its own default," no `--model` flag injected.
This guards a real past bug where one provider's default leaked into another's. **Do not**
give gemini/kimi/codex a hardcoded default here.

### Optional JSON-schema mode
Content phases produce **markdown**, not structured JSON (the old per-phase schema table,
`STRUCTURED_PHASE_SCHEMAS`, was removed). `run_phase` still has a generic, opt-in `schema=`
mode — when a caller passes a Pydantic model, the JSON Schema is embedded in the prompt and
the response is `model_validate_json`'d with one retry. It's used for structured *internal*
calls (e.g. TOC extraction and the LLM judge's verdict), not the homework phases.

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
- `GET /jobs/{id}/download` — download the packet as a ZIP of one markdown file per completed
  (non-extract) phase. There's also `POST /jobs/{id}/cancel` to stop a running job.
- `GET /agent/models` / `GET /agent/stats` — the model menu, and per-provider rolling usage
  stats for the `/usage` dashboard.
- `POST /books/from-notion` — fetch a subject's textbook straight from Notion (by subject
  page id + grade) and ingest it like an upload, TOC extraction included.
- `POST /jobs/batch` — fleet launch: fan out one job per lesson of a `toc_ready` book
  (skipping/adopting lessons that already have jobs). `GET /jobs/batches`,
  `GET /jobs/batches/{id}`, and `GET /jobs/batches/{id}/jobs` serve the funnel rollups and
  the per-lesson drill-in. *(Registration order matters: these static routes are registered
  before the dynamic `/jobs/{job_id}`, or FastAPI would parse "batches" as a job id.)*
- `GET /workers` — fleet liveness: every registered worker plus a derived online/offline flag.
- `GET /notion/grades` / `GET /notion/grades/{id}/subjects` — the Notion pickers that feed
  the from-notion flow and the fleet launcher.

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
- `preview` / `job` → render each finished phase's **markdown** (via the `RichText`
  component) plus any validation warnings. (Older interactive renderers under `components/`
  predate the markdown-per-phase flip.)
- `usage` → the per-provider consumption dashboard.
- `fleet` → the launch page: prepare/launch a Notion subject as a batch, plus an
  `OnlineStrip` worker-liveness line.
- `monitor` → the monitoring page: batch funnel bars with per-lesson drill-in
  (cancel/retry/open) and worker PC liveness cards. Polls `/jobs/batches*` + `/workers`
  every ~3.5s (components live in `components/fleet/`; moved off `/fleet` in chunk-3).

`lib/api.ts` is the typed client, `lib/types.ts` mirrors the backend schemas.

> ⚠️ The console now renders each phase's **markdown** (`output_md`) rather than bespoke
> interactive widgets. Some renderers under `components/` (`memory-sprint`, `reading`,
> `adaptive-quiz`) predate that flip and map to phases the backend no longer produces — they
> are not on the live render path. When this doc and the live `web/` disagree, the backend's
> actual `phase_outputs.output_md` (see §4) is the source of truth.

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
- **TOC extraction scans both ends of the PDF** — the front pages *and* the last ~15 pages —
  because some Uzbek textbooks print their "Mundarija" (contents) at the back. It also
  glyph-decodes broken font subsets. Very large or scanned/OCR-less books can still come back
  empty; you then add the section by hand.

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

1. **Don't add LLM SDK calls outside `app/services/api_transport.py`.** The CLI router
   (`agent.py`) is for `transport=cli` — no SDK calls there. The `google-genai` and
   `anthropic` SDKs now live in `api_transport.py` for `transport=api` only; don't
   introduce SDK usage anywhere else.
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
| Change the flow or which game a subject gets | `app/services/flows.py` (`flow_for`, `SUBJECT_GAME`, `PHASE_DEPS`) |
| Add/remove a selectable model | `app/services/agent_models.py` (`MODEL_MANIFEST`) |
| Change how a CLI is invoked or parsed | `app/services/providers/<cli>.py` |
| Touch the spawn/usage logic | `app/services/agent.py` |
| Add an API endpoint | `app/api/v1/jobs.py` or `books.py` |
| Change what a phase outputs | `prompts/_general/<phase>.md` (there's no assembly step) |
| Edit what the AI is told to do per phase | `prompts/_general/<phase>.md` (all subjects) |
| Change language behavior (Uzbek / English-target) | `app/services/prompts.py` (`LANGUAGE_RULES`, `get_prompt`) |
| Tweak queue/worker/timeout behavior | `app/config.py` + `app/services/worker.py` |
| Understand the DB schema / queue / clocks in depth | `docs/DATABASE.md` |
| Touch batches / fleet launch / rollups | `app/repositories/batches.py` + `app/api/v1/batch.py` |
| Touch worker liveness / the registry | `app/repositories/workers.py` + `app/api/v1/workers.py` |
| Change the fleet dashboard | `web/src/routes/fleet.tsx` + `web/src/components/fleet/` |
| Set up a new worker PC | `docs/fleet/worker-pc-setup.md` |
| See the project's terse rules | `CLAUDE.md` |
| Read the running worklog/history | `docs/memory/MASTER_MEMORY.md` |
