# Language-aware source books (UZ / RU textbooks, surfaced everywhere)

Status: PLAN — awaiting user approval. Author: gatekeeper. Executor: implementer via
`superpowers:subagent-driven-development`.

## Approach & key decisions

Today `output_language` is a **pure output transform layered on a single `book_id`**
(`batch.py:54-56` `UNIQUE(book_id, transport, output_language)`; `pipeline.py:175-176`
selects the PDF by `book_id` only; `prompts.py:103-110` `_resolve_language_rule` is the
*only* place language changes output). A `Book` has **no language column** (`book.py:11-38`
— `subject + grade + sha` only). Result: a "Russian" homework is the *Uzbek* PDF rendered
in Russian.

This plan makes language a **source** property of the book while keeping translation as a
fallback. Load-bearing facts verified against code:
- Book dedup is `(content_sha256, subject)` (`books.py:43-55,83`) — a Russian PDF already
  produces a *distinct* row (different sha); we only add a **label**, no dedup redesign.
- `output_language` is independent of `book_id` today, so keeping it **overridable** means
  **no change to the batch key** — a RU book's `book_id` already differs; `output_language`
  stays the axis on top (`batch.py:54-56`, `batch.py:191,239,303`).
- Generation needs **zero change**: `_resolve_language_rule` already renders any
  `output_language` for non-L2 subjects (`prompts.py:103-110`). This is a **source + UI**
  feature, not a prompt feature.
- The real backend work is the **Notion fetch path**: `list_subjects` hard-filters to the
  Uzbek `N - sinf` container and ignores `klass` (`notion_fetch.py:89,102-105`), and
  `_map_subject` is Uzbek-keyword-only (`notion_fetch.py:25-38`, `subjects.py:48-103`).

Locked decisions (user, 2026-06-29): **(1)** output language *defaults to* the book's
source language but stays overridable; **(2)** source languages = **uz + ru + en** — uz/ru
are fetched from Notion's `sinf`/`klass` trees; **en is a first-class source language but has
no native Notion tree yet, so fetching English requires the operator to first create an
English page/container (or upload the textbook directly)** — until that page exists English
shows as *unavailable* in the prepare flow; **(3)** prepare flow **detects available
languages** (crawl the per-language containers) and the operator **picks**; **(4)** UI shows
a language **badge + filter**.

Rejected: *pin output to source* (blocks RU content when only a UZ textbook exists);
*one book row + multiple PDFs* (breaks the one-PDF-per-`book_id` storage model — dedup
already lets each language be its own row); *language as top-level nav split* (user chose
badge+filter).

## Non-goals
- No change to generation/prompt rendering (already language-correct).
- No *seeded* EN content tree — EN *source* is supported in the model, but has **no native
  Notion tree today**; fetching English needs the operator to create an English
  container/page (or upload the PDF directly), so English is *unavailable* in the prepare
  flow until that page exists. We do NOT auto-create English pages.
