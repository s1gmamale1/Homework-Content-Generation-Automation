# Notion textbook fetching correctness (BE-19) — candidates, validation, language guard

**Item:** audit finding BE-19 (root `Wishlist.md`) — textbook fetching works only for a single PDF
attached directly to the selected subject page. Live-verified failures: child-page parts invisible
(G1-UZ Matematika 4 parts, G5/G6/G11 UZ+RU — 98/419 subject pages have no direct PDF), multiple
same-page PDFs silently reduced to the first (`_first_pdf_block`), workbook can outrank textbook
(bot-handle contains "darslik"; RU markers unknown), prepare accepts any page/grade ("banana",
foreign pages, wrong grade), missing pages → HTTP 500, and a **confirmed wrong-language attachment**
(G6-RU Математика Часть-1 holds the UZ file) that naive recursion would ingest as Russian.
Subsumes BE-08/BE-09 and closes wishlist items `notion-pdf-rank-markers-1`,
`notion-nested-part-pages-1`, `notion-crosslang-part-picker-1`.

## Approach & key decisions (all three locked with user 2026-07-16)

- **Candidate model: extend #86's `parts[]`, add a block selector.** Each part gains
  `candidates:[{page_id, block_id, filename, rank}]` — enumerated from the part page's direct PDF
  blocks, PDFs inside container blocks (toggle/column_list/column, bounded recursion), and PDFs on
  its `child_page`s (one level). `FromNotionRequest` gains optional `block_id`. **Rejected:** a new
  candidates endpoint (second API surface to keep in sync with #86's map; bigger FE rewrite).
- **Ambiguity is rejected, never silently resolved:** `download_textbook` with an explicit
  `block_id` fetches exactly that block; without one it requires exactly ONE textbook-ranked
  candidate — multiple → 422 listing the candidates, zero → existing NoTextbook 422.
- **Language guard: deterministic script check, block only on high confidence.** After download,
  sample text via pypdf: requested `ru` but Cyrillic ~absent (or `uz` but Cyrillic-dominant) →
  hard 422 naming file + detected script; no text layer (scanned) or ambiguous → ingest with a
  `warnings:[…]` entry in the response. **Rejected:** full language-ID (uz/en both Latin —
  false-positive risk) and warn-only (a wrong-language book is wrongness, not polish).
- **Prepare validates ancestry:** page must sit under the lessons root within the claimed grade +
  language container (parent-chain walk, ≤4 hops); grade must be "1"–"11"; missing/inaccessible
  page → clean 404/502; duplicate language containers → explicit 422. Prepare is a rare operator
  action — a few extra Notion reads are fine.
- Verified anchors: `_pdf_rank` `notion_fetch.py:66` (textbook markers checked FIRST — the bug),
  `_first_pdf_block` `:77`, `_subjects_under` `:118`, `available_languages` `:170`,
  `download_textbook` `:222`; `FromNotionRequest` `books.py:150-153`; lessons root default
  `config.py:214`; FE pure resolver `web/src/lib/notion-parts.ts` (+`.test.ts`), consumers
  `upload.tsx` + `launcher.tsx`. Existing tests: `tests/services/test_notion_fetch.py`,
  `tests/api/test_from_notion.py`, `tests/api/test_book_from_notion_language.py`.
- No migration. Worklog **0141**. Branch `feat/notion-fetch-correctness`, worktree
  `../HCGA-notion-fetch`. Suite baseline: 1547 passed / 217 skipped. FE tests are tsx+node:assert
  (NOT vitest). All Notion access in tests is mocked; acceptance uses live READ-ONLY calls only.

## Tasks

### Task 1 — `_pdf_rank` fix: bot-handle strip, workbook-before-textbook, RU markers (RED → GREEN)

**Tests first** (`tests/services/test_notion_fetch.py`, pure): the audit's adversarial fixtures —
`"mashq daftari (@elektron_darslikbot).pdf"` → workbook rank (RED today: rank 0),
`"6_sinf_matematika_darslik_2024.pdf"` → 0, `"учебник 5-класс.pdf"` → 0 (RED: 1),
`"рабочая тетрадь 5-класс.pdf"` → workbook (RED: 1), `"тетрадь.pdf"` → workbook,
bare `"@elektron_darslikbot"`-suffixed textbook still → 0, order-independence: textbook+workbook
pair ranks identically regardless of block order.

**Code** (`app/services/notion_fetch.py`): strip bot/source handles before ranking
(`re.sub(r"\(?@[a-z0-9_]+\)?", "", name)`); check `_WORKBOOK_MARKERS` BEFORE `_TEXTBOOK_MARKERS`
(workbook wins where both match — "mashq daftari … darslikbot"); extend marker tuples with RU:
workbook `("рабочая тетрадь", "тетрадь")`, textbook `("учебник",)`. Names are `_fold`-ed — verify
fold handles Cyrillic lowercase.

Commit: `fix(notion): pdf rank — strip bot handles, workbook-first, RU markers (BE-19 task 1)`
Stage: `app/services/notion_fetch.py tests/services/test_notion_fetch.py`

### Task 2 — candidate enumeration: containers + child pages (RED → GREEN)

**Tests first** (same file, fake-client trees per existing conventions): a page with (a) two direct
PDFs → 2 candidates with distinct `block_id`s; (b) a PDF inside a toggle → found; (c) two
`child_page`s each holding a PDF (the G1-UZ Matematika shape) → candidates carry the CHILD page_id;
(d) rank attached per candidate; (e) `available_languages` marks `has_textbook=True` for a parent
whose only PDFs live in child pages (RED today) and each part carries `candidates`.

**Code** (`app/services/notion_fetch.py`): new
`textbook_candidates(client, page_id) -> list[dict]` — walk the page's blocks; PDF blocks →
candidate `{page_id, block_id, filename, rank, url}`; container blocks
(`toggle`/`column_list`/`column`) → recurse (depth-bounded, ~3); `child_page` blocks → scan that
child's blocks the same way ONE level down (no grandchildren). `_first_pdf_block` stays for
back-compat callers but `_subjects_under`/`available_languages` switch to candidates
(`has_textbook = bool(candidates)`; part dicts gain `candidates`). Keep response back-compat:
existing `page_id`/`title`/`has_textbook` keys unchanged.

Commit: `feat(notion): enumerate textbook candidates across containers + child pages (BE-19 task 2)`
Stage: `app/services/notion_fetch.py tests/services/test_notion_fetch.py`

### Task 3 — selector plumbing + reject ambiguity (RED → GREEN)

**Tests first** (`tests/services/test_notion_fetch.py` + `tests/api/test_from_notion.py`):
`download_textbook(client, page_id, block_id="…")` fetches exactly that candidate (even a child-page
one); no block_id + exactly one textbook-ranked candidate → downloads it; no block_id + TWO
candidates → raises `AmbiguousTextbook` and the route returns 422 whose detail lists candidates
(filename + block_id) (RED today: silent first); route accepts and threads `block_id`.

**Code**: `download_textbook(client, subject_page_id, block_id: str | None = None)` on candidates;
new `AmbiguousTextbook` exception; `FromNotionRequest` + route (`books.py`) thread `block_id`;
422 handler mirrors the existing NoTextbook shape.

Commit: `feat(notion): explicit block selector; ambiguous textbook → 422 with candidates (BE-19 task 3)`
Stage: `app/services/notion_fetch.py app/api/v1/books.py tests/services/test_notion_fetch.py tests/api/test_from_notion.py`

### Task 4 — prepare validation: ancestry, grade, controlled errors (RED → GREEN)

**Tests first** (`tests/api/test_from_notion.py`, mocked client): grade `"banana"`/`""`/`"12"` →
422 (RED: accepted); empty `subject_page_id` → 422; client raising not-found on the page → 404
(RED: 500); other API error → 502; page whose parent chain does NOT reach the requested grade page
under the lessons root → 422 "page not under grade N/<language>"; page under the WRONG language
container → 422; duplicate matching language containers on the grade page → 422 naming both;
happy path (chain page→language container→grade page→lessons root) → 201.

**Code**: new `verify_page_ancestry(client, page_id, *, grade, language, lessons_root) -> None`
in `notion_fetch.py` (parent-chain walk ≤4 hops via the client wrapper — extend
`NotionClientWrapper` with `get_page_parent` if absent; duplicate-container detection reuses the
container regex from `_subjects_under`); route validates grade ∈ {"1".."11"} and non-blank page id
via Pydantic (`pattern`/validator), wraps title+fetch in try → 404/502.

Commit: `feat(notion): prepare validates ancestry/grade; controlled 404/502 (BE-19 task 4)`
Stage: `app/services/notion_fetch.py app/services/notion_client.py app/api/v1/books.py tests/api/test_from_notion.py`

### Task 5 — language script guard (RED → GREEN)

**Tests first** (`tests/api/test_book_from_notion_language.py` + a pure unit for the checker):
pure: Cyrillic sample + requested `ru` → ok; Latin sample + `ru` → mismatch; Cyrillic + `uz` →
mismatch; empty/None text (scanned) → indeterminate. Route: mismatch → 422 naming filename +
detected script (RED today: 201); indeterminate → 201 with `warnings:["language-check skipped…"]`;
match → 201 no warning.

**Code**: `detect_pdf_script(pdf_bytes, sample_pages=5) -> "cyrillic"|"latin"|"unknown"` (pypdf
text sample, character-class ratio ≥0.3 Cyrillic → cyrillic; letters present else unknown) in a
small pure module (e.g. `app/services/pdf_lang.py`); prepare route calls it post-download,
pre-ingest: hard 422 only on confident mismatch (`ru`→latin, `uz`/others→cyrillic), else thread
`warnings` into the response payload.

Commit: `feat(notion): PDF script guard — block confident language mismatch, warn otherwise (BE-19 task 5)`
Stage: `app/services/pdf_lang.py app/api/v1/books.py tests/api/test_book_from_notion_language.py tests/services/test_pdf_lang.py`

### Task 6 — FE: candidate picker + block_id + warnings (tsc-driven)

**Code** (`web/src/`): types gain `candidates` on parts + `block_id` on the prepare call +
`warnings` on the response; `lib/notion-parts.ts` resolution consumes candidates (clicked-page
authority and #86 cross-language rules unchanged — a part with >1 candidate exposes them for an
explicit pick; parts whose candidates live on child pages resolve to the child `page_id` +
`block_id`); `upload.tsx` + `launcher.tsx` render a candidate select when >1 (filename labels),
pass `block_id`, toast any `warnings`. Extend `lib/notion-parts.test.ts` (tsx+node:assert):
child-page candidate resolution, multi-candidate exposure, single-candidate auto-pick.
Verify: `npx tsx src/lib/notion-parts.test.ts`, `npx tsc -p tsconfig.app.json --noEmit`,
`npm run build`.

Commit: `feat(fleet): textbook candidate picker + block selector + prepare warnings (BE-19 task 6)`
Stage: the touched web/src files only.

### Task 7 — docs + acceptance + finish

- **Acceptance (read-only, $0, live Notion):** run `available_languages`/`textbook_candidates`
  against the REAL workspace for the audit's confirmed shapes — G1-UZ Matematika (expect 4
  child-page candidates), G11-UZ Algebra (2 same-page candidates), G6-RU Математика (child parts
  enumerated) — and run `detect_pdf_script` on the two read-only-downloaded G6 PDFs (expect Part-1
  → latin → would-block for `ru`; Part-2 → cyrillic → pass). NO prepare/ingest against prod, NO
  writes, NO generation. Paste outputs in the PR.
- Docs de-stale: `docs/HOW_IT_WORKS.md` (fetch flow, candidates, validation, guard),
  `docs/CODE_MAP.md` if it names `_first_pdf_block`, `CLAUDE.md` PDF-handling bullet if touched.
- Close `notion-pdf-rank-markers-1`, `notion-nested-part-pages-1`,
  `notion-crosslang-part-picker-1` in `docs/memory/WISHLIST.md` (✅ CLOSED lines, style-matched).
- Worklog **0141** + INDEX row (re-verify tail at rebase). Full suite; FE gates; rebase check;
  push; PR → **GK2 gates + merges**; then plan → `shipped/`.

## Flagged for the gate

1. Prepare's response shape gains `warnings` (additive); 422 detail for ambiguity is a NEW error
   contract the FE must parse — FE lands in the same PR (task 6).
2. The ancestry walk adds Notion read calls to prepare (bounded ≤4 + container listing) — rare
   operator action, acceptable.
3. `_first_pdf_block` kept only if a caller remains; if task 2 leaves it dead, delete it in task 2.
4. Child-page descent is ONE level by locked decision — grandchildren stay invisible (documented).
