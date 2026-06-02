# Notion Content Archive — Design Spec

**Status:** Design (brainstormed 2026-06-02). Ready for an implementation plan.
**Goal:** When a homework job finishes, automatically file it into a **Notion database** as the content archive / system-of-record — one row per finished homework, filterable by subject/section/date/provider.
**Reference:** the proven `tools/notion/` package in `s1gmamale1/Notion---Video-Lesson` (same "push-to-Notion-on-done" pattern). We **reuse its plumbing** (client, file-upload flow, chunking/limits) and **replace its page-tree structure with a real database**.

---

## 1. Decisions (locked in brainstorming)

1. **Purpose:** content archive / CMS — a searchable Notion **database**, one row per finished job. (Not a review surface, not delivery, not an ops board.)
2. **Trigger:** **auto on job-done**, as a post-assembly step inside the pipeline. **Best-effort: a Notion failure must NEVER fail or block the homework job.** Retry-able.
3. **Tooling:** official **`notion-client`** SDK + raw **`httpx`** for file uploads (the reference's proven combo). Notion is a data *sink*, not an LLM — this does not violate CLAUDE.md's "no-SDK / CLI-only" rule (that rule is about LLM providers). It is a new dependency + a new secret.
4. **Structure:** a Notion **database with typed properties** (NOT the reference's hardcoded page-tree + title-matching). DB is **pre-created by the owner**; we take its ID from config and write rows. We document the required property schema.
5. **Body content (chosen — vetoable on review): Option A — attach the artifacts.** Each row's page body holds the finished packet as **attached files** (`homework.md` + the structured content JSON) — lossless, exact, robust, which is what a system-of-record needs. A short header (title + a couple of summary lines) is added for browsability. **Native Notion-block rendering of the packet (Option B) is explicitly deferred** (lossy on SVG/tables, more fragile) — the reference's markdown→blocks converter is liftable later if wanted.

---

## 2. Non-goals (explicit)

- No native-block rendering of the packet body (deferred — files are the record).
- No per-phase sub-pages / page hierarchy (the reference's approach — rejected).
- No two-way sync, no editing-in-Notion-flows-back, no delivery/publishing semantics.
- No backfill of historical jobs in v1 (the trigger is forward-only on new completions; a batch backfill can reuse the same `archive_job` later).
- No change to generation, schemas, the CLI router, or the frontend.

---

## 3. Architecture

### 3.1 New module — `app/services/notion_archive.py`
A small, self-contained unit with one public entry point:

```
archive_job(job) -> ArchiveResult   # best-effort; never raises into the caller
```

Internals (cribbed from the reference `tools/notion/client.py`):
- `notion_client.Client(auth=settings.notion_api_key)` for DB query + page create.
- raw `httpx` for the **3-step file upload**: `POST /v1/file_uploads` (create) → `POST /v1/file_uploads/{id}/send` (multipart) → reference it in a `file` block (`{"type":"file_upload","file_upload":{"id":...}}`). Header `Notion-Version: 2022-06-28`, `Authorization: Bearer <key>`.
- **Liftable constants/limits:** ≥0.35s between requests (~3 req/s), ≤100 blocks/append, ≤2000 chars/rich-text segment, ~20 MB file cap (our `.md`/`.json` are far under — no zip/split needed).

### 3.2 Config (`app/config.py` + `.env`)
- `notion_enabled: bool = False` — master switch. **When False, `archive_job` is a no-op** (so dev/CI without a token just skips silently). Env `NOTION_ENABLED`.
- `notion_api_key: str | None = None` — env `NOTION_API_KEY`.
- `notion_database_id: str | None = None` — env `NOTION_DATABASE_ID` (the owner-created archive DB).
- Guard: if `notion_enabled` but key/db missing → log a warning once and no-op (never crash).

### 3.3 The job-done hook (`app/services/pipeline.py`)
- After assembly succeeds and the job is marked `done` (right after `set_assembled_md` + the JSON-column setters, end of `pipeline.run`), call:
  ```python
  try:
      await notion_archive.archive_job(job)
  except Exception:
      log.warning("notion archive failed (non-fatal)", ...)   # NEVER re-raise
  ```
- The job's success/`done` status is committed **before** (or independently of) the Notion call, so archiving is strictly additive.
- *(Exact insertion point + how `job` (with its JSON columns + assembled_md) is in scope to be confirmed against `pipeline.run` at plan time.)*

### 3.4 Idempotency / retry
- New nullable column on `homework_jobs`: **`notion_archived_at: datetime | None`** (+ alembic migration). Set on successful archive.
- `archive_job` is a **no-op if `notion_archived_at` is already set** → safe to call on re-runs/retries; no duplicate rows.
- Dedup safety net: before creating, query the DB for a page whose **`JobId`** property == `str(job.id)`; if found, skip + stamp. (Notion has no upsert.)
- Failed archives leave `notion_archived_at` NULL → a future optional batch sweep (`archive all done jobs where notion_archived_at IS NULL`) can retry. (Sweep itself is out of v1 scope but the column enables it.)

---

## 4. Notion database schema (owner pre-creates; we document + validate)

| Property | Type | Source |
|---|---|---|
| **Name** (title) | title | `"{subject} — {chapter} §{section}"` |
| Subject | select | `job.subject` |
| Chapter | rich_text | from the section's chapter |
| Section | rich_text | section number + title |
| Provider | select | `job.provider` |
| Model | rich_text | `job.model` (or default) |
| Generated | date | job completion time |
| JobId | rich_text | `str(job.id)` — dedup key |
| Status | select | `"done"` |

Page **body**: a 2–3 block header (title + "Generated {date} · {provider}") then the attached files: **`homework.md`** (from `assembled_md`) and **`content.json`** (the structured content payload — the same JSON artifact the download endpoint serializes; exact shape confirmed at plan time).

---

## 5. Error handling

- **Never fatal:** all Notion I/O wrapped so the homework job is unaffected. The job is already `done` and downloadable regardless.
- Network/rate-limit errors → log + leave `notion_archived_at` NULL (retry later).
- Missing config while `notion_enabled` → warn-once + no-op.
- Partial failure (page created but file upload failed) → still stamp? **No** — only stamp `notion_archived_at` after the row + both files are fully attached, so a retry can complete it. (Accept a small risk of a duplicate row on retry; the `JobId` dedup query mitigates.)

---

## 6. Testing & acceptance

- **Unit (mock `notion_client` + `httpx`):** property mapping from a `Job` fixture; dedup-query path (existing JobId → skip); the `notion_enabled=False` no-op; the **failure-is-non-fatal** guard (archive raises → `pipeline.run` still completes the job `done`).
- **Migration:** `notion_archived_at` column applies cleanly (offline check; live needs Docker).
- **No live Notion in CI** (needs a token/DB). **One manual live smoke:** set the 3 env vars against a scratch Notion DB, run one job, confirm a row appears with correct properties + both files attached, and re-running the job creates **no** duplicate.
- Full suite stays green.

---

## 7. Open items to confirm at plan time
- Exact shape/name of the structured JSON artifact to attach (is there a real `content.json` serializer, or do we assemble it from the `*_json` columns the way the download endpoint does?).
- Exact insertion point in `pipeline.run` for the hook (after which commit).
- Whether the owner wants us to **auto-create** the DB (via `notion-create-database`) on first run vs require a pre-made DB id (spec assumes pre-made — simpler, owner owns the schema).

---

## 8. Reusable references (from `Notion---Video-Lesson/tools/notion/`)
- `client.py` — SDK init, the raw-`httpx` 3-step file upload, rate limiting, `make_file_upload_block`.
- `content_writer.py` — markdown→blocks + 2000-char chunking (only if Option B is ever revived).
- Gotchas already learned there: 100-block/req, 2000-char/segment, ~3 req/s, ~20 MB file cap, idempotency by key.
