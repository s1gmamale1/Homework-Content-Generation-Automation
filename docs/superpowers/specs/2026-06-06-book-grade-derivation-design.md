# Book grade derivation + silent-skip visibility — Design

**Date:** 2026-06-06
**Branch:** Nggaev-v2
**Status:** approved (design locked; reviewer-endorsed + verified)

## Problem

Job `3b026cfb` (`math-algebra`, `done`) was never archived to Notion. Root cause, verified against live code + DB:

- The book `62865c70` (`7-sinf_Algebra_2022_…pdf`) has **`grade = NULL`**, even though the filename states the grade.
- The Notion archive resolves the target page by the key `{subject}|{grade}` (`notion_archive.py:60`). `_resolve_subject_page_id` short-circuits on a falsy grade: `if not grade: return None` (`:58-59`).
- `archive_job` then logs `no subject-page mapping for subject=math-algebra grade=None — skipping` (`:188-192`) and returns. The job stays `done`, `notion_archived_at` stays NULL — a **silent skip** with no error on the row.

This is the documented silent-skip failure mode, here triggered by a NULL grade rather than a missing map entry.

### How books become gradeless

`ingest_pdf` (`books.py:45`) is the single shared creation path for both ingest routes. The **upload** route (`upload_book:100`) takes `grade` as an optional form field (`Form(default=None)`), so an operator who uploads without typing a grade produces a NULL-grade book — even when the grade is plainly in the filename. The **Fetch-from-Notion** route (`book_from_notion:144`) passes the grade the UI navigated to, so it is rarely affected.

### Verified facts (DB + code, 2026-06-06)

- **2 gradeless books**, both filename-parseable: `english` `8-sinf_Ingliz_tili…`→8, `math-algebra` `7-sinf_Algebra…`→7.
- **6 `done` + unarchived jobs total:** 3 on the gradeless books (2 english, 1 algebra) + 3 graded-but-unarchived (`history|7`, `kimyo-g7-11|8`, `kimyo-g7-11|9`) that completed before the map/feature existed.
- All five relevant `{subject}|{grade}` keys are currently **mapped**, so a re-archive pass should resolve all six (modulo the history dict-keyword match — see Edge cases).
- **`book.grade` is written in 4 places but READ in exactly one: the Notion archive** (`notion_archive.py:184`). `pipeline.py` references `grade` nowhere. The English CEFR level is leveled by the model **from the PDF source** (`prompts.py:34-39`, a static instruction string) — `book.grade` is never interpolated into any prompt. **Therefore the NULL grade did not affect any generated content; its only effect is blocking archiving.** No regen is entangled.

## Goals

1. Stop producing gradeless books: derive the grade from the filename at ingest when the caller does not supply one.
2. Heal existing gradeless rows (backfill) so the current 3 jobs can archive.
3. Make any *remaining* silent skip visible (best-effort derivation can still miss; an unmapped `subject|grade` can still skip), so a done-but-unarchived job is never invisible again.

## Non-goals

- No change to generation, prompts, difficulty, or CEFR leveling.
- Not making `grade` a required upload field (derivation + visibility is the chosen mechanism; a hard requirement is a separate UX decision).
- Notion anchor auto-resolve (crawl the tree instead of the hand-maintained map) remains a future WISHLIST item.
- The re-archive sweep is a one-off operational step, not a new permanent startup hook or endpoint.

## Design

### Commit 1 — Derive at ingest + backfill (closes the cause)

**New pure helper** `derive_grade_from_filename(name: str) -> str | None` in `app/services/grade.py`:
- Regex `(\d{1,2})\s*[-_ ]?\s*(?:sinf|klass|класс)`, case-insensitive.
- First match wins; the numeric group is clamped to the inclusive range **1–11** (the supported band) — anything outside (e.g. `12-sinf`, `0`) → `None`.
- No match → `None`. Never raises; tolerant of `None`/empty input (returns `None`).
- Returns the grade as a **string** (matches the column type), e.g. `"7"`.

**Wire into `ingest_pdf`** immediately before `books_repo.create` (`books.py:70`):
```python
grade = grade or derive_grade_from_filename(filename)
```
Explicit caller grade always wins. The dedup hit returns earlier (`:67-68`), so it is unaffected. Fetch-from-Notion is unaffected (it passes a grade).

**Backfill** — an **Alembic data migration**:
- `UPDATE books SET grade = <derived> WHERE (grade IS NULL OR grade = '')`, computing the derived grade per row from `original_filename` using the **same regex inlined into the migration** (Alembic best practice: a migration is a frozen snapshot and must not import evolving app code; the small duplication is intentional and acceptable).
- Rows whose filename yields no grade are left untouched (still NULL → Commit 2 surfaces them if they ever archive-skip).
- Auto-runs on `alembic upgrade head`, so every host self-heals.
- `downgrade()` is a no-op (cannot reliably know which grades were absent before; documented in the migration).

### Commit 2 — Surface the silent skip (safety net)

