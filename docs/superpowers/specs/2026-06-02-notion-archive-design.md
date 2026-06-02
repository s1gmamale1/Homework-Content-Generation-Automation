# Notion Integration — Design Spec (v2, loop-aware)

**Status:** Design (brainstormed + live-verified against the real Notion workspace, 2026-06-02). Ready for an implementation plan.
**Supersedes** the v1 "create a Notion database, row per job" draft — **that was wrong** (see §2). This is a rewrite after inspecting the live tree via the Notion API.
**Goal:** Connect the homework generator to the **existing Notion lesson tree** in both directions — **push** finished homework into the right `Homework` page (Phase 1), and eventually **pull** the source textbook *from* Notion to drive generation (Phase 2, the automation loop).
**Reference impl to crib:** `s1gmamale1/Notion---Video-Lesson` `tools/notion/` (Python; `notion-client` + raw `httpx` for file uploads; idempotent create-by-title; rate/limit handling). It used the **same tree** and pushed media into these sub-pages on job-done.

---

## 1. Live findings (verified via Notion API, read-only, 2026-06-02)

The Notion tree (manually built):
```
Class A Creative ▸ Class A Education (ClassAI) ▸ Lessons
  ▸ 1–11 Grade
    ▸ "N - sinf" (Uzbek)  /  "N - класс" (Russian)            ← language split
      ▸ Subject (Ona tili, Algebra, Geometriya, Fizika, Kimyo, Biologiya, Ingliz tili, Jahon/O‘zbekiston tarixi, …)
        ▸ Lesson "N-dars «title» …… <page>"
          ▸ 12 sub-pages: Text Original · Text Refined AI · Video · Images · Audio · Prompt ·
            Lesson files PPT,PDF · Final Video · Quizlet · **Homework** · Lesson Plan · Get ready…
```

- **`notion-fetch` works both directions:** any page returns its full **ancestor-path** (→ grade/language/subject/lesson) AND, for a lesson, the **child page list with IDs** (incl. `Homework`). So the tree is fully crawlable and **find-or-create is feasible**.
- **The tree is PARTIAL / built per-subject.** `Ona tili` is fully built (25 lessons × 12 sub-pages). **`Geometriya` (one of OUR subjects) has only the source PDF attached — no lessons, no Homework pages.** → the app **must find-or-create** the lesson + Homework pages; it cannot assume they exist.
- **The subject page hosts the textbook PDF.** `Geometriya/8-sinf` has `8-sinf_Geometriya_2019_(elekton_darslikbot).pdf` attached — the **same file** as our book's `original_filename`. So our book ↔ Notion subject page is a confident match, and the **subject page's location gives grade + subject for free** (via ancestor-path).
- **Our addressing data is thin:** no `grade` or clean lesson-number column. Grade lives **inside `books.original_filename`** (`8-sinf`); `subject` is a range (`geometriya-g7-11`); `toc_entry` has `chapter_number`/`section_number` (`1.1`) + `section_title`. **Numbering schemes differ** from Notion's `N-dars` → matching a lesson by number is unreliable; title is brittle (Uzbek + `……3` suffix).
- **Notion file attachments** are exposed as **expiring S3 URLs** via the REST API (the MCP view shows an internal ref; the real `file` object carries a temporary `url`). Verify the exact retrieval at build time.
- **Access note:** all of the above used the **assistant session's** Notion MCP (your OAuth connection), **read-only**. **The app itself has ZERO Notion access today** — the entire integration must be built.

---

## 2. Decisions (locked)

