# Claim jobs in ascending lesson order (fix randomized lesson generation)

**Date:** 2026-06-29 · **Author:** gatekeeper (spec for implementer) · **Size:** small, 1 commit · **Risk:** touches the claim gate (sensitive — RED-prove the test)

## Approach & key decisions

**Symptom:** within a batch, lessons generate/archive in random order → they appear scrambled in Notion (Notion orders child pages by creation time).

**Root cause (verified):** `jobs.py:389-402` `claim_next_job` orders the pick by
`.order_by(HomeworkJob.priority.desc(), HomeworkJob.scheduled_at.asc())`. Every job in a batch is launched together → same `priority` and ~identical `scheduled_at` → the tiebreaker collapses and `FOR UPDATE SKIP LOCKED` returns an **arbitrary** pending row. There is no lesson-sequence ordering, so workers claim lessons in effectively random order.

**Fix:** add the lesson's TOC sequence as a tiebreaker, **ascending (1 → 26)**. The clean field is `toc_entries.order_index` (non-nullable `Integer`, already indexed by `ix_toc_entries_book_id_order` = `(book_id, order_index)`) — it is the canonical lesson order. Do **not** parse `section_number` (nullable; text like `'2-3'`).

**Mechanism decision — correlated scalar subquery, NOT a join.** Order by a scalar subquery that looks up `order_index` for the job's `toc_entry_id`, so the `SELECT ... FOR UPDATE SKIP LOCKED` stays on `homework_jobs` only — a JOIN would drag `toc_entries` into the lock/skip-locked semantics. (Fallback if the subquery misbehaves under `FOR UPDATE`: JOIN `toc_entries` + `.with_for_update(skip_locked=True, of=HomeworkJob)`. Prefer the subquery; the implementer confirms which passes the scratch-DB test.)

With `SKIP LOCKED`, Postgres evaluates rows in ORDER BY order and skips locked ones — so worker A takes lesson 1, worker B skips the locked row and takes lesson 2, etc. → lessons are claimed in ascending order across the fleet.

**Load-bearing facts verified against code/DB:**
- `claim_next_job` order clause: `app/repositories/jobs.py:399`.
- `toc_entries.order_index`: non-nullable `Integer` (`app/models/toc_entry.py:26`), indexed `(book_id, order_index)` (`:31`).
- `HomeworkJob.toc_entry_id` FK exists (used already at `archive_job`). A `/generate` job *may* have `toc_entry_id` NULL → the subquery returns NULL → Postgres `ASC` sorts NULLS LAST, so batchless jobs fall to the end of the tiebreak (harmless; priority + scheduled_at still order them). Keep that behavior (no special-casing).

## Task 1 — add ascending lesson-order tiebreaker to the claim pick

**Files:**
- Modify: `app/repositories/jobs.py` (the `pick_stmt` order_by, ~`:399`)
- Test: `tests/integration/test_claim_order.py` (new, real-DB)

**Steps (TDD):**

- [ ] **Step 1 (RED): write `tests/integration/test_claim_order.py`** (scratch-DB, mirror `test_claim_gate_self_grade.py`'s fixture style; `RUN_DB_INTEGRATION`). Seed one book with a TOC of, say, 6 entries whose `order_index` = 0..5 but **insert/create the homework_jobs in scrambled order** (e.g. order_index 3,0,5,1,4,2), all `status='pending'`, same `priority`, same `scheduled_at`, same batch. Then call `claim_next_job` (a cli/all-pass capability) repeatedly, recording each claimed job's lesson `order_index`. **Assert the claimed sequence is `[0,1,2,3,4,5]`** (ascending). RED-prove: with the current order clause this fails (claims in scrambled/insert order).

- [ ] **Step 2: edit the order clause.** Replace `.order_by(HomeworkJob.priority.desc(), HomeworkJob.scheduled_at.asc())` with a lesson-order tiebreaker between them:

```python
    lesson_order = (
        select(TocEntry.order_index)
        .where(TocEntry.id == HomeworkJob.toc_entry_id)
        .scalar_subquery()
    )
    # ...
    .order_by(
        HomeworkJob.priority.desc(),
        lesson_order.asc(),                 # ascending lesson order (1 -> 26); NULLS LAST for batchless jobs
        HomeworkJob.scheduled_at.asc(),     # final FIFO tiebreaker
    )
```

Import `TocEntry` (the toc_entries model) at the top of `jobs.py` if not already imported. Keep everything else in `pick_stmt` (the where-clauses, `with_for_update(skip_locked=True)`, `limit(1)`) unchanged. Update the `claim_next_job` docstring's "Order:" note (`:296-297`) to mention ascending lesson order.

- [ ] **Step 3 (GREEN):** run on a scratch DB:
```bash
createdb -U macmini5 edu_claimorder_test
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_claimorder_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_claimorder_test \
  RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \
  tests/integration/test_claim_order.py tests/integration/test_claim_gate_self_grade.py \
  tests/integration/test_claim_contention.py -q
dropdb edu_claimorder_test
```
Expected: new test GREEN; **the existing claim-gate + contention tests still pass** (the tiebreaker must not disturb the capability gate). Bite-verify: remove `lesson_order.asc()` → the ascending assertion fails.

- [ ] **Step 4: commit** (stage only these two files):
```bash
git add app/repositories/jobs.py tests/integration/test_claim_order.py
git commit -m "fix(worker): claim jobs in ascending lesson order (toc_entries.order_index)"
```

## Acceptance
- New scratch-DB test proves repeated `claim_next_job` returns lessons in ascending `order_index`, RED-proved (remove the clause → fails). Existing claim-gate/contention suites stay green.

## Out of scope
- Re-ordering the **already-archived** Notion pages (Notion orders by creation time; this fix only affects *future* claims). The in-flight re-archive migration creates new pages in lesson order; existing pages keep their position unless re-created.
- Any change to priority/scheduling semantics.

## Finish (per CLAUDE.md)
- Worklog + INDEX row (next-free id — verify at finish). De-stale `docs/CODE_MAP.md`/`HOW_IT_WORKS.md` if they describe claim ordering. `git mv` this plan to `plans/shipped/`. Branch + PR → gatekeeper (no self-merge).
