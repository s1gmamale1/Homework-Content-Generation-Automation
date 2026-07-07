# TOC lesson filter — classify rows + default batch launch to LESSON-only

Plan for `batch-launch-lesson-filter-1` (REOPENED 2026-07-07). Branch `feat/toc-lesson-filter`
off `origin/Nggaev-v2` (@ a0e92c6). Commit prefix `tocf:`. Worklog **0127**. **No migration**
(class is computed on-the-fly — slot 0045 stays free).

## Approach & key decisions

**Problem (verified twice against real data):** batch launch fires one job per TOC row with
zero lesson/non-lesson discrimination. 2026-07-06 sweep: only ~282 of ~415 rows across 10 UZ
math books are real lessons. 2026-07-07 printed-mundarija verification confirmed the stored TOCs
are **faithful to print** — the junk is *structural* (hierarchical `N-§` umbrellas whose page
ranges swallow their child mavzu rows → duplicate packets; 1-page nazorat/test rows; «Javoblar»
answer key; «Tarixiy ma'lumotlar»; Ilova), not an extraction bug, so it won't self-correct.
Blind full-TOC batch ≈ +$80 waste + junk packets per 10 books; October is bulk-scale.

**Chosen approach (both decisions locked with the user 2026-07-07):**
1. **Deterministic title-pattern classifier** (rejected: extractor-prompt tagging — needs paid
   re-extract of 17 books, no offline unit test, drifts per model; rejected: both — two sources
   of truth for no gain). A pure Python function is the only shape that covers already-ingested
   books, is unit-testable offline against a hand-labeled fixture, and costs $0.
2. **Compute the class on-the-fly, no persisted column, no migration, no backfill** (diverges
   from the dispatch's directed persist-a-column shape; user accepted). Rationale: HEADER
   detection is a *whole-book* computation (a row is HEADER because its page range swallows ≥2
   siblings), and TOC rows are user-editable/deletable (`toc_repo.update`/`delete`, FE
   `book.tsx`). A persisted class goes stale on any edit — delete a child mavzu and the parent's
   stored `header` is wrong, silently excluding a real lesson — so "persist correctly" would
   require whole-book recomputes on ≥3 mutation hooks plus every future TOC-curation surface. A
   single pure classifier called at the two read points (books serialization + batch `targets`)
   gives one consistent verdict for all consumers, self-heals on edits, and is trivially cheap
   (<100 pure rows/read, no LLM). **Carry-in requirement (user):** because the class now
   materializes at read time, the launcher MUST surface it (per-row class chip) and the batch
   preview MUST report the excluded-by-class count — that is the audit trail the DB column would
   have given, moved to where the operator looks.

**Load-bearing facts verified against current code (a0e92c6):**
- Filter slot: `app/api/v1/batch.py:157` — `targets = lessons` when `body.toc_entry_ids is None`;
  explicit picks at `151-155` stay unfiltered (operator override is sacred —
  `per-job-selection-overrides-env`). Preview branch at `260-281` walks the same `targets`.
- `app/models/toc_entry.py` — **no class column** (correct; we add none). `toc_repo.bulk_create`
  (`app/repositories/toc_entries.py:11`) is the ingest point (untouched — no persistence).
- Single serialization choke point: `_enriched_toc_entries` (`app/api/v1/books.py:465`), shared
  by the REST book endpoint AND the SSE `toc_ready` replay — inject `entry_class` here so the two
  cannot drift. `TOCEntryOut` already carries injected non-column fields (`latest_job_status`),
  so there is precedent.
- FE: `launchBody` (`web/src/components/fleet/launcher.tsx:873`) sends `toc_entry_ids` **only**
  in choosing/subset mode; the default "Launch remaining N" path sends none → hits the server
  filter. So the money-safe default belongs server-side; per-class opt-in rides a new
  `include_classes` request field, not FE pre-selection. Choosing list renders every row at
  `launcher.tsx:1335`. `TOCEntry` FE type at `web/src/lib/types.ts:81`.
- Migration head is `0044_solver_boss_toggle`; **we add no migration** (0045 stays free).

**Classes** (lowercase string values): `lesson`, `header`, `recall`, `revision`, `test`, `other`.
**Precedence** (first match wins): keyword classes → page-containment HEADER → caps+single-page
residual → default `lesson`. Every row gets a concrete class (never null). Default launch filter
= `lesson` only; `include_classes` widens it; explicit `toc_entry_ids` bypasses entirely.

## Global constraints (binding — copy verbatim into every reviewer prompt)

- **Operator override is sacred:** explicit `toc_entry_ids` and the single-section `/generate`
  path MUST stay UNFILTERED. The class filter applies ONLY to the `toc_entry_ids is None` batch path.
- **No new migration, no new DB column** — class is computed on-the-fly. If a task adds a column
  or migration, it is wrong.
- **gemini-only models**; **never enqueue real jobs for acceptance** (the stale Windows worker
  "Oliver" claims anything queued) — use `preview: true` + DB assertions / direct function calls only.
- **Classifier is a pure function** over duck-typed rows (attrs: `section_number`, `section_title`,
  `page_start`, `page_end`); returns a class list **aligned to input order** (it sorts internally
  for containment). No DB, no I/O, no model import — so it is offline-unit-testable and callable
  from any read point.
- Accuracy is gated against a **hand-labeled fixture built from REAL `edu_copy` rows**; state the
  confusion counts, do not round up.
- Stage only each task's listed files; never `git add -A`. Commit per task, prefix `tocf:`.

## Tasks

### Task 1 — Pure classifier module (structural TDD)
**Files:** `app/services/toc_classifier.py` (new), `tests/services/test_toc_classifier.py` (new).
**Do:** Implement `classify_entries(entries) -> list[str]` + module-level class constants
(`LESSON="lesson"`, etc.) and the keyword tables. Duck-typed input; output aligned to input order.
Precedence exactly: (1) keyword match on `section_title` (case-insensitive, normalized `'`/`ʼ`):
- `recall` ← `eslang`
- `revision` ← `takrorlash`, `bobga doir mashqlar`, `bobni takrorlash`, `повторение`
- `test` ← `nazorat`, `bilimingizni sinab`, `\btest\b` (word-ish), `sinov`
- `other` ← `tarixiy`, `javoblar`, `ответы`, `ilova`, `loyiha ishi`, `atamalar`, `lug'at`, `mundarija`

(2) **HEADER via page-containment:** with `page_start`/`page_end` present, a row A is `header` if
it strictly contains ≥2 later rows B (A.page_start ≤ B.page_start and B.page_end ≤ A.page_end,
A≠B). Null `page_end` on A → cannot be header by this rule. (3) **caps+single-page residual:** a
row that is ALL-CAPS *and* single-page (`page_end == page_start`) → `other` (a divider). **Caps
alone is NOT a header/other signal** (G10-Algebra all-caps multi-page rows are real lessons —
guard against this in a test). (4) default → `lesson`.
**Tests (structural, synthetic rows):** each keyword class; containment produces header for a
parent swallowing 2 children and NOT for a parent swallowing 1; all-caps multi-page → lesson (the
G10 guard); all-caps single-page → other; output order alignment when input is page-shuffled;
plain numbered mavzu → lesson.
**Commit:** `tocf: pure TOC row classifier (keyword → containment → caps → lesson)`

### Task 2 — Serve `entry_class` at the read choke point
**Files:** `app/schemas/toc.py` (add `entry_class: Optional[str] = None` to `TOCEntryOut`),
`app/api/v1/books.py` (`_enriched_toc_entries`), `tests/api/test_books_toc.py` (or the existing
TOC-serialization test file — locate; else new).
**Do:** In `_enriched_toc_entries`, after loading `book.toc_entries`, call
`classify_entries(book.toc_entries)` once and set `entry_out.entry_class = classes[i]` per row
(zip in `book.toc_entries` order — same order the classifier returns). Do NOT add a model column.
**Tests:** a book whose rows include a header umbrella + a javoblar + plain mavzu → the serialized
`entry_class` values match; SSE replay path (if separately tested) also carries it.
**Commit:** `tocf: compute entry_class on-the-fly in the TOC read path`

### Task 3 — Batch launch filter + `include_classes` + preview breakdown
**Files:** `app/api/v1/batch.py`, `tests/api/test_batch_launch.py` (locate the batch-launch test
file; add cases).
**Do:** Add `include_classes: list[str] | None = None` to `BatchLaunchRequest`. Validate: each
value ∈ the 6 class constants else `HTTPException(422)`. In the `toc_entry_ids is None` branch
(only there), compute `classes = classify_entries(lessons)` and filter:
`wanted = set(include_classes) if include_classes is not None else {"lesson"}`;
`targets = [t for t, c in zip(lessons, classes) if c in wanted]`. Explicit `toc_entry_ids` path
unchanged (unfiltered). In the **preview** branch, additionally return an
`excluded_by_class: {class: count}` map (over the rows NOT in `wanted`) and `target_count`, so the
FE can show why rows were dropped. Guard: preview creates no jobs (already true).
**Tests (real-DB scratch, no enqueue):** seed a book with known-class rows; default launch/preview
→ targets only lesson rows (assert count + that header/test/javoblar excluded); `include_classes=
["lesson","test"]` → widens; explicit `toc_entry_ids=[header_row]` → that row IS targeted
(override); unknown class → 422; preview returns `excluded_by_class` totals and creates 0 jobs.
**Commit:** `tocf: default batch launch to LESSON-only + include_classes opt-in + preview breakdown`

### Task 4 — FE: class chip, opt-in toggles, filtered count, preview breakdown
**Files:** `web/src/lib/types.ts` (`entry_class: string | null` on `TOCEntry`; extend preview
response type with `excluded_by_class?` + `target_count?`), `web/src/lib/api.ts` (`include_classes?`
on launch + preview body types; preview response type), `web/src/components/fleet/launcher.tsx`.
**Do:** (a) Per-row **class chip** in the choosing list (`launcher.tsx:~1335`) — small tag showing
non-lesson classes (lesson = no chip or a muted dot). (b) **Class opt-in chips** near the launch
control: `lesson` on by default, `header/recall/revision/test/other` off; selection drives an
`includeClasses` state threaded into `launchBody` (non-subset path only) as `include_classes`.
(c) The "Launch remaining N" count reflects the class filter (count rows whose `entry_class` ∈
includeClasses, excluding done). (d) Surface the preview's `excluded_by_class` (e.g. "12 rows
excluded: 8 header, 3 test, 1 other") so the operator sees why. Explicit hand-picks (checkboxes →
`toc_entry_ids`) stay unfiltered — unchanged.
**Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit` exit 0; `npm run build` clean.
**Commit:** `tocf: launcher class chips + LESSON-only default with per-class opt-in`

### Task 5 — Real-fixture accuracy gate (acceptance) + tune classifier
**Files:** `tests/services/fixtures/toc_classifier_labels.json` (new — real rows + hand labels),
`tests/services/test_toc_classifier_accuracy.py` (new), tune `app/services/toc_classifier.py`.
**Do (controller-driven, read-only from `edu_copy`):** dump real `toc_entries` for at least
G8-Geometriya (hierarchical + Javoblar), G8-Algebra (lessons-only), G7-Algebra (back-matter
included), one G5/G6 book (ALL-CAPS single-page chapters), one RU book. Hand-label each row's true
class (I inspect the rows — not delegated). The test runs `classify_entries` per book and asserts
overall accuracy **≥ 90%** (floor) AND **prints both error directions separately** — real lessons
misclassified as non-lesson (the scary one: silently drops content) vs junk misclassified as
lesson ($ leak) — plus full per-class confusion counts, no rounding. If the real data can't clear
90% or the lesson→junk direction is non-trivial, stop and surface the counts to the user rather
than forcing the number. Tune the keyword tables until the gate passes; the specific
G8-Geometriya assertion — a default batch targets ~62 mavzu rows, not 75 — is included.
(Visibility mitigates residual error: per-row chips + preview `excluded_by_class` let the operator
catch a misclassified row and hand-pick it — unfiltered — before a bulk launch.)
**Commit:** `tocf: real-fixture classifier accuracy gate + tuning`

## Finish (controller, after final whole-branch review)
- Full suite green (`uv run python -m pytest tests/ -q`; the 2 known pre-existing
  `test_failover_api.py` reds are not this lane).
- Rebase-check: `git fetch origin`; if `origin/Nggaev-v2` moved, rebase + re-run suite.
- Docs: worklog **0127** in `docs/memory/MASTER_MEMORY.md` + INDEX row (re-verify ascending order,
  0127 after 0126); close `batch-launch-lesson-filter-1` in `docs/memory/WISHLIST.md`; de-stale
  `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` (batch launch now class-filters; no DB change so
  `DATABASE.md` untouched). `git mv` this plan → `docs/superpowers/plans/shipped/`.
- PR to GK2's gate (GK2 merges, not me). Note in the PR: no migration; slot 0045 intentionally free.
