# Notion Lesson-Page Collision Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop distinct lessons that share a title from collapsing onto one Notion page, and recover the 49 homeworks already lost to it.

**Architecture:** `_push_to_notion` resolves a lesson page by **title alone** (`find_or_create`, which lowercases and strips `(N)` suffixes). Uzbek/Russian maths textbooks reuse rubric headings — `Вспомните`, `Подумайте. Проблемное задание`, `Текстовые задачи`, `ПОВТОРЕНИЕ 1` — as section titles, and `section_number` is NULL for exactly those rows. Every such lesson resolves to the same page; the first job populates it, the rest hit `page_has_content` and silently skip — yet still get stamped `notion_archived_at`. Identity moves from the Notion title to the DB: a section that already owns a page reuses it by id, and a section whose title is ambiguous within its book gets a page-number suffix.

**Tech Stack:** Python 3.14, SQLAlchemy async, pytest. No new dependencies, no migration.

## Approach & key decisions

- **Verified, not assumed.** 2026-08-05: 184 jobs stamped archived → **135** distinct Homework pages; 49 lessons have no content in Notion. Ownership confirmed by content comparison (the earliest-created job's rendered markdown matches the page byte-for-byte; the other sharers' does not). Loss is **skip**, not overwrite — `notion_archive.py:166-168` returns without writing when `replace=False`. Nothing in Notion is corrupted.
- **Disambiguator = `page_start`, chosen by measurement.** Across the 56 colliding rows: `chapter_number` still leaves 4 collisions; `page_start` and `order_index` both fully disambiguate; neither is ever NULL. `page_start` wins over `order_index` because it is meaningful to a human browsing Notion ("· p.42" locates the lesson in the textbook).
- **Suffix only when ambiguous** — never unconditionally. Unconditional suffixing would rename every lesson, so the next archive would no longer match the 135 existing pages and would create a duplicate beside each one, orphaning all the good content. A title unique within its book keeps its plain name.
- **Identity from the DB beats identity from the title.** When `toc_entries.notion_homework_page_id` is already set, reuse it directly and skip title resolution entirely. This is what protects the 9 legitimate owner pages, whose titles *are* ambiguous and which would otherwise be re-keyed onto new suffixed pages.
- **Ambiguity is scoped to the book, not to `subject|grade`.** Two collisions span Part I and Part II of the same textbook (`…_RU.pdf` / `…_RU-2.pdf`), which share a `Generated Homeworks` container. Scoping to the book alone would leave those two colliding, so the check must span every book that maps to the same container. Task 2 handles this by scoping to `(subject, grade, language)`.
- **Rejected: a `notion_lesson_page_id` column.** It would make ownership explicit but needs a migration and a backfill for rows we can already disambiguate from data in hand. Not worth it for pass 1.
- **Data repair is separate from the code fix and runs second.** The 47 non-owner sections must have their false stamps cleared before they can re-archive; the 9 owners must keep theirs. Doing this before the code fix would let a re-archive collide all over again.
- **Order matters: fix → repair → map → re-archive.** Re-archiving before the fix reproduces the bug. Adding the `ru:matematika` mapping before the fix would archive the 6 pending jobs into colliding pages.

## Global Constraints

- Worktree `/Users/macmini5/Documents/HCGA-notion-fix` on `fix/notion-lesson-page-collision`, cut from `origin/Nggaev-v2` @ `2203f6e`. Verify before EVERY commit: `[ "$(git rev-parse --abbrev-ref HEAD)" = "fix/notion-lesson-page-collision" ] || exit 1`.
- Stage only the files each task lists. Never `git add -A`.
- `uv run python -m pytest tests/ -q` — baseline must stay green.
- **No Notion writes in Tasks 1–3.** Tasks 4–6 are outward-facing and each needs explicit operator go-ahead.
- The fleet runs git vintage `2203f6e`; workers must be pulled/restarted before the fix is live for new jobs.
- Trailer: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `app/repositories/toc_entries.py` | New `title_is_ambiguous(...)` query. |
| `app/services/notion_archive.py` | Reuse a known page id; apply the suffix when ambiguous. |
| `app/services/notion/page_creator.py` | Unchanged — title matching stays; callers supply a unique title. |
| `tests/services/test_notion_lesson_collision.py` | New. The collision, the suffix, and the reuse path. |
| `scripts/repair_notion_collisions.py` | New. Dry-run-by-default data repair. |

---

### Task 1: Prove the collision in a test

**Files:** Test: `tests/services/test_notion_lesson_collision.py`

**Interfaces:** Produces the fake-client harness later tasks reuse: `FakeNotion` recording `create_page` / `append_block_children` calls, injected via `_push_to_notion`'s `find_or_create=` parameter and a stub client.

