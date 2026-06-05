# Fetch From Notion — alternate textbook source for the generation spawn

**Status:** Design approved — ready for writing-plans.
**Date:** 2026-06-05
**Branch:** Nggaev-v2

## Goal

Add a second way to start a generation session: instead of uploading a PDF, **fetch a textbook that's already attached to a Notion subject page**. The New Session screen offers a source choice — **Fetch From Notion** or **Upload a Book**. Upload is unchanged. Fetch walks grade → subject (Uzbek-medium only for v1), downloads the attached textbook PDF, and joins the existing upload→TOC→generate pipeline from the same point an upload does.

## Background / the real structure (crawled 2026-06-04)

Verified live against the workspace. The Notion `Lessons` tree is:

```
Lessons (root, id 2c1998381c768063bc43c84d59c0abf3)
└─ "{N} Grade"  (12: 1–11 + "Rules")
   ├─ "N - sinf"   ← Uzbek-medium
   └─ "N - класс"  ← Russian-medium
        └─ ~20 subject pages  ← textbook PDF attached as a file block on the subject page
```

- Each grade has an Uzbek (`N - sinf`) and a Russian (`N - класс`) child page; both are populated with textbooks (grade-9 `класс` had 18 subjects with attachments).
- A subject page has the textbook as a `file`/`pdf`/`embed` block; some have none (e.g. Jismoniy tarbiya, kelajak soati), some have multiple (e.g. "Student book + workbook").
- ~20 subjects per page, but only **7 map to an app pipeline** (`flows.SUPPORTED_SUBJECTS`): Algebra→math-algebra, Geometriya→geometriya-g7-11, Fizika→physics, Kimyo→kimyo-g7-11, Biologiya→biology, Ingliz tili→english, Jahon tarixi + O'zbekiston tarixi→history.
- Titles are messy: trailing `\n`/spaces, embedded notes (`Ingliz tili  1-st version missing`). `Matematika` (lower-grade math) is **not** the app's `math-algebra`.

The existing upload path (`app/api/v1/books.py::upload_book`, lines 44-90) is **inline handler code**: dedup by sha → `books_repo.create` → write `var/books/<id>/source.pdf` → `toc_extractor.run(book_id, pdf_path, subject)`. From `toc_extractor.run` onward (TOC extraction → theme/lesson pick → generate) everything is shared and unchanged.

## Decisions locked (during brainstorm)

