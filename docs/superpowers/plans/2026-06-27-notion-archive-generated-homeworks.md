# Notion archive: always file under "Generated Homeworks" (drop human-page matching)

**Date:** 2026-06-27 · **Author:** gatekeeper (spec for implementer) · **Size:** small, 1 commit

## Approach & key decisions

Today `_push_to_notion` (`app/services/notion_archive.py:141-147`) calls `match_lesson(lesson_title, human_pages)` to adopt a **human-built** Notion lesson page; on a miss it creates the lesson under a `Generated Lessons` container. The match is a strict unique content-word superset and scatters lessons unpredictably.

**Two user-confirmed changes:**
1. **Stop adopting human-made lesson pages entirely** — *always* use the container path. Every homework lands in one predictable app-owned bucket per subject, regardless of any matching human page that exists.
2. **Rename the container** `"Generated Lessons"` → `"Generated Homeworks"`.

Consequences (all confirmed with user):
- Human-page adoption is intentionally gone. Idempotency is unchanged (still find-or-create by title; re-archiving reuses the same `Generated Homeworks → <lesson> → Homework`).
- `match_lesson` + the whole `lesson_match.py` matcher become dead code → **delete** them (and `test_lesson_match.py`). The only surviving constant, `CONTAINER_TITLE`, moves into `notion_archive.py`.
- Slight bonus: drops the per-archive `get_child_pages(subject)` lookup → one fewer Notion API call per homework.

**Out of scope — gatekeeper-run AFTER this merges:** a one-time script renames the existing `"Generated Lessons"` pages already in Notion → `"Generated Homeworks"`, so past homeworks unify under the new name (otherwise each subject would have an old `Generated Lessons` *and* a new `Generated Homeworks`). NOT part of this PR; do not script Notion changes here.

**Verified against code (grep):**
- `match_lesson` used only at `notion_archive.py:142`. `CONTAINER_TITLE` at `:146` + `lesson_match.py:13,50`. No other importers.
- Affected tests: `tests/services/test_notion_archive.py` (both the match-adoption path and the miss path), `tests/services/test_lesson_match.py` (matcher unit).

## Task 1 — unconditional `Generated Homeworks` path; delete the matcher

**Files:**
- Modify: `app/services/notion_archive.py`
- Delete: `app/services/notion/lesson_match.py`
- Rewrite: `tests/services/test_notion_archive.py`
- Delete: `tests/services/test_lesson_match.py`

**Steps (TDD):**

- [ ] **Step 1 (RED): rewrite `test_notion_archive.py` to the new contract.** Collapse the two existing tests (the "match → adopt human page" case and the "miss → Generated Lessons" case) into the single unconditional contract: **regardless of the subject page's existing children**, the parent chain is `Subject → "Generated Homeworks" → <lesson_title> → "Homework" → <grouped layout>`.
  - Assert `find_or_create` is invoked with `"Generated Homeworks"`, then `lesson_title`, then `"Homework"` (mirror the existing call-args assertions at `:162-164`, just renamed).
  - **Delete the human-page-adoption test** (`:128-142`, "no Generated Lessons container, adopts the matching page") — that behavior no longer exists. Add an assertion in its place that even when the mock subject page **already has a child whose title equals `lesson_title`**, the archive still routes through `"Generated Homeworks"` (proves adoption is gone).
  - Run `uv run python -m pytest tests/services/test_notion_archive.py -q` → fails.

- [ ] **Step 2: edit `notion_archive.py`.**
  - Add a module constant near the top: `CONTAINER_TITLE = "Generated Homeworks"`.
  - Change the import at `:26` to drop `lesson_match` entirely (remove `from app.services.notion.lesson_match import match_lesson, CONTAINER_TITLE`).
  - Replace the match block (`:141-147`) with the unconditional path:
    ```python
    container_id, _ = find_or_create(client, subject_page_id, CONTAINER_TITLE)
    lesson_id, _ = find_or_create(client, container_id, lesson_title)
    homework_id, _ = find_or_create(client, lesson_id, "Homework")
    ```
    (i.e. remove `human_pages = client.get_child_pages(subject_page_id)`, the `match_lesson(...)` call, and the `if hit is not None: … else:` branch — keep only the container → lesson lines, then the existing `Homework` line).
  - Update the module docstring (`:4`) + the `_push_to_notion` docstring (`:136-140`) to describe the new unconditional `Generated Homeworks → <lesson> → Homework` structure (no matching).

- [ ] **Step 3: delete the dead matcher.** `git rm app/services/notion/lesson_match.py tests/services/test_lesson_match.py`. (Confirm nothing else imports it: `grep -rn "lesson_match\|match_lesson" app/ tests/` → only the now-removed lines.)

- [ ] **Step 4 (GREEN):** `uv run python -m pytest tests/services/test_notion_archive.py tests/services/test_notion_archive_skip.py tests/services/test_pipeline_notion_hook.py -q` → green. Then full suite `uv run python -m pytest tests/ -q`.

- [ ] **Step 5: commit** (stage only these files):
  ```bash
  git add app/services/notion_archive.py tests/services/test_notion_archive.py
  git rm app/services/notion/lesson_match.py tests/services/test_lesson_match.py
  git commit -m "feat(notion): always file homeworks under 'Generated Homeworks'; drop human-page matching"
  ```

## Acceptance
- Mock-client archive (the `test_notion_archive` style) proves the unconditional `Generated Homeworks → <lesson> → Homework` tree **even when** a subject child page already matches the lesson title (adoption gone). Full suite green.

## Finish (per CLAUDE.md)
- Worklog + INDEX row (next-free id — verify at finish; fetch-1 took 0095).
- De-stale any doc describing the old "match → adopt human page / Generated Lessons" archive flow (`docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`).
- `git mv` this plan into `docs/superpowers/plans/shipped/`.
- Branch + PR → gatekeeper (no self-merge). **Note in the PR description** that the one-time Notion page-rename (`Generated Lessons` → `Generated Homeworks`) is a separate gatekeeper-run migration after merge.