- [ ] **Step 1: Write the failing test** — two sections, same title, different `page_start`; assert two distinct lesson pages are created and both receive content. Expected today: one page, one write.
- [ ] **Step 2: Run it** — `uv run python -m pytest tests/services/test_notion_lesson_collision.py -v`. Expected: FAIL, second lesson skipped.
- [ ] **Step 3: Commit the RED test** with `git commit -m "test(notion): reproduce the lesson-title collision"` so the defect is recorded independently of its fix.

### Task 2: `title_is_ambiguous` repository query

**Files:** Modify `app/repositories/toc_entries.py`; test in the same new test file.

**Interfaces:** Produces
`async def title_is_ambiguous(session, *, subject, grade, language, title) -> bool` —
True when more than one `toc_entry` across every book matching `(subject, grade, language)` normalizes to the same title. Normalization must match `page_creator._normalize` exactly (lowercase, trim, strip trailing `(N)`); import it rather than re-implementing, or the two will drift.

- [ ] Failing test: two books, same `(subject, grade, language)`, same title → True; a unique title → False; same title in a *different* grade → False.
- [ ] Implement, run, commit.

### Task 3: Wire reuse + suffix into `archive_job`

**Files:** Modify `app/services/notion_archive.py` (`archive_job` ~line 265, `_push_to_notion` ~line 143, `_push_with_retry`).

**Interfaces:**
- `_push_to_notion(..., homework_page_id: str | None = None)` — when given, use it as `homework_id` and skip container/lesson/Homework resolution.
- `archive_job` computes `lesson_title`, and when `title_is_ambiguous` appends `f" · p.{section.page_start}"` (only if `page_start` is not None; fall back to `order_index` if it is).

- [ ] Failing tests: (a) ambiguous title → suffixed page; (b) section with an existing `notion_homework_page_id` → reused, zero `find_or_create` calls; (c) unique title → unchanged plain title (regression guard for the 135 good pages).
- [ ] Implement, run the FULL suite, commit.
- [ ] **RED-prove the suffix guard bites:** force `title_is_ambiguous` to return False, confirm the collision test fails, restore. `grep` for the mutation before trusting a green result.

### Task 4: Data repair script (dry-run default) — OUTWARD-FACING, needs go-ahead

**Files:** Create `scripts/repair_notion_collisions.py`.

For each group of `toc_entries` sharing a `notion_homework_page_id`: keep the section whose stamped job has the earliest `completed_at` (the proven content owner); for the rest, NULL `notion_homework_page_id` and `notion_archived_job_id`, and clear the job's `notion_archived_at` so it becomes re-archivable.

- [ ] `--apply` required to write; default prints the exact plan.
- [ ] Test with a seeded scratch DB (`RUN_DB_INTEGRATION=1`), asserting the owner is untouched and exactly the non-owners are cleared.
- [ ] **Show the operator the dry-run output and get explicit approval before `--apply`.**

### Task 5: Subject-page mapping — OUTWARD-FACING, needs go-ahead

Add `ru:matematika|5` → `3b399838-1c76-8135-a4b3-eda1efefabf4` and `ru:matematika|6` → `3b399838-1c76-819c-b3b3-d611a6184662` to this host's `NOTION_SUBJECT_PAGES`. Both derived read-only from archived lesson pages and confirmed by ancestry (`Generated Homeworks < Математика < {5,6} - класс < Grade < Lessons`), unanimous across 8 sampled lessons per grade.

- [ ] `.env` is shared machine config read by the running head and other sessions — confirm before editing, and note that the head needs a restart to pick it up.

### Task 6: Re-archive — OUTWARD-FACING, needs go-ahead

- [ ] Re-archive the 47 repaired sections + the 6 never-pushed jobs, **sequentially** (concurrent Notion pushes hit rate limits).
- [ ] **Never `--force`/`replace`** on this run: force clears and rewrites, which on a still-colliding page would destroy the owner's content. Plain re-archive is safe by construction.
- [ ] Verify after: distinct Homework page ids == archived job count for 2026-08-05.

## Finish

Full suite green; rebase-check against `origin/Nggaev-v2`; PR (do not self-merge); worklog + INDEX row; `git mv` this plan to `shipped/`; de-stale `docs/HOW_IT_WORKS.md` and `docs/CODE_MAP.md` where they describe Notion archival.

## Out of scope (filed, not fixed here)

Markdown→Notion converter gaps found in the same audit: numbered lists and tables become run-on paragraphs (all 190 `practice-tictactoe` grids destroyed), `####` and `>` survive as literal text, nested bullets flatten. Real defects, unrelated to page identity — separate lane.