**Schema** — Alembic migration adding a nullable column to `homework_jobs`:
- `notion_skip_reason VARCHAR NULL` — human-readable reason the archive skipped, or NULL when archived / not-yet-attempted / archiving disabled.
- `upgrade()` = `op.add_column(...)`; `downgrade()` = `op.drop_column("homework_jobs", "notion_skip_reason")`. (This is a schema migration with a real downgrade — do NOT copy the *backfill* migration's no-op downgrade pattern.)

**`archive_job` wiring** — set/clear the reason ONLY on resolvable outcomes:

| Branch (notion_archive.py) | Action |
|---|---|
| `notion_enabled = False` (`:166`) | leave untouched (intentional) |
| `notion_api_key` missing (`:168`) | leave untouched (config, not per-job) |
| job gone / already archived (`:177`) | leave untouched (idempotent) |
| `book is None or section is None` (`:181`) | **SET** reason `"book/section row missing"` (real data fault) |
| no `subject_page_id` (`:187`) | **SET** reason `"no Notion page for {subject}|{grade}"` |
| no completed phase outputs (`:201`) | **SET** reason `"no completed phase outputs"` |
| successful archive (`:214-217`) | **CLEAR** reason (set NULL) alongside `set_notion_archived` |

Implementation notes:
- **The first session block (`:174-199`) is read-only and never commits** — the success path commits in a *separate* fresh session at `:214-217`. So the in-block stamps (no-mapping `:187`, missing-row `:181`) MUST `await session.commit()` before returning, or the stamp silently rolls back and Commit 2 no-ops on exactly the no-mapping path we are fixing. This is the load-bearing detail.
- The no-phases branch (`:201`) executes *after* that block closes, so it stamps via a fresh short session (snapshot pattern, consistent with the SSE-session discipline) and commits there.
- The success-path clear of `notion_skip_reason` rides the existing commit at `:214-217` (same session as `set_notion_archived`).

**Repository** — add `jobs_repo.set_notion_skip_reason(session, job_id, reason: str | None)`. `set_notion_archived` also clears the reason (NULL).

**API** — `JobOut` gains `notion_skip_reason: str | None = None`. It is **optional with a default**, so existing `JobOut.model_validate` call sites / test stubs need no new required field (no 500-regression risk).

**Frontend** — a concise indicator on the job page: when `status === "done"` and `notion_skip_reason` is set, show a small neutral note ("Not archived to Notion: {reason}"). No new route, no blocking UI.

### Operational — one-pass re-archive sweep (after both commits land)

Run once: for every `done` job with `notion_archived_at IS NULL`, call `archive_job(job_id)` (idempotent, best-effort). With the current map, this archives all six; any residual gets a `notion_skip_reason`. Delivered as a small re-runnable script `scripts/rearchive_unarchived.py` (query → loop → `await archive_job`), not wired into startup.

## Data flow

```
upload (no grade) ─┐
                   ├─► ingest_pdf ─► grade = grade or derive_grade_from_filename(filename) ─► books_repo.create
fetch (has grade) ─┘                                                   (explicit wins)

job done ─► archive_job ─► resolve {subject}|{grade}
                              ├─ resolved  ─► push ─► set_notion_archived + clear skip_reason
                              └─ skip      ─► set notion_skip_reason (resolvable branches only)
```

## Edge cases

- **Derived-but-unmapped grade** (e.g. a `5-sinf` book where `subject|5` isn't mapped): derivation succeeds, archive still skips, Commit 2 surfaces it. Correct synergy.
- **Unparseable filename** (`algebra_final.pdf`): grade stays NULL, archive skips, Commit 2 surfaces it. No silent loss.
- **History dict form** (`history|7` → `{jahon, ozbekiston}`): a graded history job still requires the filename to match a keyword; if it doesn't, it skips and is marked — no mis-filing.
- **Cyrillic names**: `класс` is in the regex; `N-klass` Latin also covered.
- **Re-run safety**: backfill migration and the re-archive sweep are both idempotent.

## Testing

DB-free, matching the suite's conventions. No CLI smoke (does not touch generation).

- **Helper unit tests** (`tests/services/test_grade.py`): `7-sinf_Algebra…`→"7", `8-sinf_Ingliz…`→"8", `9 sinf`→"9", `5-klass`→"5", `7-класс`→"7", `algebra_final.pdf`→None, `12-sinf`→None, `0-sinf`→None, ``""``→None, `None`→None.
- **Ingest test**: `ingest_pdf` with `grade=None` and a `7-sinf…` filename calls `books_repo.create` with `grade="7"`; with an explicit `grade="9"` the explicit value is kept (derivation not consulted).
- **Archive skip-marker tests** (mocked session/repo): no-mapping → `set_notion_skip_reason` called with the `{subject}|{grade}` reason; `notion_enabled=False` → `set_notion_skip_reason` NOT called; successful archive → reason cleared.
- **Backfill migration**: covered by a focused test that runs the inlined derivation over representative filenames (the regex is unit-tested via the helper; the migration reuses the same pattern).

## Rollout / ordering

1. Commit 1 (helper + ingest wiring + backfill migration) → `alembic upgrade head`.
2. Commit 2 (skip_reason column + archive wiring + JobOut + FE) → `alembic upgrade head`.
3. Run `scripts/rearchive_unarchived.py` once.
4. Restart server (to load the new archive/JobOut code) — same restart already pending for 0038/0040/0041.

## Worklog

On completion: worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row; the gradeless-book root cause closes the `notion-subject-page-map` silent-skip concern's NULL-grade arm.