1. **Not a database.** Write into the **existing page tree** (the v1 "database/row-per-job" idea is dropped).
2. **Find-or-create.** The app creates the **lesson page + `Homework` sub-page** if missing (just those two — not the full 12-sub-page template; media pages stay the video tool's job), then writes into Homework. Idempotent by stored page ID + normalized title.
3. **Loop-aware, built in two phases:**
   - **Phase 1 — PUSH** (this spec's core): on job-done, find-or-create + write homework into the matched `Homework` page. Best-effort, **never fails the job**.
   - **Phase 2 — PULL** (the automation transform): fetch the textbook PDF *from* the Notion subject page to source/trigger generation, closing the loop.
4. **App owns lesson titles.** Create lessons with a **deterministic title derived from the `toc_entry`** (e.g. `"{section_number} {section_title}"`) — do not try to match human `N-dars` formatting. For app-owned subjects, humans should not also hand-create lessons (dup risk).
5. **Tooling:** `notion-client` + `httpx` (cribbed from the reference). Notion is a data sink/source, **not an LLM** — does not violate the "no-SDK/CLI-only" rule (that's about LLM providers). New dependency + a secret.
6. **Body content:** since `Homework` is a content page, write the homework **rendered into blocks** (markdown→Notion blocks, the reference's converter) **and/or attach `homework.md` + `content.json`** as the lossless record. Respect Notion limits (≤100 blocks/req, ≤2000 chars/text segment, ≥0.35s between calls, ~20 MB file cap).

---

## 3. Addressing (the crux — now solved)

- **Anchor = the Notion SUBJECT page.**
  - **Phase 2 (book pulled from Notion):** we already hold the subject-page ID → anchor for free; grade+subject come from its ancestor-path. No mapping needed.
  - **Phase 1 standalone (book uploaded manually):** resolve `{subject, grade} → subject-page ID` via a small config map **or** by matching the shared PDF `original_filename` against subject-page attachments. Grade parsed from the filename (`8-sinf`).
- **Under the subject:** `find_or_create_lesson(subject_page, deterministic_title)`.
- **Under the lesson:** `find_or_create_subpage(lesson_page, "Homework")`.
- **Idempotency (robust):** after first creation, **store the Homework page ID on the `toc_entry`** (`notion_homework_page_id`) and stamp **`homework_jobs.notion_archived_at`**. Re-runs write straight to the stored ID — no re-matching, no duplicates.

---

## 4. Backend changes

### Phase 1 — push (core)
- **New module** `app/services/notion_archive.py`: `notion-client` + `httpx`; public `archive_job(job)` (best-effort, never raises into the pipeline). Internals cribbed from the reference: 3-step file upload (`/v1/file_uploads` → `/send` → `file_upload` block), markdown→blocks, rate limiting, find-or-create-by-title.
- **Config** (`app/config.py` + `.env`): `notion_enabled: bool = False` (master switch — when False, `archive_job` is a no-op so dev/CI without a token just skip); `notion_api_key`; the subject-anchor map (or filename-match strategy). Guard: enabled-but-unconfigured → warn-once + no-op.
- **Hook** in `pipeline.run`, after assembly + job marked `done`:
  ```python
  try: await notion_archive.archive_job(job)
  except Exception: log.warning("notion archive failed (non-fatal)")   # never re-raise
  ```
- **Migrations:** add `homework_jobs.notion_archived_at` (nullable) + `toc_entries.notion_homework_page_id` (nullable). The NULL state enables a future retry/backfill sweep.

### Phase 2 — pull / ingestion transform (the "fetch textbook from Notion" change)
- **New ingestion path:** instead of (or alongside) manual PDF upload, **fetch the textbook from a Notion subject page** — read the subject page → get the attachment's expiring download URL → download → save to **`var/books/<book_id>/source.pdf`** (the path the pipeline already reads) → create the `books` row, inferring **subject + grade from the subject page's ancestor-path**.
- **Downstream pipeline is unchanged** — extract (gemini, on-disk PDF) → TOC → generate per section → Phase 1 writes each homework back **under the same subject page** (anchor already known). Loop closed.
- **The existing manual upload endpoint stays** (manual remains supported); Notion-fetch is an additional source.
- **Automation trigger/policy** (which subjects/lessons to ingest+generate — e.g. "subject pages that have a PDF but missing/empty Homework pages") is a **Phase-2 decision, deferred.**

---

## 5. Error handling
- All Notion I/O best-effort; the homework job is already `done` and downloadable regardless — archiving is strictly additive.
- Only stamp `notion_archived_at` after the lesson + Homework page exist AND content is fully written (so a retry can finish a partial); the stored-ID + title dedup guards against a duplicate row on retry.
- Rate-limit/network errors → log, leave NULL, retry later. Missing config while enabled → warn-once + no-op.

---

## 6. Testing & acceptance
- **Unit (mock `notion_client` + `httpx`):** subject-anchor resolution; find-or-create (existing → reuse, missing → create); store-ID-back + skip-if-stamped; **failure-is-non-fatal** (archive raises → `pipeline.run` still completes `done`); body mapping; limit chunking.
- **Phase 2 unit:** mock subject-page fetch → file-URL → download → save-to-path; subject/grade inferred from ancestor-path.
- **Migrations** apply (offline; live needs Docker).
- **No live Notion in CI.** **Manual live smoke** against a scratch subject page: run a job, confirm a lesson + Homework page get created and filled; re-run → **no duplicate** (stored ID reused). Phase 2 smoke: point at a subject page with a PDF → confirm download + book row + generation.
- Full suite stays green.

---

## 7. Non-goals
- No Notion database; no two-way *content* sync (Notion edits don't flow back); no recreating the full 12-sub-page template (only `Homework`); media sub-pages (Video/Images/Final Video) remain the video tool's job; Phase-2 automation trigger policy is out of this spec.

---

## 8. Open items to confirm at plan time
- Phase-1-standalone subject anchor: **config `{subject,grade}→page-ID` map vs filename-match** — pick one.
- Deterministic **lesson-title format** (`"{section_number} {section_title}"`?).
- **Grade source:** keep parsing `original_filename`, or add a real `grade` field to `books`/`toc_entries`.
- Exact **`content.json`** artifact shape to attach (real serializer vs assemble from `*_json` columns, like the download endpoint).
- **Body:** rendered-blocks vs attach-files vs both (reference has converter + upload for all three).
- **Phase 2:** the Notion file **download-URL retrieval** path; the **ingestion trigger/policy**.

---

## 9. Reusable references (`Notion---Video-Lesson/tools/notion/`)
`client.py` (SDK init, raw-`httpx` 3-step file upload, rate limiting, `make_file_upload_block`) · `content_writer.py` (markdown→blocks, 2000-char chunking, file-block fallback) · `page_creator.py` (idempotent create-by-title) · `config.py` (grade-root page-ID maps — the anchor pattern). Limits learned there: 100 blocks/req, 2000 chars/segment, ~3 req/s, ~20 MB file cap.
