# Batch rollup: launched-lessons denominator (BE-03) — derive targets from member jobs

**Item:** audit finding BE-03 (root `Wishlist.md`) — a lesson-only launch (the default since #89)
can never read `complete` because `rollup_for_batch` counts the WHOLE book's TOC rows as the
denominator (`not_started = book_total − launched`), and `complete` requires `not_started == 0`.
Every campaign batch misreports (live example: G8-Algebra UZ, 31/31 done, Monitor says "18 not started").

**Gatekeeper verdict (GK2, 2026-07-15): approved with mechanism swapped — derive, don't persist.**

## Approach & key decisions

- **Mechanism: derive launch scope from member jobs** — `SELECT DISTINCT ON (toc_entry_id) … FROM homework_jobs WHERE batch_id = :id` IS the launch scope. Verified: all three launch paths stamp a job per target (create `batch.py:369`, adopt `:340`, resume `:364` — 0133 fixed resume's batch_id), and `rollup_for_batch`'s tally already reads exactly this; only the synthetic `not_started` line (`batches.py:112-125`) is wrong. **Rejected:** `batch_targets(batch_id, toc_entry_id)` table (migration + backfill + a three-path dual-write invariant — the exact family where 0133 forgot `batch_id` on resume; its own backfill would be seeded FROM the jobs, proving the derivation is the truth) and re-run-classifier-at-read (can't represent explicit picks; drifts when the classifier recalibrates, e.g. #92).
- **`complete` = all launched lessons done** (`done == lessons_covered > 0`). ⚠️ Semantic change, flagged for gate: today `failed`/`cancelled` do NOT block `complete` (only pending/running/cancelling do); under the locked "all launched lessons done" they now do. Cancelled/failed jobs still COUNT as targets (they were launched; resume re-enqueues them).
- **Rest-of-book is a separate display figure, never in the denominator:** new response field `toc_total` (whole-book TOC row count). Net query count unchanged — the same COUNT moves out of `rollup_for_batch` into the serializer path.
- **Non-targeted rows: visible but excluded** — `list_jobs` keeps returning every TOC row; each row gains `toc_class` (pure `classify_entries` at read, needs only section_title/page_start/page_end); the FE renders un-launched rows with their class chip (`header`/`test`/`lesson`…) instead of a bare "not started", counted in nothing. Top-up flips a row to a real target automatically (new job stamps `batch_id`).
- **API break (deliberate, FE updated in lockstep):** `rollup` loses the `not_started` key; `lessons_covered` becomes `sum(rollup)`. No external consumers known.
- Branch `feat/batch-target-denominator` off `origin/Nggaev-v2`, worktree `../HCGA-target-denom`. Migration: none. Worklog: **0139**. Suite baseline: 1544 passed / 214 skipped.

## Tasks

### Task 1 — repo: launched-only tally + `toc_total_for_batch` (RED → GREEN)

**Tests first** (`tests/integration/test_batches_repo.py`, RUN_DB_INTEGRATION — scratch DB
`edu_scratch_be03`, recipe: `createdb -h 127.0.0.1 -U edu -O edu edu_scratch_be03` +
`DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_be03 uv run alembic upgrade head`):

```python
async def test_rollup_partial_launch_has_no_not_started(db_session):
    # book with 3 toc rows; batch launches 2 (one done, one running)
    ...seed helpers already in this file...
    tally = await batches_repo.rollup_for_batch(db_session, batch.id)
    assert tally == {"done": 1, "running": 1}          # RED today: {"done":1,"running":1,"not_started":1}
    assert "not_started" not in tally

async def test_toc_total_for_batch_counts_whole_book(db_session):
    total = await batches_repo.toc_total_for_batch(db_session, batch.id)
    assert total == 3
```

Prove RED on the first test against unmodified code (the old assert fires with `not_started: 1`), then:

**Code** (`app/repositories/batches.py`):
- `rollup_for_batch` — delete the book_id/total/not_started block (current lines 112-125); docstring
  rewritten: "tally over the batch's launched lessons only (DISTINCT ON latest job per toc_entry);
  the denominator is the launch scope derived from member jobs — rest-of-book is `toc_total_for_batch`."
- New:
```python
async def toc_total_for_batch(session: AsyncSession, batch_id: UUID) -> int:
    """Whole-book TOC row count for this batch's book — display-only context
    (the rollup denominator is the launched-lesson count, never this)."""
    from app.models.toc_entry import TOCEntry
    return (await session.execute(
        select(func.count()).select_from(TOCEntry)
        .join(Batch, Batch.book_id == TOCEntry.book_id)
        .where(Batch.id == batch_id)
    )).scalar_one()
```
- `list_with_rollups` (`batches.py:252-268`): add `"toc_total": await toc_total_for_batch(session, b.id)`
  per row (replaces the count formerly inside rollup — net round trips unchanged).

Update in the same commit any existing repo tests asserting `not_started` in this file.

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=... uv run python -m pytest tests/integration/test_batches_repo.py -q`
Commit: `fix(batch): rollup tallies launched lessons only; toc_total split out (BE-03 task 1)`
Stage only: `app/repositories/batches.py tests/integration/test_batches_repo.py`

### Task 2 — API: `complete` semantics + `toc_total` field (RED → GREEN)

**Tests first** (`tests/api/test_rollup_pause_and_not_started.py` → extend in place; keep filename):
- response `rollup` has no `not_started`; response has `toc_total == <book rows>`.
- `complete is True` for a batch whose 2 launched lessons are done while the book has a 3rd row (RED today).
- `complete is False` when a launched lesson is `failed` (RED today — new semantics).
- `lessons_covered == sum(rollup.values())`.

**Code** (`app/api/v1/batch.py`):
- serializer (~line 100-133): `"rollup": tally` (unchanged shape, now launched-only),
  `"lessons_covered": sum(tally.values())`,
  `"complete": sum(tally.values()) > 0 and tally.get("done", 0) == sum(tally.values())`,
  new `"toc_total": toc_total`.
- Thread `toc_total` at the three call sites (`:387`, list route via `list_with_rollups` row, `:416`).

Run: `uv run python -m pytest tests/api/test_rollup_pause_and_not_started.py tests/api/ -q`
Commit: `fix(batch): complete = all launched lessons done; toc_total display field (BE-03 task 2)`
Stage only: `app/api/v1/batch.py tests/api/test_rollup_pause_and_not_started.py`

### Task 3 — `list_jobs` rows carry `toc_class` (RED → GREEN)

**Test first** (`tests/integration/test_batches_repo.py`): seed a book with a header row
(all-caps, single page) + 2 lesson rows, launch 1 lesson; `list_jobs` returns 3 rows each with
`toc_class`; header row → `"header"`, lessons → `"lesson"`; launched row keeps job fields.

**Code** (`app/repositories/batches.py` `list_jobs`): select `TOCEntry.page_start/page_end` too
(classifier duck-types `section_title`, `page_start`, `page_end`); after fetching rows, run
`classify_entries(rows)` once (import `app.services.toc_classifier`) and zip `"toc_class"` into
the dicts. Order preserved (classifier returns input-order-aligned list).

Run: `RUN_DB_INTEGRATION=1 ... pytest tests/integration/test_batches_repo.py -q`
Commit: `feat(batch): list_jobs rows carry toc_class for excluded-row chips (BE-03 task 3)`
Stage only: `app/repositories/batches.py tests/integration/test_batches_repo.py`

### Task 4 — FE: excluded-row chips + launched-only bar + book-context line

**Code** (`web/src/`):
- `lib/types.ts`: `BatchRollup` drops `"not_started"`; `BatchSummary` + `toc_total: number`;
  batch-jobs row type + `toc_class: string`.
- `components/fleet/status.ts`: remove `not_started` from `STATUS_ORDER` + `STATUS_COLORS`.
- `components/fleet/rollup-bar.tsx`: formula unchanged (`total = sum(rollup)` is now launched-only).
- `components/fleet/batch-funnel.tsx`: under the bar, secondary text
  `{covered} launched · {toc_total − covered} book rows not in launch` (only when > 0).
- `components/fleet/batch-lesson-list.tsx`: un-launched row chip renders `row.toc_class`
  (e.g. `header`, `lesson`) instead of `not started`; keep dim styling; launched rows unchanged.
- `lib/monitor-grouping.test.ts`: fix fixture (`rollup: { not_started: 5 }` → a real status);
  `batchActionFlags` itself is untouched.

Run: `cd web && npx vitest run && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Commit: `feat(fleet): excluded-row class chips + launched-only rollup bar (BE-03 task 4)`
Stage only: the five files above.

### Task 5 — docs + acceptance + finish

- `docs/HOW_IT_WORKS.md`: rollup/complete semantics section rewritten (derive-from-jobs, toc_total).
- `CLAUDE.md` batches bullet: denominator wording + de-stale the batch uniqueness note
  (it says `UNIQUE(book_id, transport)`; reality since mig 0038 is the TRIPLE
  `uq_batches_book_id_transport_output_language`).
- Worklog **0139** in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`.
- **Acceptance (read-only, $0, real prod DB):** `GET /jobs/batches` on the live head —
  G8-Algebra UZ api batch must show `complete: true`, rollup without `not_started`,
  `toc_total` = its book's row count; paste actual JSON into the PR. No generation involved
  (read-path change only), so no model smoke needed.
- Full suite (`uv run python -m pytest tests/ -q` — expect 1544+new passed / 214 skipped / 0 failed),
  `git fetch origin && git log HEAD..origin/Nggaev-v2` rebase check, push, PR → **GK2 gates + merges**.
- After merge: `git mv` this plan to `docs/superpowers/plans/shipped/`.

## Flagged for the gate (honesty items)

1. `complete` now blocked by `failed`/`cancelled` (previously only in-flight statuses blocked it).
2. `not_started` removed from the API rollup — FE updated in lockstep; no other consumers found
   (`grep -rn not_started web/src app/` = fleet/status.ts, types.ts, one test fixture, batches.py).
3. Monitor list still shows every TOC row (visible-but-excluded, per locked decision) — only the
   counting changed.
