# Handoff: Notion archival — structure & how to validate "archived as expected"

**Audience:** the session planning the content validator.
**Purpose:** so the validator can confirm a generated homework packet was archived into Notion correctly.
**Status:** written 2026-06-04. The lesson-matching *placement* logic is on branch `lesson-matching` (pending merge to `Nggaev-v2`); the `Homework` subtree it describes is already on `Nggaev-v2` (shipped worklog 0029).

## 1. What this work changed (and what it didn't)

Two layers — keep them separate:

- **Lesson-page *placement* (NEW, branch `lesson-matching`):** decides *where the `Homework` page hangs* under a subject. It either **adopts a human-built lesson page** (when the app's lesson title uniquely word-matches one) or creates an app-owned **`Generated Lessons`** container and puts the lesson there.
- **The `Homework` subtree itself (UNCHANGED — shipped worklog 0029):** the grouped page layout under `Homework`. The validator mostly cares about *this*, and it is identical on `Nggaev-v2` and the branch.

So: **the structure the validator checks is stable; only the lesson page's parent changed.**

## 2. Archive entry point & flow

`app/services/notion_archive.py::archive_job(job_id)` — called by the pipeline **after a job reaches `done`**. Best-effort (never raises into the pipeline; failures are logged).

Guards / behavior:
- No-ops if `settings.notion_enabled` is false or `settings.notion_api_key` missing.
- **Idempotent:** returns early if `job.notion_archived_at is not None` (already archived).
- Resolves the **subject page** via `_resolve_subject_page_id(notion_subject_pages, subject, grade, book.original_filename)` — history splits jahon/ozbekiston by filename keyword.
- Builds `phase_md = {phase_name: output_md}` from `phase_outputs` rows that are **`status="done"`, not `extract`, and have non-empty `output_md`**. (This set = "the phases that should appear in Notion.")
- Calls `_push_to_notion(...)`, then on success writes the two DB success markers (below) and commits.

## 3. The Notion tree (what "as expected" looks like)

**Matched** (app lesson uniquely word-matches a human page):
```
Subject page  (e.g. "Tarix (Jahon Tarixi)")
└─ <human lesson page>            ← adopted (e.g. "1-mavzu. German qabilalari…6")
   └─ Homework                    ← id stored in toc_entries.notion_homework_page_id
      ├─ Case-Based Preview       (leaf)
      ├─ Flashcards               (leaf — holds flashcards + memory-check INLINE)
      ├─ Gamified Practices       (container, no body)
      │   ├─ Real-Life Challenge
      │   ├─ Error Detection
      │   └─ <one subject game>   (Memory Matching | TicTacToe | Jigsaw Matching | Sentence Filling)
      ├─ Boss Arena               (leaf)
      └─ Reflection               (leaf)
```
**Unmatched** (fallback): identical, except the lesson sits at `Subject ▸ Generated Lessons ▸ "<N Title>" ▸ Homework ▸ …`.

**Leaf page contents:** attachments **first** (the phase `.md` uploaded as a file block, at the very top), then a divider, then the rendered markdown blocks. The **Flashcards** leaf has **two** attachments at top (flashcards.md + memory-check.md) then two content sections.

## 4. Phase → page-title map (`PHASE_TITLES`) and the non-1:1 rules

| phase_name | Notion title | where it appears |
|---|---|---|
| case-based-preview | Case-Based Preview | own leaf |
| flashcards | Flashcards | Flashcards leaf |
| memory-check | (Memory Check) | **inline inside the Flashcards leaf**, not its own page |
| practice-rlc | Real-Life Challenge | child of Gamified Practices |
| practice-error-detection | Error Detection | child of Gamified Practices |
| practice-memory-match | Memory Matching | child of Gamified Practices |
| practice-tictactoe | TicTacToe | child of Gamified Practices |
| practice-jigsaw | Jigsaw Matching | child of Gamified Practices |
| practice-sentence | Sentence Filling | child of Gamified Practices |
| boss-arena | Boss Arena | own leaf |
| reflection | Reflection | own leaf |

**Non-obvious:** memory-check has no page (folded into Flashcards); the six games live under the **Gamified Practices** container, and a job has exactly **RLC + Error Detection + one** subject game (so Gamified Practices has ≤3 children). A layout group is **omitted entirely** if none of its phases were generated.

## 5. DB success markers (necessary conditions)

- `homework_jobs.notion_archived_at` — timestamp, set only on a fully successful archive (NULL = not archived).
- `toc_entries.notion_homework_page_id` — the `Homework` page id, set on success.

Query, e.g.:
```sql
SELECT j.id, j.status, j.notion_archived_at, t.notion_homework_page_id
FROM homework_jobs j JOIN toc_entries t ON t.id = j.toc_entry_id
WHERE j.id = :job_id;
```

## 6. Validation checklist the validator can implement

**A. DB gate (cheap, necessary):**
- `status = 'done'` AND `notion_archived_at IS NOT NULL` AND `notion_homework_page_id IS NOT NULL`.
- If `notion_archived_at` is NULL but Notion is enabled → archival did not complete (investigate). If Notion disabled, NULL is expected (not an error).

**B. Notion structure (as-expected), via the Notion API/MCP, fetching `notion_homework_page_id`:**
- Compute the **expected page set** from the job's `phase_md` keys mapped through §4 (apply the inline/container rules).
- Assert the `Homework` page's children == that expected set (titles); Gamified Practices children == present games; no extra/missing pages.
- For each leaf (and game child): first block is a **file** attachment, page is **non-empty** (has content blocks after the divider). Flashcards leaf has **two** file blocks at top.

**C. Content fidelity (optional, stronger):**
- The attached `.md` on each leaf should equal that phase's `phase_outputs.output_md`. (Validator can compare the stored markdown to the uploaded file, or at least confirm a file is present per phase.)

**D. Lesson placement (this session's new behavior, optional):**
- The `Homework` page's parent (the lesson page) is **either** a child of the subject page directly (adopted human page) **or** under a `Generated Lessons` container under the subject (fallback). Walking the parent chain tells you which.

## 7. Idempotency / caveats the validator must know

- **Best-effort:** a `done` job may legitimately be un-archived (Notion down/disabled) — `notion_archived_at` NULL ≠ generation failure.
- **Idempotent at the leaf level:** re-archiving **skips any page that already has content** (`page_has_content`), and skips the whole job if `notion_archived_at` is set. So the validator should not expect re-runs to overwrite.
- **Rate limits:** the Notion client is throttled (~3 req/s); large structure reads paginate (cursor-looped) — no silent truncation.
- **Branch status:** the lesson-matching placement logic is on branch `lesson-matching` pending merge to `Nggaev-v2`; the `Homework` subtree (everything the structure check inspects) is already on `Nggaev-v2`.

## 8. Key code locations

- `app/services/notion_archive.py` — `archive_job`, `_push_to_notion`, `_HOMEWORK_LAYOUT`, `PHASE_TITLES`, `_resolve_subject_page_id`.
- `app/services/notion/lesson_match.py` — `match_lesson(app_title, human_pages) -> str | None`, `CONTAINER_TITLE = "Generated Lessons"` (the new matching).
- `app/services/notion/client.py` — `NotionClientWrapper` (`get_child_pages`, `page_has_content`, `get_block_children`, `upload_bytes`).
- `app/services/notion/page_creator.py` — `find_or_create` (idempotent by normalized title).

## 9. Quick end-to-end example (verified 2026-06-04)

Real gemini job `defe06b6` (history / humanities, grade-7 world history) generated 8 phases — `case-based-preview, flashcards, memory-check, practice-rlc, practice-error-detection, practice-memory-match, boss-arena, reflection`. Expected Notion structure under its `Homework` page:
- Case-Based Preview (leaf)
- Flashcards (leaf, with flashcards + memory-check inline → 2 attachments)
- Gamified Practices (container) → Real-Life Challenge, Error Detection, Memory Matching
- Boss Arena (leaf)
- Reflection (leaf)

(No TicTacToe/Jigsaw/Sentence pages — history's subject game is memory-match.) This is the shape a validator should reconstruct from `phase_md` and assert against the live Notion tree.
