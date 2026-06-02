# Edu-Homework — Thin-Slice Design

**Date:** 2026-04-28
**Status:** Approved (pending final review)
**Slice:** End-to-end thin slice — upload one PDF → extract TOC → run pipeline on one selected section → download assembled `.md`.

---

## 1. Goals & non-goals

### Goal
Stand up a microservice that, given a curriculum PDF and a selected subject, extracts a Table of Contents via Gemini, lets a caller pick one section, runs the subject-specific homework prompt pipeline (`prompts/<subject>/...`), and returns an assembled Markdown file.

### Non-goals (intentional, deferred)
- Real authentication / multi-tenancy (auth is a stub for v0).
- Persistent file storage of original PDFs (Gemini File API's ~48h TTL is enough).
- Real job queue / multi-worker deployment (single worker, `asyncio.create_task` background work).
- Phase-level retry or partial resume (whole-job restart only).
- Subject-specific extract prompts (one generic extract prompt for all 7 subjects).
- Conditional phases (e.g., biology-HARD skip-consolidation for single-concept lessons).
- Regenerate single phase without re-running the pipeline (`force: true` whole-job restart instead).
- Cost dashboard / token-accounting UI.
- Webhook notification on job completion (SSE + GET poll only).
- Rate limiting, structured logging, metrics, tracing.
- Frontend polish, design system, UI i18n.
- Multi-PDF books / page-range overrides.
- Soft delete / GDPR retention.
- **Tests of any kind** — logic is expected to churn during this phase. Verification is manual via the test UI.

### Operating constraints
- Microservice in a larger education system. The API contract is the product; UI is throwaway.
- All state is durable in Postgres. SSE streams replay from DB on reconnect.
- LLM provider: Google Gemini (file handling + prompt execution).
- Single LLM client; uploaded `file_uri` is reused across TOC extraction, lesson extraction, and every phase prompt.

---

## 2. User journey & decisions

| Decision | Choice |
|---|---|
| Slice scope | End-to-end (upload → TOC → one section → assembled `.md`) |
| Input format | Mixed PDFs (digital + scanned) — handled by Gemini File API |
| Subject routing | User picks subject from a fixed dropdown (one of the 7 `prompts/` directories) |
| Processing model | SSE streaming for both TOC extraction and pipeline execution |
| Frontend stack | Throwaway static HTML+JS in `frontend/`, mounted at `/ui`, calls `/api/v1/...` |
| API style | REST + SSE under `/api/v1/`, OpenAPI auto-generated |
| Auth (v0) | Stub `Depends(get_current_user)` — reads `X-User-Id` or returns fixed test user |
| DB engine | PostgreSQL + SQLAlchemy 2.0 async + Alembic + asyncpg |

User flow:
1. Open test UI, pick subject from dropdown, choose PDF, submit.
2. UI navigates to `/ui/book.html?id=<book_id>`. SSE stream pushes `status: extracting` then `toc_ready` with entries. UI renders the list.
3. User clicks an entry. UI POSTs `/api/v1/books/{id}/sections/{toc_entry_id}/generate`, navigates to `/ui/job.html?id=<job_id>`.
4. SSE stream pushes `phase_started` / `phase_completed` events as each phase runs. Final `job_completed` event reveals a download button.
5. Download returns the assembled `.md` as `text/markdown` attachment.

---

## 3. Architecture & module layout

```
edu-homework/
├── pyproject.toml          add: google-genai, sqlalchemy[asyncio], asyncpg, alembic,
│                           sse-starlette, pydantic-settings, python-multipart
├── alembic.ini
├── alembic/
│   ├── env.py              async migration env
│   └── versions/           generated migrations
├── docker-compose.yml      Postgres for dev
├── .env.example            DATABASE_URL, GEMINI_API_KEY, MODEL_NAME, MAX_FILE_MB
├── main.py                 FastAPI app: lifespan (DB warmup, prompt cache, orphan sweep),
│                           v1 router include, /health, StaticFiles mount on /ui
├── app/
│   ├── config.py           pydantic-settings (typed env)
│   ├── db.py               async engine, sessionmaker, get_session() dep
│   ├── auth.py             stub Depends(get_current_user)
│   ├── models/             SQLAlchemy 2.0 ORM (DB shape)
│   │   ├── base.py         DeclarativeBase + UUID PK + created_at/updated_at mixins
│   │   ├── book.py
│   │   ├── toc_entry.py
│   │   ├── homework_job.py
│   │   └── phase_output.py
│   ├── schemas/            Pydantic API contracts (separate from ORM)
│   │   ├── book.py
│   │   ├── toc.py
│   │   ├── job.py
│   │   └── events.py       SSE event payloads
│   ├── repositories/       data access — one query owner per table
│   │   ├── books.py
│   │   ├── toc_entries.py
│   │   ├── jobs.py
│   │   └── phase_outputs.py
│   ├── services/
│   │   ├── gemini.py       Gemini SDK wrapper
│   │   ├── prompts.py      load prompts/<subject>/*.md once at startup → in-mem dict
│   │   ├── flows.py        SUBJECT_FLOWS: per-subject phase sequences
│   │   ├── events_bus.py   in-process pub/sub: asyncio.Queue registry by resource_id
│   │   ├── toc_extractor.py background TOC extraction task
│   │   └── pipeline.py     phase orchestrator (writes phase_outputs, emits events)
│   └── api/v1/
│       ├── __init__.py     v1 APIRouter aggregator
│       ├── books.py        POST /books, GET /books/{id}, GET /books/{id}/toc/stream
│       ├── jobs.py         POST .../generate, GET /jobs/{id},
│       │                   GET /jobs/{id}/stream, GET /jobs/{id}/download
│       └── health.py       GET /health (DB ping)
├── frontend/               THROWAWAY — delete to remove UI
│   ├── index.html          upload form
│   ├── book.html           TOC list (SSE-driven)
│   ├── job.html            phase timeline + download
│   ├── app.css             Tailwind CDN
│   └── app.js              fetch + EventSource helpers
└── prompts/                (untouched curriculum prompts)
```

**Layer boundaries:**
- `models/` (DB) and `schemas/` (API) are **separate** — repositories return ORM rows; services convert to Pydantic before crossing API boundary. Stops DB-shape changes from leaking into the public contract.
- `repositories/` are the only places SQLAlchemy queries live. Services compose repositories.
- `services/` hold long-running work and emit events. They write to DB via repositories, never directly via session.
- Routes (`api/v1/`) are thin: parse request → call service → format response.

**Removing the UI later:** delete `frontend/`, remove the `app.mount("/ui", StaticFiles(...))` line in `main.py`. Backend untouched.

---

## 4. Data model

Four tables. Every state transition is durable; SSE replay reconstructs progress from `phase_outputs`. UUID PKs everywhere; `created_at`/`updated_at` on every row via mixin.

### `books`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | server-generated |
| `subject` | text | one of: biology, english, geometriya-g7-11, history, kimyo-g7-11, math-algebra, physics |
| `original_filename` | text | as uploaded |
| `content_sha256` | text | hash of file bytes — for dedup; indexed but not unique |
| `file_size_bytes` | bigint | |
| `gemini_file_uri` | text | e.g., `files/abc-xyz` |
| `gemini_file_expires_at` | timestamptz | ~48h after upload |
| `status` | text | `uploading` → `toc_extracting` → `toc_ready` / `failed` |
| `error_message` | text nullable | populated only on `failed` |
| `created_at`, `updated_at` | timestamptz | mixin |

### `toc_entries`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `book_id` | UUID FK → books | ON DELETE CASCADE |
| `chapter_number` | text nullable | "3" or "Bob 3" |
| `chapter_title` | text nullable | |
| `section_number` | text | "§12" |
| `section_title` | text | "Fotosintez" |
| `page_start`, `page_end` | int nullable | anchors for cheaper text extraction |
| `order_index` | int | preserves TOC order |
| `created_at` | timestamptz | |

Index: `(book_id, order_index)`.

### `homework_jobs`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `book_id` | UUID FK → books | |
| `toc_entry_id` | UUID FK → toc_entries | |
| `subject` | text | denormalized; immutable per job |
| `difficulty` | text nullable | `easy` / `hard`, set after classify phase |
| `status` | text | `pending` → `running` → `done` / `failed` |
| `current_phase` | text nullable | e.g., "memory-sprint" |
| `error_message` | text nullable | |
| `assembled_md` | text nullable | only set on `done` |
| `created_at`, `started_at`, `completed_at` | timestamptz | |

Indexes: `(book_id, toc_entry_id)` for idempotency lookup; `status` for monitoring.

### `phase_outputs`
| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `job_id` | UUID FK → homework_jobs | ON DELETE CASCADE |
| `phase_name` | text | "extract", "classify", "preview-hard", … |
| `phase_order` | int | deterministic position in pipeline (0-indexed) |
| `prompt_hash` | text | SHA-256 of prompt source — reproducibility |
| `model_name` | text | e.g., "gemini-2.0-flash-exp" |
| `output_md` | text nullable | null while running |
| `tokens_input`, `tokens_output` | int nullable | cost accounting |
| `status` | text | `pending` → `running` → `done` / `failed` |
| `error_message` | text nullable | |
| `started_at`, `completed_at` | timestamptz nullable | |

Unique index: `(job_id, phase_order)` — one row per phase per job.

**Idempotency:**
- `POST /books` dedups by `(content_sha256, subject)` only when an existing book is in `toc_ready`. Failed/extracting books are not reused so retry actually retries.
- `POST .../generate` dedups by `(book_id, toc_entry_id)`. Existing non-failed job → returns same `job_id` with `200`. `force: true` body field creates a new job regardless.

---

## 5. API surface

All under `/api/v1/`. OpenAPI auto-published at `/openapi.json`. `/docs` (Swagger UI) gated to dev via env flag. Auth stub wired into mutating routes.

### REST endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/health` | — | `{ "status": "ok", "db": "ok" }` |
| `POST` | `/api/v1/books` | `multipart/form-data`: `file` + `subject` | `201 { book_id, subject, status: "uploading" \| "toc_extracting", ... }` |
| `GET` | `/api/v1/books/{book_id}` | — | `200 { book_id, subject, status, toc?: [TOCEntry], error_message? }` |
| `GET` | `/api/v1/books/{book_id}/toc/stream` | — | `text/event-stream` |
| `POST` | `/api/v1/books/{book_id}/sections/{toc_entry_id}/generate` | `{ "force"?: false }` | `201 { job_id, status: "pending", ... }` (or `200` on idempotent return) |
| `GET` | `/api/v1/jobs/{job_id}` | — | `200 { job_id, status, difficulty?, current_phase?, phases: [...], assembled_md? }` |
| `GET` | `/api/v1/jobs/{job_id}/stream` | — | `text/event-stream` |
| `GET` | `/api/v1/jobs/{job_id}/download` | — | `200 text/markdown` + `Content-Disposition: attachment; filename="..."` (or `404` if not `done`) |

### SSE event types

**TOC stream** (`/books/{id}/toc/stream`):

| event | `data` shape |
|---|---|
| `status` | `{ "status": "uploading" \| "toc_extracting" }` |
| `toc_ready` | `{ "entries": [TOCEntry, ...] }` |
| `error` | `{ "message": "..." }` |

Stream closes after `toc_ready` or `error`.

**Job stream** (`/jobs/{id}/stream`):

| event | `data` shape |
|---|---|
| `phase_started` | `{ "phase_name", "phase_order" }` |
| `phase_completed` | `{ "phase_name", "phase_order", "output_md", "tokens_input", "tokens_output" }` |
| `difficulty_classified` | `{ "difficulty": "easy" \| "hard" }` |
| `job_completed` | `{ "job_id", "download_url" }` |
| `error` | `{ "phase_name?", "message" }` |

Stream closes after `job_completed` or `error`.

### SSE replay protocol

When a client connects to either stream:
1. The handler reads current state from the DB.
2. It emits *catch-up* events to bring the client to current state: e.g., `phase_completed` for every done phase ordered by `phase_order`, then `phase_started` for the running phase.
3. It attaches to the live in-process pub/sub queue and forwards events from the running task.
4. If the work has already finished by the time of connection, the catch-up step also emits the terminal event (`toc_ready` / `job_completed` / `error`) and closes the stream.

Result: refreshing the page or surviving a network drop both end at the same state with no client-side reconciliation logic.

### Background work

- Background work is started with `asyncio.create_task(...)` from the route handler — **not** FastAPI's `BackgroundTasks` (which run after response and can be cancelled).
- The pub/sub bus is a per-process registry of `asyncio.Queue` instances keyed by `book_id` / `job_id`. Single-worker deployment.
- On lifespan startup, a sweeper marks any `homework_jobs` left in `running` and `phase_outputs` left in `running`/`pending` as `failed` with `error_message="orphaned: worker restarted"`. Same for `books` left in `toc_extracting`. The user retries via re-upload or `force: true` regenerate.

---

## 6. Pipeline orchestrator

### Phase model

Every job runs an ordered phase sequence. Each phase = one row in `phase_outputs` + one Gemini call.

Three phase categories:
1. **`extract`** (phase_order=0) — non-creative: read the book pages for this section, extract topic + terms + processes + visuals as structured Markdown. Output is **input for every subsequent phase**, **not part of the assembled `.md`**.
2. **`classify`** (phase_order=1) — runs `prompts/<subject>/classify.md` against the extracted lesson context, returns `EASY` or `HARD`. Sets `homework_jobs.difficulty`. Skipped for subjects whose `flow.md` has no classify phase (e.g., history).
3. **Content phases** (phase_order=2..N) — the actual homework prompts (`preview-*`, `flashcards`, `memory-sprint`, …). Sequence depends on subject + difficulty.

### Per-subject phase sequences

Codified as data in `app/services/flows.py`, one entry per subject:

```python
SUBJECT_FLOWS = {
    "biology": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "history": {
        # History is always Hard mode — no Easy pipeline.
        # Canonical structure is 6 mandatory phases (preview, flashcards,
        # memory-sprint, game-breaks, final-challenge, reflection) + consolidation
        # as a conditional 7th (skip if single-concept). Per v0 design we run
        # consolidation unconditionally and rely on the prompt to self-emit a skip
        # marker when not applicable — see "Out of scope" in §1.
        "has_classify": False,
        "easy": [],
        "hard": ["preview", "flashcards", "memory-sprint", "game-breaks",
                 "consolidation", "final-challenge", "reflection"],
    },
    # english, geometriya-g7-11, kimyo-g7-11, math-algebra, physics
    # filled in during implementation by reading each subject's flow.md once and
    # committing the sequences as code.
}
```

### Prompt assembly per phase

The full prompt sent to Gemini for a content phase has three parts:

```
[SYSTEM]
<verbatim contents of prompts/<subject>/<phase>.md>

[USER]
## Lesson context
<output_md from the extract phase>

## Difficulty
EASY  (or HARD; omitted if subject has no classify)

## Prior phase outputs
### <phase_name>
<output_md>

### <phase_name>
<output_md>
...

[FILE]
file_uri = <book.gemini_file_uri>
```

**Prior outputs scope:** in v0, **every** earlier phase's output is included. Future optimization is per-phase dependency declarations.

### Extract phase prompt (subject-generic, in code)

```
You are reading the attached textbook. The lesson is "{section_title}"
(section {section_number}, pages {page_start}-{page_end}).

Extract all factual lesson content the textbook teaches on these pages.
Include: key terms with definitions, named processes/mechanisms with steps,
diagrams/visuals (describe them), worked examples, formulas,
organisms/structures with functions, historical references, experiments,
and comparison tables.

Output as structured Markdown. Be faithful to the source — do not invent.
```

### Execution loop (pseudocode)

```python
async def run_pipeline(job_id):
    job = await jobs_repo.get(job_id)
    book = await books_repo.get(job.book_id)
    section = await toc_repo.get(job.toc_entry_id)
    flow = SUBJECT_FLOWS[book.subject]

    sequence = ["extract"]
    if flow["has_classify"]:
        sequence.append("classify")
        # content phases get appended after classify resolves difficulty
    else:
        # no classify → subject runs its hard sequence by default
        # (e.g., history's easy list is empty by design)
        sequence.extend(flow["hard"])

    await jobs_repo.set_status(job_id, "running", started_at=now())
    difficulty = None

    phase_order = 0
    while phase_order < len(sequence):
        phase_name = sequence[phase_order]
        po = await phase_repo.create(job_id, phase_name, phase_order, prompt_hash, model_name)
        emit("phase_started", {phase_name, phase_order})
        await phase_repo.set_status(po.id, "running", started_at=now())

        try:
            output, in_tok, out_tok = await gemini.run_phase(
                phase_name, book.gemini_file_uri, section, prior_outputs, difficulty
            )
        except Exception as e:
            await phase_repo.fail(po.id, str(e))
            await jobs_repo.fail(job_id, f"{phase_name}: {e}")
            emit("error", {phase_name, message: str(e)})
            return

        await phase_repo.complete(po.id, output, in_tok, out_tok)
        emit("phase_completed", {phase_name, phase_order, output_md: output, ...})

        if phase_name == "classify":
            difficulty = parse_classify(output)        # "EASY" or "HARD"
            await jobs_repo.set_difficulty(job_id, difficulty)
            emit("difficulty_classified", {difficulty})
            sequence.extend(flow[difficulty.lower()])

        phase_order += 1

    assembled = assemble(job_id)
    await jobs_repo.complete(job_id, assembled, completed_at=now())
    emit("job_completed", {job_id, download_url: ...})
```

### Final assembly (non-LLM)

`assemble(job_id)` reads `phase_outputs` ordered by `phase_order`, **skips `extract` and `classify`** (their outputs are not part of the homework), concatenates the remaining phases' `output_md` under `## <Phase title>` headers, and stores the result in `homework_jobs.assembled_md`. Pure string ops.

### Failure model

- A phase failure marks the phase row `failed` and the job row `failed`. Subsequent phases do not run.
- No automatic retry in v0. Caller re-POSTs `/generate` with `"force": true` to start a new job.
- On app restart, the lifespan sweeper marks orphaned `running` work as `failed` with `error_message="orphaned: worker restarted"`. Caller retries.

---

## 7. Configuration

Environment variables (loaded by `pydantic-settings`):

| Var | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://user:pass@host:5432/edu_homework` |
| `GEMINI_API_KEY` | yes | — | from Google AI Studio |
| `GEMINI_MODEL` | no | `gemini-2.0-flash-exp` | exact model id |
| `MAX_FILE_MB` | no | `50` | reject uploads above this |
| `ENABLE_DOCS` | no | `false` | `true` exposes `/docs` Swagger UI |
| `ALLOW_ORIGINS` | no | `*` | CORS for the test UI |

A `.env.example` is committed; `.env` is gitignored.

---

## 8. Open items / known follow-ups

- Phase sequences for the 5 subjects beyond biology and history must be filled in `flows.py` during implementation (one read of each subject's `flow.md`).
- The 48h Gemini File API TTL means stale `gemini_file_uri`s will fail. v0 surfaces the error to the caller; v2 should detect and prompt re-upload.
- Multi-worker scale-out replaces the in-process `asyncio.Queue` registry with Redis pub/sub or Postgres `LISTEN/NOTIFY`. The `events_bus.py` interface stays the same.
- v2 should add an `Idempotency-Key` header pattern alongside the existing semantic dedup.