- The full `ru:` (and any `en:`) Notion archival page map is an **operator/config** step
  (archival code shipped in #60), not a code task — see "Operator steps".
- Strict source↔output pinning (explicitly rejected).

---

## Phase 1 — Backend source-language model + Notion RU fetch

### Task 1 — `books.source_language` column + migration
- **Model** `app/models/book.py`: add `source_language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="uz")` (mirror the `output_language` pattern in `homework_job.py:30`); add CHECK `source_language IN ('uz','ru','en')` named `ck_books_source_language` (mirror `homework_job.py:116-117` — same enum as `output_language`).
- **Migration** `alembic/versions/<next>_books_source_language.py` (revision id ≤32 chars, e.g. `0040_books_source_language`, down_revision = current head `0039_launch_defaults_content`): `add_column` + `create_check_constraint`; downgrade drops both. Existing rows backfill to `'uz'` via the `server_default` (all current books are Uzbek).
- **Test** `tests/repositories/test_books_source_language.py` (DB-integration, `RUN_DB_INTEGRATION=1`): upgrade head → insert a book with default → `source_language == "uz"`; insert with `"ru"` and `"en"` → both ok; insert `"fr"` → raises (CHECK bites). Bite-proof: drop the CHECK locally → the `"fr"` case stops raising.
- **Commands**: `createdb -U macmini5 edu_t1 && RUN_DB_INTEGRATION=1 DATABASE_URL=…edu_t1 uv run alembic upgrade head && RUN_DB_INTEGRATION=1 … uv run python -m pytest tests/repositories/test_books_source_language.py -q`
- **Commit**: `feat(books): add source_language column (uz|ru) + migration 0040`

### Task 2 — Russian + English Notion title→subject mapping
- **Registry** `app/services/subjects.py`: add optional `ru_keywords` and `en_keywords` (`tuple[str, ...] = ()`) to `SubjectDef` (line 27-34). Populate Russian folded keywords from the live `klass` crawl (Operator step O1; e.g. `algebra`→`("алгебра",)`, `geometriya-g7-11`→`("геометрия",)`, `physics`→`("физика",)`, `kimyo-g7-11`→`("хими",)`, `biology`→`("биолог",)`, history→`("всемирная истори","история узбекистан")`). Populate English folded keywords from the app labels (e.g. `algebra`→`("algebra",)`, `geometriya-g7-11`→`("geometry",)`, `biology`→`("biology",)`, `physics`→`("physics",)`) so a future operator-created English page resolves. Generalize `notion_keyword_pairs(language: str = "uz")` to return the uz/ru/en pairs (longest-first); keep the existing call site default `"uz"`.
- **Fetcher** `app/services/notion_fetch.py`: add `_map_subject_for_language(title, language)` (one helper, reads `notion_keyword_pairs(language)`); keep `_map_subject` as the `"uz"` wrapper. `_fold` must NOT strip Cyrillic — it only lowercases + strips apostrophes; add a test asserting Cyrillic survives folding.
- **Test** `tests/services/test_notion_lang_mapping.py`: `_map_subject_for_language("Алгебра","ru") == "math-algebra"`, `"Геометрия"/"ru" == "geometriya-g7-11"`, `"Биология"/"ru" == "biology"`, history split RU titles map to `history`; `"Algebra"/"en" == "math-algebra"`; a UZ title under the ru mapper → `None` (no cross-talk). Bite-proof: empty the ru keyword for algebra → its assertion fails.
- **Command**: `uv run python -m pytest tests/services/test_notion_lang_mapping.py -q`
- **Commit**: `feat(notion): Russian + English subject-title mapping`

### Task 3 — Crawl per-language containers + available-language detection
- `app/services/notion_fetch.py`:
  - Define a per-language container matcher: `_LANG_CONTAINER_RE = {"uz": _SINF_RE, "ru": re.compile(r"-\s*(класс|klass)\b", re.I), "en": re.compile(r"-\s*(english|grade|inglizcha)\b", re.I)}` — the `en` pattern matches whatever an operator names the English container; it simply finds nothing today (English is unavailable until that page is created).
  - Factor a private `_subjects_under(client, grade_page_id, container_re, language)` from the current `list_subjects` body (maps titles via `_map_subject_for_language(title, language)`). Keep `list_subjects` (uz) as a thin backward-compat wrapper (`books.py`/tests still call it).
  - Add `list_subjects_for_language(client, grade_page_id, language)` for any of uz/ru/en.
  - Add `available_languages(client, grade_page_id)` → `dict[app_subject, dict[lang, {"page_id","has_textbook"}]]` by crawling **all three** containers; a subject lists only the languages whose container exists AND has a textbook. With no English container present, English is simply absent from the map (→ UI shows it unavailable).
- **Test** `tests/services/test_notion_lang_crawl.py` with a fake client exposing `9 - sinf` + `9 - класс` (no English container): `list_subjects_for_language(..,"ru")` returns the RU subjects mapped; `available_languages` reports `math-algebra → {uz:{…}, ru:{…}}` (no `en`); a subject only under sinf reports `uz` only. Add a case WITH a `9 - grade` English container → `available_languages` then includes `en`. Bite-proof: make the ru container regex never match → the ru entries vanish and the test fails.
- **Command**: `uv run python -m pytest tests/services/test_notion_lang_crawl.py -q`
- **Commit**: `feat(notion): crawl per-language containers + available-language detection`

### Task 4 — Thread `source_language` through ingest (upload + from-notion)
- `app/repositories/books.py`: `create(...)` gains `source_language: str = "uz"`, stamped on the row.
- `app/api/v1/books.py`:
  - `ingest_pdf(...)` (line 57-114) gains `source_language="uz"`, passed to `create` (line 93-101). Dedup stays `(sha, subject)` — different-language PDFs differ by sha, so no false reuse; add an assertion-comment that a re-fetch of the *same* edition still dedups.
  - `book_from_notion` (line 145-172): request body gains `language: str = "uz"` (validate `in {"uz","ru","en"}`); resolve the subject page_id for that language via `available_languages` / `list_subjects_for_language`; **if that language has no container/textbook → 422 with a clear actionable message** (e.g. `"No English page for this subject yet — create an English container/page with the textbook in Notion, or upload the PDF directly."`); use `_map_subject_for_language(title, language)`; pass `source_language=language` into `ingest_pdf`.
  - `upload_book` (line 117-132): optional form field `source_language` (default `"uz"`, validate `{"uz","ru","en"}`) → `ingest_pdf`. This is the direct path for an English textbook while no English Notion tree exists.
- **Test** `tests/api/test_book_from_notion_language.py` (extend existing `test_from_notion.py` style, fake client): from-notion `language="ru"` → created book has `source_language=="ru"` and the subject resolved via the ru mapper; `language="ru"` for a subject with no klass textbook → 422; `language="en"` with no English container → 422 carrying the "create the page / upload directly" guidance; default (`language` omitted) → `"uz"` (byte-identical to today); upload with `source_language="en"` → book tagged `"en"`. Bite-proof: hardcode `source_language="uz"` in `ingest_pdf` → the ru/en assertions fail.
- **Command**: `NOTION_API_KEY=dummy uv run python -m pytest tests/api/test_book_from_notion_language.py tests/api/test_from_notion.py -q`
- **Commit**: `feat(books): tag fetched/uploaded books with source_language`

---

## Phase 2 — Launch coupling + API payloads

### Task 5 — Output language defaults to the book's source language (overridable)
- `app/api/v1/batch.py` (`launch_batch`, line 91): the book is already loaded for `book_id` validation (line 96-104). Change the resolution so the order is **explicit pick → book.source_language → global launch-default**: when `body.output_language is None`, fall back to `book.source_language` *before* `ld.output_language`. Keep `resolve_output_language` pure; add a small `resolve_output_language_for_book(explicit, book_source_language, global_default)` (or pass the book lang as the default arg) in `agent_models`/the existing resolver module, with a unit test.
- Translate path preserved: an explicit `output_language` still wins (UZ book → RU output remains possible).
- **Test** `tests/services/test_output_language_resolution.py`: explicit `"ru"` + book `"uz"` → `"ru"`; `None` + book `"ru"` → `"ru"` (source default, NOT the global uz); `None` + book `"uz"` + global `"ru"` → `"uz"` (book beats global) — confirm the precedence; bite-proof by swapping the fallback order → the `None+ru-book` case flips to the global default and fails.
- **Command**: `uv run python -m pytest tests/services/test_output_language_resolution.py -q`
- **Commit**: `feat(launch): output language defaults to book source language`

### Task 6 — Expose language in API payloads
- `GET /books` (`books.py` list serializer) + the `Book` response schema: add `source_language`.
- `output_language` on the batches list / `BatchSummary` is **NOT done here** — it is delivered by the Monitor curriculum-dashboard plan's Phase 1 (`_rollup_payload` at `batch.py:59` + the `BatchSummary` type). This task depends on that having merged; if it hasn't when this task runs, add the one `_rollup_payload` line + `BatchSummary` field here instead (do not duplicate).
- The available-languages probe surfaces to the FE: add `GET /api/v1/books/notion/subjects?grade=N` (or extend the existing notion-subjects listing the launcher uses) to return per-subject `available_languages`.
- **Test** `tests/api/test_books_language_payload.py`: a book with `source_language="ru"` serializes that field; batches list returns `output_language`; the notion-subjects endpoint returns `{subject: {uz:…, ru:…}}` (fake client). 
- **Command**: `NOTION_API_KEY=dummy uv run python -m pytest tests/api/test_books_language_payload.py -q`
- **Commit**: `feat(api): surface source_language (books) + output_language (batches) + available languages`

---

## Phase 3 — Frontend: badge, filter, prepare-flow (FE acceptance = tsc + build + tsx pure-helper tests + in-browser eyeball)

### Task 7 — Types
- `web/src/lib/types.ts`: add `source_language: OutputLanguage` to `Book` (line 94-106) and `output_language: OutputLanguage` to `BatchSummary` (line 368-389). (`OutputLanguage` already exists, line 70.)
- Pure helper `web/src/lib/language.ts`: `LANG_LABEL = {uz:"O‘zbek", ru:"Русский", en:"English"}`, `langBadge(lang)` (chip class), `langAccent`. `npx tsx`-tested helper `language.test.ts`.
- **Commit**: `feat(fe): Book.source_language + BatchSummary.output_language types + language helpers`

### Task 8 — Library: language badge + filter
- `web/src/routes/library.tsx`: add a language chip to `BookCard` meta row (line 393-403) using `langBadge(book.source_language)`; add a **language filter** facet (segmented `All / UZ / RU`) above the `CategoryBrowser` (line 152) that filters `books` before grouping; add a per-language count to the summary strip (line 95-126).
- **Acceptance**: `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`; eyeball `/library` shows the chip + filter narrows.
- **Commit**: `feat(fe): language badge + filter on Library`

### Task 9 — Fleet launcher: reconcile the Language selector with source language
- `web/src/components/fleet/launcher.tsx`: `ReadyCard` (line 566) shows the book's `source_language` as a badge in the header chip row (line 834-838). The existing Language `Select` (line 932-961) now reads "Auto → {source language}" when on Auto (compute from `book.source_language`, not the global default); an explicit non-source pick is visually marked "translate". Default `outputLanguage` state stays `null` (= Auto = source).
- **Acceptance**: tsc + build; eyeball a RU book defaults to RU and shows a "translate" hint when overridden to UZ.
- **Commit**: `feat(fe): launcher language selector defaults to book source language`

### Task 10 — Prepare-from-Notion: pick the language
- `web/src/components/fleet/launcher.tsx` Part A "Prepare a subject" (line 130-286) **and** `web/src/routes/upload.tsx` Notion browser (line 296-415): after grade+subject, call the new available-languages endpoint and render **UZ / RU / EN** availability chips; the operator picks a language; `api.fetchBookFromNotion(page_id, grade, language)` gains the `language` arg (`lib/api.ts:308`). A language with no container/textbook is **disabled** with a tooltip — for English specifically: *"No English page yet — create an English page (with the textbook) in Notion, or upload the PDF directly."*
- Upload form (`upload.tsx` lines 181-294): add a **source-language Select** (`uz`/`ru`/`en`, default `uz`) wired into `api.uploadBook(file, subject, grade, sourceLanguage)` (`lib/api.ts:83`) — the direct route for an English textbook today.
- **Acceptance**: tsc + build; eyeball: picking RU fetches the klass edition (RU badge in tray); English chip is disabled with the create-page/upload hint; uploading with EN selected tags the book `en`.
- **Commit**: `feat(fe): choose language when preparing a book (RU fetch, EN upload/disabled-with-hint)`

### Task 11 — Monitor batch cards: language badge — ❌ DROPPED (user decision, 2026-06-29)
**Not built.** The Monitor already received language tabs from the monitor-dashboard work (#65), so a per-card `output_language` badge is redundant and would collide with that UI. Skipped entirely; Phase 3 shipped Tasks 7–10 only and touches no Monitor file.

~~- `web/src/components/fleet/batch-funnel.tsx`: add an `output_language` chip to `BookCard`/`TransportRow` header, alongside grade + transport label. Optionally extend `lib/monitor-grouping.ts` with an opt-in language sub-group.~~

---

## Operator steps (config + content, not code)
- **O1 — Full `ru:` archival map.** Crawl every grade's `N - класс` tree (the gatekeeper has a working crawler; one pass) and produce the complete `{"ru:<subject>|<grade>": "<page-id>", …}` block (history uses the `{keyword}` object form with Russian filename keywords). Add to the head **and every worker** `.env` `NOTION_SUBJECT_PAGES`, then restart (archival runs on the worker; config is read at process start). Unblocks RU archival fleet-wide (the single `ru:math-algebra|9` key was added manually as a spot-fix).
- **O2 — Re-fetch RU editions.** For the subjects/grades you want RU content on, use the new prepare-with-language flow to fetch the klass textbooks (each becomes a `source_language="ru"` book).
- **O3 — English source (no native tree).** To generate from an English textbook you must first provide it: either **upload the PDF directly** (Upload form → source language = English), or **create an English container** in Notion (named to match the `en` container regex from Task 3, e.g. `"9 - grade"` / `"9 - english"`) with the textbook attached, then prepare-with-language=en will fetch it. Until one of those exists, English shows as unavailable in the prepare flow (by design). If you later want English homeworks archived to a dedicated Notion page, add `en:<subject>|<grade>` keys to `NOTION_SUBJECT_PAGES` (same shape as the `ru:` map).

## Risks / open
- **Cyrillic TOC/extract is unproven** — before bulk RU generation, run a real TOC-extract smoke on one RU textbook (acceptance gate per CLAUDE.md: actual gemini call). If the `Содержание` heading / Cyrillic page text trips `toc_extractor` heuristics, that's a fast-follow on the extractor (out of this plan's scope but a hard dependency for RU at scale).
- **Duplicate Notion entries**: an old UZ-source→RU-output homework and a new RU-source→RU-output one both archive under the same `ru:` lesson page → two "Homework" subpages. Decide whether to delete the translate-era RU copies (manual, like the earlier re-archive).
- **`_map_subject_for_language` coverage**: the klass subject set is fixed and small; curate `ru_keywords` (and `en_keywords` for a future English page) from the live crawl (O1) so every container subject resolves — a missing keyword silently 422s a fetch (the Task 3 test guards the known set).
- **Cost/scale**: native RU sources roughly double the corpus + generation volume for dual-language grades — honor the no-spam rule; ramp via the normal batch flow, not bulk.

## Suggested PR slicing
P1 (Tasks 1-4) → P2 (Tasks 5-6) → P3 (Tasks 7-11). Each phase is independently shippable
and gate-able; P3 depends on P2's payload fields.
