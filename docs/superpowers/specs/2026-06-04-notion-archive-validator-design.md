# Notion Archive Validator — Auto Structural Check — Design Spec

**Date:** 2026-06-04
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

After a job archives to Notion, automatically confirm the live Notion tree **matches the structure
the archive was supposed to build** — the right pages under `Homework`, the right game child under
Gamified Practices, each leaf non-empty with its file attachment(s) — and record a per-job result the
operator can trust. Best-effort: it never breaks the pipeline and never re-runs generation.

This is a **separate validator** from the LLM phase judge (`docs/.../2026-06-04-llm-phase-validator-design.md`):
job-level not per-phase, **deterministic** not LLM, post-archive not pre-`done`. They compose — the judge
guarantees each phase's *content* before archive; this confirms the already-judged content was *placed*
correctly.

## Why

`archive_job` is best-effort and idempotent; today nothing confirms the resulting tree is correct. The
two DB markers (`notion_archived_at`, `toc_entries.notion_homework_page_id`) only prove the archive
*thought* it succeeded — not that the page set, container children, or attachments are actually there.
A structural check closes that gap with bounded cost.

## Grounding facts (verified against the live code)

- `archive_job(job_id)` (notion_archive.py) runs **after `done`**, best-effort. On success it sets
  `homework_jobs.notion_archived_at` and `toc_entries.notion_homework_page_id`.
- The **expected tree is fully derivable** from the job's `phase_md` keys (done, non-`extract`, non-empty
  `output_md` — the exact filter at `notion_archive.py:188-192`) run through `_HOMEWORK_LAYOUT` +
  `PHASE_TITLES`. `_push_to_notion` skips any layout group with no present phase (`if not present:
  continue`, `:144-145`) and builds each leaf's attachments as `[pn for pn in entry["phases"] if pn in
  phase_md]` (`:144`).
- `NotionClientWrapper` (notion/client.py) exposes the reads needed: `get_child_pages(parent) ->
  [{id,title,type}]` (`:59`), `get_block_children` (`:44`), `page_has_content` (True iff any
  non-`child_page` block, `:72`). All rate-limited (~3 req/s) and cursor-paginated (no silent truncation).
- A **leaf** page holds content blocks (file + dividers + paragraphs) and no child pages; the **Gamified
  Practices container** holds child pages and no content (`find_or_create` container carries no body). So
  `page_has_content` distinguishes them.
- Archive I/O runs synchronously via `asyncio.to_thread` (notion_archive.py:199); the validator mirrors
  that pattern.

**Framing:** because a `done` job means every content phase succeeded, the empty-group-skipping path is an
**edge case** (a `done` phase that emitted empty/whitespace `output_md`, so the archive filter drops it),
not the common path. The realistic per-job variability is **which single game child** appears
(`flows.SUBJECT_GAME`: memory-match / tictactoe / jigsaw / sentence). Deriving the whole expected tree —
including per-leaf attachment counts — from `phase_keys` is the defensive-correct choice regardless.

## Design

### 1. Components

- **`app/services/notion/archive_validator.py` (new)**
  - `expected_tree(phase_keys: set[str]) -> ExpectedTree` — **pure**. Reuses `_HOMEWORK_LAYOUT` +
    `PHASE_TITLES` (imported from `notion_archive`; see §6 for the optional `layout.py` lift). Mirrors
    `_push_to_notion` exactly: per layout group, `present = [p for p in entry["phases"] if p in
    phase_keys]`; skip if empty. Produces:
    - top-level entries: each present leaf (title + **attachment count = `len(present)`**) or container
      (title + expected game-child titles via `PHASE_TITLES`).
    - **Attachment count is derived, never hardcoded** — Flashcards is 2 normally but 1 if `memory-check`
      is absent from `phase_keys`.
  - `async validate_archive(job_id) -> None` — **best-effort** orchestrator (try/except wrapping all of
    it; never raises into the pipeline). DB gate → live read → compare → record.
- **`homework_jobs.notion_validation` (new, additive nullable JSONB column + migration)** — the per-job
  result: `{status, checked_at, issues: list[str]}`.
  - `status ∈ {verified, mismatch, archive-incomplete, check-failed, skipped}`.
  - **NULL means the validator never ran** (historical / pre-feature jobs) — distinct from `skipped`
    (ran, intentionally no-op because Notion is disabled). NULL is never overloaded.
  - `jobs_repo.set_notion_validation(session, job_id, result)` — mirrors `set_notion_archived`.
- **`app/services/pipeline.py`** — invoke the validator **after the archive try/except** (see §3).

### 2. The check algorithm (`validate_archive`)

1. **Guards (mirror `archive_job`).** If `settings.notion_enabled` is false (or key missing) → record
   `skipped`, return. Load the job; if gone, return (record nothing).
2. **DB gate.** Build `phase_keys` with the **same filter** as archive (done, non-`extract`, non-empty
   `output_md`). If `notion_archived_at IS NULL` **or** `toc_entries.notion_homework_page_id IS NULL` →
   record `archive-incomplete` (a real signal now, since Notion is enabled), return.
3. **Live structure** (read via `NotionClientWrapper` inside `asyncio.to_thread`):
   - `exp = expected_tree(phase_keys)`.
   - `get_child_pages(homework_page_id)` → assert title set == `exp` top-level titles (report
     missing/extra by name).
   - If a Gamified Practices container is expected → locate its id among the homework children →
     `get_child_pages(container_id)` → assert == expected game-child titles.
   - For each expected leaf id: `page_has_content(leaf_id)` is True (non-empty), and its **first
     `N` blocks are file attachments** where `N = exp` leaf's derived attachment count (via
     `get_block_children`; the file-block type is confirmed against `blocks.make_file_upload_block`
     at plan time).
4. **Record.** No discrepancies → `verified`. Any discrepancy → `mismatch` with `issues` (human-readable,
   e.g. `"Homework missing child: Boss Arena"`, `"Flashcards: expected 2 attachments, found 1"`). Always
   `log.warning` on a non-`verified` result.

Title comparison normalizes the same way `find_or_create` does (idempotent-by-normalized-title), so
casing/whitespace differences don't cause false mismatches.

### 3. Pipeline placement (explicit — must be AFTER the archive try/except)

In `pipeline.run`, the archive hook is wrapped (pipeline.py:196-199):
```python
        try:
            await notion_archive.archive_job(job_id)
        except Exception:
            log.warning(f"[job {job_id}] notion archive hook failed (non-fatal)", exc_info=True)