1. **Two entry options**: Fetch From Notion | Upload a Book. Upload unchanged.
2. **sinf-only for v1** — only the Uzbek `N - sinf` branch is fetchable. `класс` is **hidden** (Russian generation is a separate future effort; the whole prompt/language stack is Uzbek + English-L2 only).
3. **Keep the 7 supported subjects.** The subject list shows **every subject with a textbook**; the 7 mapped ones are selectable, the rest are shown **disabled** ("unsupported for now"). (Prompts are already subject-agnostic — the limit is the `flows` registration gate `SUBJECTS` + `SUBJECT_GAME`, not a prompt limitation — but opening more subjects is out of scope here.)
4. **No-textbook subjects** → shown **disabled** with a "no textbook" hint (not hidden — hiding reads as a bug).
5. **Oversized books** → **reject >20 MB with a clear message**, no dead book row. (Gemini's TOC extraction rejects >20 MB per CLAUDE.md:122; ~1 in 4 real textbooks exceeds it, e.g. the 26.4 MB algebra.) Subset-TOC / auto-shrink are logged follow-ons.
6. **Reuse = extract + share, not verbatim.** Pull the inline upload logic into a shared `ingest_pdf(...)`; both `upload` and `from-notion` call it. **Upload must be re-tested.**
7. **Grades with zero supported subjects (1–6)** are greyed in the grade picker to avoid dead-end clicks.

## Architecture

"Fetch From Notion" is an **alternate source for the PDF bytes** plus a navigation layer. No change to the book/TOC/generate pipeline.

### Backend — shared ingest helper (refactor)

Extract the inline body of `upload_book` into:

```
async def ingest_pdf(session, *, body: bytes, subject: str, grade: str | None,
                     filename: str) -> BookOut
```

It does exactly what the handler does today: validate subject ∈ SUPPORTED_SUBJECTS, size check, sha dedup (`find_ready_by_hash`), `books_repo.create`, write `var/books/<id>/source.pdf`, kick `toc_extractor.run`. `upload_book` becomes a thin wrapper that reads the `UploadFile` and calls `ingest_pdf`. The from-notion endpoint downloads bytes then calls the same helper. **Re-test the existing upload behavior unchanged.**

### Backend — new `app/services/notion_fetch.py` (pure-ish, wraps NotionClientWrapper)

- `list_grades(client) -> list[GradeRef]` — `get_child_pages(notion_lessons_root)`; return grade pages (title like `"{N} Grade"`, excluding `"Rules"`).
- `list_subjects(client, grade_page_id) -> list[SubjectRef]` — find the `N - sinf` child (Uzbek; ignore `класс`); for each subject child: detect a textbook (`_first_pdf_block`), map title→app key (`_map_subject`), return `{notion_title, page_id, app_subject | None, has_textbook}`.
- `download_textbook(client, subject_page_id) -> tuple[bytes, str]` — `_first_pdf_block` → resolve its URL by block type (below) → httpx download immediately (signed URLs expire ~1 h) → `(bytes, filename)`. Raise a typed error if no block / size > 20 MB.
- `_first_pdf_block(client, page_id)` — first `file`/`pdf` block **in page order** whose filename ends `.pdf` (note: prefer `darslik`/textbook over `ish daftari`/workbook is a logged refinement, not v1). URL shapes to enumerate: `file.file.url` (signed, expiring), `file.external.url`, `pdf.file.url`, `pdf.external.url`, `embed.url`.
- `_map_subject(title) -> str | None` — fold the title (reuse the `notion_archive._fold` style: lowercase + apostrophe strip), then **longest-keyword-first** substring match against:
  `geometriya→geometriya-g7-11`, `algebra→math-algebra`, `biolog→biology`, `kimyo→kimyo-g7-11`, `fizika→physics`, `ingliz→english`, `jahon tarixi→history`, `ozbekiston tarixi→history`, `tarix→history`. **`matematika` is NOT a key** (≠ math-algebra). Longest-keyword-first resolves a double-hit (e.g. a title containing both "algebra" and "geometriya" → geometriya by length, but such combined pages are not expected; precedence is defined, not relied upon).
- Config: new `settings.notion_lessons_root` (the Lessons page id). `_TOC_MAX_MB = 20` (the Gemini TOC ceiling, distinct from `max_file_mb = 50`).

### Backend — endpoints (`app/api/v1/notion.py`, mounted under `/api/v1`)

- `GET /notion/grades` → `[{title, page_id, has_supported_subjects}]` (read-only; for the grade picker; `has_supported_subjects` drives greying).
- `GET /notion/grades/{grade_page_id}/subjects` → `[{notion_title, page_id, app_subject|null, has_textbook}]` (the subject list).
- `POST /books/from-notion {subject_page_id, grade}` → resolve the subject page, `_map_subject` its title (must be a supported key, else 422), `download_textbook` (422 if >20 MB or no PDF), then `ingest_pdf(...)` → returns the same `BookOut` as upload. `grade` is passed from the wizard (it was already picked in step 1) — no ancestor crawl needed.

### Frontend (`web/`)

- New Session gets a **source chooser** (two cards: Fetch From Notion | Upload a Book).
- Upload → today's page, unchanged.
- Fetch → step 1 grade select (`GET /notion/grades`; grades with no supported subjects greyed) → step 2 subject list (`GET …/subjects`; supported+textbook selectable, others disabled with reason: "unsupported" / "no textbook") → on select, `POST /books/from-notion` → navigate to the book's existing TOC page → pick lesson → generate (unchanged).
- Oversize/auth/no-PDF errors from the endpoint render inline with a fall-back-to-Upload hint.

## Data flow

```
source chooser ──Fetch──▶ GET /notion/grades ──▶ pick grade
   ──▶ GET /notion/grades/{id}/subjects ──▶ pick a SUPPORTED+textbook subject
   ──▶ POST /books/from-notion {subject_page_id, grade}
        ├─ resolve page → _map_subject(title) → app subject (422 if unsupported)
        ├─ download_textbook → bytes  (422 if >20MB or no PDF block)
        └─ ingest_pdf(body, subject, grade, filename)  [SAME path as upload]
   ──▶ BookOut ──▶ frontend → existing /books/{id} TOC view ──▶ generate (unchanged)
```

## Error handling

- **Notion not configured / token not shared with `notion_lessons_root`** → `GET /notion/grades` returns a clean typed error → wizard shows "Notion browsing unavailable — upload a book instead." (The archive token has subject-page access; the Lessons subtree may need separate sharing.)
- **Oversize (>20 MB)** → 422 with the size and a "shrink + upload manually" message; **no book row created**.
- **No PDF block on the subject page** → the subject is shown disabled ("no textbook"); the endpoint also 422s defensively.
- **Expired signed URL** → download happens immediately after fetching the block; on a rare expiry, re-fetch the block once.
- **sha dedup** → a previously-fetched/uploaded identical book is reused (existing `find_ready_by_hash`), so re-fetching is idempotent.

## Out of scope

- **Russian (`класс`) generation** — needs a Russian language contract (sibling of WS5); hidden in v1.
- **Subjects beyond the 7** — prompts could handle them (config gate only), but not opened here.
- **Subset-TOC / auto-shrink for >20 MB** — logged follow-on; v1 rejects.
- **Workbook-vs-textbook preference** when multiple PDFs — v1 takes the first PDF in page order.
- **Notion anchor auto-resolve** (the separate WISHLIST item) — `notion_lessons_root` is a single configured id here, not crawled-from-root discovery.

## Testing strategy

- **Unit `tests/services/test_notion_fetch.py`** (DB-free, stubbed client):
  - `_map_subject`: the 7 mappings; messy titles (`"Ingliz tili  1-st version missing"`→english, `"Matematika\n"`→None, `"O‘zbekiston tarixi"`→history); unmatched→None; longest-keyword precedence.
  - `_first_pdf_block`: picks the first `.pdf` `file`/`pdf` block in order; URL extraction per block type (`file.file.url`, `file.external.url`, `pdf.file.url`, `embed.url`); raises on none.
  - `list_subjects`: stubbed `get_child_pages` (sinf child found, класс ignored) → correct `app_subject`/`has_textbook` flags.
  - `download_textbook`: size > 20 MB → typed reject (no ingest).
- **Integration `tests/api/test_from_notion.py`**: `POST /books/from-notion` with a mocked `download_textbook` → calls `ingest_pdf` → `BookOut`; unsupported subject → 422; oversize → 422.
- **Refactor safety**: existing `upload_book` tests stay green (the `ingest_pdf` extraction is behavior-preserving); add a direct `ingest_pdf` unit test.
- **Acceptance (live smoke)**: fetch a real grade-9 `sinf` Kimyo (≤20 MB) end-to-end → book created, TOC extracted, lands on the theme view. Confirm a >20 MB subject (algebra) is rejected with the message and creates no book row.

## Risks / notes

- **`notion_lessons_root` sharing** is a deploy precondition; the degrade path keeps the app usable if absent.
- **Title→subject mapping is heuristic** — folded-substring on human-entered titles; new/renamed Notion titles that drop a keyword fall to "unsupported" (safe, visible) rather than mis-mapping.
- **The 20 MB ceiling is a real product limit** for fetch until subset-TOC/shrink lands; ~1 in 4 textbooks is affected. Surface it clearly; it is the top follow-on.
- **`ingest_pdf` refactor touches the upload path** — low risk (pure extraction) but explicitly re-tested in the plan.
