# Prepare dialog: system-aware status + explicit TOC redo

**Problem (user, 2026-07-16):** the "Prepare a subject" dialog's chips describe Notion state only
(`TEXTBOOK READY`/`no textbook`) — an already-ingested-and-TOC-extracted subject looks identical
to a never-touched one; picking it silently dedups (books.py:86-90, sha+subject) with no way to
deliberately redo a TOC. Design reviewed by gatekeeper 2026-07-16 (7 corrections, all folded
below; identity model = mapping table, verified against code).

## Approach & key decisions

- **Identity: `book_notion_sources` mapping table** (NOT a column on books):
  `(book_id FK CASCADE, notion_page_id TEXT norm, notion_block_id TEXT norm, linked_at,
  UNIQUE(notion_page_id, notion_block_id))` — ids normalized via hyphen-strip+lower (the
  api-vs-config mismatch class from PR #97's gate). Rationale: two candidate PDFs can live on ONE
  part page (live G11-UZ Algebra), and SHA-dedup means one book is reachable from several Notion
  locations — page-id-only cannot represent either. **Upsert on EVERY `/from-notion` success,
  including dedup hits** (`ON CONFLICT (page, block) DO UPDATE SET book_id, linked_at`).
- **`books.toc_ready_at` timestamp** (nullable): stamped when extraction completes successfully /
  TOC accepted — `created_at` means ingestion started, not prepared. Both schema changes in ONE
  migration (number = next free at implementation time — **check the BE-16 lane's migrations
  first, collision risk**).
- **Server-side enrichment, not FE joins:** `GET /notion/grades/{id}/available-languages`
  (notion.py:38) enriches each part server-side via the mapping table: `book_id, book_status,
  toc_validation, toc_total, toc_ready_at, redo_blocked_by_jobs (count)` — the FE `/books` list
  is limit-capped (books.py:350) and must not be joined client-side.
- **Redo = `/toc/retry` extended, book id stable:** allowlist gains `toc_ready`; the
  blocking-jobs 409 becomes STRUCTURED (`{"error": "toc_retry_blocked_by_jobs", "count": N,
  "jobs": [{id, status} …≤20]}` — FE never parses prose); on redo, CLEAR stale
  `toc_validation`/`toc_validation_detail` (today a validation-disabled redo retains the old
  verdict) and null `toc_ready_at` until the re-run completes.
- **UX:** chips per subject/part: `NO TEXTBOOK` / `TEXTBOOK READY` (in Notion, unprepared) /
  `PREPARED · N lessons` + non-steady states `PREPARING` (toc_extracting) / `NEEDS REVIEW`
  (toc_review) / `FAILED` — reopening the dialog mid-flight must not lie. Selecting a PREPARED
  part shows the existing book (title, N lessons, toc_ready_at) with two actions: **Use existing**
  (NO mutation — navigate/focus the book) and **Redo TOC extraction** (destructive-styled,
  explicit "re-extracts from the PDF and REPLACES current TOC rows"; disabled with the structured
  reason when `redo_blocked_by_jobs > 0`).
- **Backfill (pre-migration from-notion books):** safe reconciliation — for each currently-mapped
  Notion candidate, download bytes READ-ONLY, match `content_sha256` UNIQUELY against existing
  books (same subject); ambiguous/multi-match → leave unlinked (they simply show TEXTBOOK READY
  until next prepare, which dedup-upserts the link). Shipped as an operator script
  (`scripts/backfill_notion_sources.py`), run once manually — not in the migration (network in
  migrations = no).
- No generation-path changes. Worklog **0144** (0142 BE-16, 0143 router — re-verify at finish).
  Branch `feat/prepare-status-redo`, worktree `../HCGA-prep-status`.

## Tasks (TDD, commit per task)

1. **Migration + models + repo** — `book_notion_sources` table + `books.toc_ready_at`;
   `notion_sources_repo.upsert_link / links_for_pages(page_ids)`; stamp `toc_ready_at` in the
   extraction-success path (find where status flips to `toc_ready` — `toc_extractor` — and set it
   there; clear on redo). Scratch-DB tests: upsert idempotence, uniqueness, cascade on book
   delete, toc_ready_at stamped/cleared.
2. **`/from-notion` upserts the mapping** — on fresh ingest AND dedup hit, using the RESOLVED
   candidate (subject page id + block id actually downloaded; `download_textbook` must surface
   which candidate it picked — extend its return or a small out-param). Route tests incl. dedup
   path.
3. **`/toc/retry` extension** — allow `toc_ready`; structured 409 (`toc_retry_blocked_by_jobs`
   + count + jobs≤20); clear `toc_validation*` + `toc_ready_at` on accept. RED the prose-409
   shape away; keep #87's refuse-only semantics byte-intact otherwise.
4. **Availability enrichment** — notion.py joins the mapping table + books for per-part
   `book_status/toc_total/toc_ready_at/redo_blocked_by_jobs`; response back-compat (keys added,
   none changed). Tests: prepared/unprepared/mid-extract/review/failed parts, and a part whose
   book was deleted (dangling link → treated unprepared).
5. **FE dialog state machine** — chips (6 states), PREPARED panel with Use-existing (no
   mutation) / Redo (confirm + destructive style + blocked-reason from structured 409), poll or
   SSE for PREPARING→ready transitions while the dialog is open; upload.tsx + launcher.tsx via a
   SHARED component (the BE-19 copy-paste drift lesson — do not duplicate). tsx tests for the
   pure state mapping; tsc + build.
6. **Backfill script + docs + finish** — `scripts/backfill_notion_sources.py` (read-only
   downloads, unique-SHA match, ambiguous→skip+report); run it live once as acceptance (report
   linked/skipped counts); docs (HOW_IT_WORKS prepare flow, DATABASE.md new table, CLAUDE.md
   key-tables line); worklog 0144 + INDEX; wishlist: close nothing, this is new surface. Full
   suite + FE gates + rebase check + PR → gate.

## Flagged for the gate
1. Migration collision risk with the BE-16 limiter lane (both may claim the next migration
   number) — serialize or renumber at rebase.
2. `download_textbook` return-shape extension (task 2) is internal API surface — callers besides
   the route: none today (verified in BE-19), but re-verify at implementation.
3. Backfill is operator-run and idempotent; un-backfilled books degrade gracefully (show
   TEXTBOOK READY; next prepare self-heals the link via dedup-upsert).