```
`validate_archive(job_id)` is called **after** this `except` (≈line 200), NOT inside the `try`. If it were
inside and `archive_job` raised, the validator would be skipped — exactly the case that should record
`archive-incomplete`. The validator's own internal try/except keeps it from ever breaking the pipeline.

### 4. Error handling

Fully best-effort. Any exception inside `validate_archive` (Notion read error, throttle, transient) is
caught, logged, recorded as `check-failed`, and never propagated — the job is already `done`/archived and
must not be turned into a failure by a verification step. Pagination + throttling are handled by the
existing client.

## Testing strategy

- **Pure unit (DB-free) — the high-value core:** `expected_tree(phase_keys)` —
  - the four game variants each yield the right single Gamified Practices child;
  - full 8-phase set → CBP, Flashcards(2 attachments), Gamified Practices(3 children), Boss Arena,
    Reflection;
  - `memory-check` absent → Flashcards attachment count == 1 (the #1 regression guard);
  - a group with no present phase is omitted entirely.
- **Acceptance smoke (real Notion):** a freshly-archived live job → `validate_archive` records
  `verified`; then delete one child page in Notion and re-run → `mismatch` naming the missing page;
  Notion disabled → `skipped`.
- **Migration:** additive `notion_validation` column proven by `alembic upgrade head` (plan confirms the
  head via `alembic heads` — expected `0019` / `a7c1e9d2b4f8`, new revision `0020`).

## Scope

**IN:** auto in-pipeline structural validation (DB gate + live tree compare), per-job `notion_validation`
result, `skipped`/`archive-incomplete`/`mismatch`/`check-failed`/`verified` states.

**OUT (deferred):**
- **Content-fidelity** (uploaded `.md` byte-equals `output_md`) — requires fetching Notion's expiring file
  URL + byte compare; high flakiness, low marginal value once a file block is confirmed present.
- **Lesson placement (D)** — adopted-human-page vs `Generated Lessons` parent — depends on the unmerged
  `lesson-matching` branch (not on `Nggaev-v2`).
- **Auto re-archive** on mismatch.
- **Operator-console surfacing** of `notion_validation` — queryable in DB for v1; a small frontend
  follow-up. **Captured in WISHLIST so it isn't forgotten.**

## Risks / open items

- **Import shape (optional refinement).** `archive_validator` importing `_HOMEWORK_LAYOUT`/`PHASE_TITLES`
  from `notion_archive` pulls that module's heavier deps (SessionLocal, repos, client) into the otherwise
  pure `expected_tree`. It's DB-free at import (conftest sentinels cover it), so unit tests pass. If we
  want `expected_tree` genuinely dependency-light, lift the two constants into a tiny
  `app/services/notion/layout.py` that both modules import. Default: import from `notion_archive` (less
  churn to shipped code); revisit only if the import weight bites.
- **Title normalization** must match `find_or_create`'s normalization, or correctly-archived pages could
  read as mismatches. Confirmed/locked at plan time against `page_creator.find_or_create`.
- **File-block type** for the attachment-first assertion is confirmed against
  `blocks.make_file_upload_block` at plan time.
