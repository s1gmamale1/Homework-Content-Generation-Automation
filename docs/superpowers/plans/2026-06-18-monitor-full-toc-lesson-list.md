# Monitor — Full-TOC Lesson List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Monitor batch card list *every* lesson in the book — launched lessons show their job status, un-launched lessons show a dim "not started" row — so a partial launch no longer looks like a tiny 3-lesson subject.

**Architecture:** Change one repository read (`batches.list_jobs`) from an INNER join (latest-job-per-section ⋈ TOCEntry) to a **LEFT JOIN driven by the book's full TOC** → latest job in this batch. Un-launched lessons return with `job_id=None` and no status. The frontend type gains nullable job fields and the lesson-list component renders un-launched rows as dim, action-less "not started" rows. Progress math is untouched.

**Tech Stack:** FastAPI + SQLAlchemy (async, Postgres), React + TypeScript, TanStack Query.

## Approach & key decisions

- **Decision: "Launched only" progress semantics** (chosen by user over "Whole book" / "Both"). The progress bar, percentage, `lessons_covered`, and the `complete` pill stay measured over **launched** lessons exactly as today. The full TOC is added to the lesson list purely as visual context. **Consequence:** `batches.rollup_for_batch` and `batch._rollup_payload` are **NOT changed** — this corrects the original task sketch, which said to touch `rollup_for_batch`; touching it would alter progress math and break the chosen semantics.
- **Decision: CLI+API collapse is out of scope** (chosen by user). A book launched on both transports still renders two cards (one per `(book, transport)` batch). No change to `get_or_create_for_book` or the `UNIQUE(book_id, transport)` model.
- **Load-bearing facts verified against code:**
  - `batches.list_jobs` (`app/repositories/batches.py:86`) inner-joins `TOCEntry` to the latest job per section *within the batch* → only launched lessons appear. This is the single source of the "card shows only 3 lessons" symptom.
  - Every `toc_entry` is a launchable lesson: `TOCEntry.section_title` is `NOT NULL`, there is no header/chapter-only row type, and the launcher targets the full `toc_entries.list_for_book` set. So a LEFT JOIN from the full TOC introduces **no phantom header rows**.
  - `list_jobs` is consumed in exactly **one** place: `web/src/components/fleet/batch-lesson-list.tsx` (via `api.batchJobs` → `/api/v1/jobs/batches/{id}/jobs`, `batch.py:163`), rendered only by the Monitor card (`batch-funnel.tsx:56`, non-selectable). The launcher does its own lesson-picking off the book TOC directly, **not** through `list_jobs`. So this change has no collateral on the launcher.
  - `rollup_for_batch` (drives the bar) is a separate query and stays as-is, so `complete`/`%`/`covered` are unaffected.

## Global Constraints

- Stage only the files each task lists — never `git add -A` (other sessions may be committing to `web/`).
- Backend tests that hit Postgres are gated by `RUN_DB_INTEGRATION=1` + `DATABASE_URL`; mirror the existing pattern in `tests/integration/test_batches_repo.py` (skipif marker, `SessionLocal`, explicit teardown via `delete(...)`).
- No frontend unit-test infra exists (no vitest). Frontend tasks are verified by `npx tsc -p tsconfig.app.json --noEmit` and `npm run build`.
- This change does **not** affect generation, so no CLI smoke gate is required.
- Local Postgres for integration tests runs on the port in `.env`'s `DATABASE_URL` (currently `localhost:5432/edu_copy`).

---

### Task 1: Backend — `list_jobs` returns the full book TOC (LEFT JOIN)

**Files:**
- Modify: `app/repositories/batches.py:86-127` (the `list_jobs` function)
- Test: `tests/integration/test_batches_repo.py` (add one test; real-DB)

**Interfaces:**
- Consumes: `app.models.batch.Batch` (`.book_id`), `app.models.toc_entry.TOCEntry` (`.id`, `.book_id`, `.section_title`, `.order_index`), `app.models.homework_job.HomeworkJob`.
- Produces: `list_jobs(session, batch_id) -> list[dict]` with one dict **per TOC entry in the book**, ordered by `order_index`. Keys (unchanged set, but job-derived keys are now nullable for un-launched lessons): `job_id: str | None`, `toc_entry_id: str`, `section_title: str`, `order_index: int`, `status: str | None`, `attempts: int | None`, `current_phase: str | None`, `error_message: str | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_batches_repo.py` (the file already imports `os`, `pytest`, `delete`, `select` and defines `_seed_book_with_lessons`):

```python
@pytest.mark.asyncio
async def test_list_jobs_includes_unlaunched_lessons():
    """list_jobs returns one row per book lesson (full TOC), launched lessons
    carry status, un-launched lessons come back with job_id/status None — while
    rollup_for_batch stays launched-only (denominator unchanged)."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.repositories import batches as batches_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book_with_lessons(s, n=3)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli")
        # Launch ONLY lessons 0 and 1; lesson 2 stays un-launched.
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[0].id,
                               subject="math-algebra", batch_id=batch.id)
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=tocs[1].id,
                               subject="math-algebra", batch_id=batch.id)
        await s.commit()
        book_id, batch_id, third_toc = book.id, batch.id, tocs[2].id
    try:
        async with SessionLocal() as s:
            rows = await batches_repo.list_jobs(s, batch_id)
            tally = await batches_repo.rollup_for_batch(s, batch_id)

        # All three lessons present, ordered by order_index.
        assert [r["order_index"] for r in rows] == [0, 1, 2]
        # Launched lessons carry a job + status.
        assert rows[0]["job_id"] is not None and rows[0]["status"] == "pending"
        assert rows[1]["job_id"] is not None and rows[1]["status"] == "pending"
        # Un-launched lesson: present, but no job/status.
        third = next(r for r in rows if r["toc_entry_id"] == str(third_toc))
        assert third["job_id"] is None
        assert third["status"] is None
        assert third["section_title"] == "L2"
        # Rollup stays launched-only: 2 lessons, NOT 3.
        assert sum(tally.values()) == 2, f"rollup must stay launched-only, got {tally}"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(Batch).where(Batch.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:RUN_DB_INTEGRATION=1; uv run python -m pytest tests/integration/test_batches_repo.py::test_list_jobs_includes_unlaunched_lessons -v`
Expected: FAIL — current `list_jobs` inner-joins, so lesson 2 is absent and `[r["order_index"] for r in rows] == [0, 1]` (assertion error on the list equality).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `list_jobs` in `app/repositories/batches.py` (keep the function name/signature) with a full-TOC LEFT JOIN:

```python
async def list_jobs(session: AsyncSession, batch_id: UUID) -> list[dict]:
    """One row per lesson in the batch's BOOK (full TOC), LEFT-joined to the
    latest job per toc_entry within this batch. Launched lessons carry their
    job's status/fields; un-launched lessons come back with job_id/status None.
    Ordered by order_index. The launched-only rollup (rollup_for_batch) is a
    separate query and is unaffected by the un-launched rows added here."""
    from app.models.toc_entry import TOCEntry

    book_id = (
        await session.execute(select(Batch.book_id).where(Batch.id == batch_id))
    ).scalar_one_or_none()
    if book_id is None:
        return []

    latest = (
        select(
            HomeworkJob.id.label("job_id"),
            HomeworkJob.toc_entry_id.label("toc_entry_id"),
            HomeworkJob.status.label("status"),
            HomeworkJob.attempts.label("attempts"),
            HomeworkJob.current_phase.label("current_phase"),
            HomeworkJob.error_message.label("error_message"),
        )
        .where(HomeworkJob.batch_id == batch_id)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
        .subquery()
    )
    stmt = (
        select(
            latest.c.job_id, latest.c.status, latest.c.attempts,
            latest.c.current_phase, latest.c.error_message,
            TOCEntry.id.label("toc_entry_id"),
            TOCEntry.section_title, TOCEntry.order_index,
        )
        .select_from(TOCEntry)
        .outerjoin(latest, latest.c.toc_entry_id == TOCEntry.id)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    rows = await session.execute(stmt)
    return [
        {
            "job_id": str(r.job_id) if r.job_id is not None else None,
            "toc_entry_id": str(r.toc_entry_id),
            "section_title": r.section_title,
            "order_index": r.order_index,
            "status": r.status,
            "attempts": r.attempts,
            "current_phase": r.current_phase,
            "error_message": r.error_message,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `$env:RUN_DB_INTEGRATION=1; uv run python -m pytest tests/integration/test_batches_repo.py::test_list_jobs_includes_unlaunched_lessons -v`
Expected: PASS

- [ ] **Step 5: Run the full batches-repo integration file to prove no regression**

Run: `$env:RUN_DB_INTEGRATION=1; uv run python -m pytest tests/integration/test_batches_repo.py -v`
Expected: PASS — including `test_rollup_is_per_lesson_latest` (proves rollup denominator is still launched-only).

- [ ] **Step 6: Commit**

```bash
git add app/repositories/batches.py tests/integration/test_batches_repo.py
git commit -m "feat(monitor): list_jobs returns full book TOC, un-launched lessons as null-job rows"
```

---

### Task 2: Frontend — nullable lesson type + "not started" rows

**Files:**
- Modify: `web/src/lib/types.ts` (the `BatchLessonRow` interface, around line 350-359)
- Modify: `web/src/components/fleet/batch-lesson-list.tsx`

**Interfaces:**
- Consumes: `BatchLessonRow` from Task 1's payload — `job_id`, `status`, `attempts`, `current_phase`, `error_message` are now nullable; `toc_entry_id`, `section_title`, `order_index` always present.
- Produces: no new exported interface; `BatchLessonList`'s props are unchanged.

- [ ] **Step 1: Make the lesson-row type nullable**

In `web/src/lib/types.ts`, change the `BatchLessonRow` interface to:

```typescript
export interface BatchLessonRow {
  job_id: string | null;
  toc_entry_id: string;
  section_title: string;
  order_index: number;
  status: JobStatus | null;
  attempts: number | null;
  current_phase: string | null;
  error_message: string | null;
}
```

- [ ] **Step 2: Verify the type change surfaces the unhandled-null spots**

Run: `cd web; npx tsc -p tsconfig.app.json --noEmit`
Expected: FAIL — type errors in `batch-lesson-list.tsx` where `row.job_id` is used as a React key, `row.status` is passed to `colorFor`, and `row.attempts > 1` is evaluated (all now possibly null). This confirms exactly the spots Step 3 must handle.

- [ ] **Step 3: Render launched vs un-launched rows**

Replace the `return (...)` block of `BatchLessonList` in `web/src/components/fleet/batch-lesson-list.tsx` (the `<ul>...</ul>`) with a version that branches on whether the lesson was launched. Key each row by `toc_entry_id` (stable; `job_id` is now nullable):

```tsx
  return (
    <ul className="divide-y divide-white/[0.06]">
      {rows.map((row) => {
        const launched = row.job_id !== null && row.status !== null;
        const canCancel =
          row.status === "pending" || row.status === "running";
        const canRetry = row.status === "failed";
        const isSelected = selected?.has(row.toc_entry_id) ?? false;

        return (
          <li
            key={row.toc_entry_id}
            className={cn(
              "flex items-center gap-3 py-2 text-sm",
              !launched && "opacity-45",
            )}
          >
            {selectable && (
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggle?.(row.toc_entry_id)}
                className="size-4 shrink-0 accent-[#7c5cff]"
              />
            )}

            <span className="shrink-0 font-mono text-xs text-white/35">
              #{row.order_index}
            </span>
            <span className="min-w-0 flex-1 truncate text-white/80">
              {row.section_title}
            </span>

            {launched ? (
              <span
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.7rem] text-white/85"
                style={{ background: `${colorFor(row.status!)}` }}
              >
                <span className="font-medium">{row.status}</span>
                {(row.attempts ?? 0) > 1 && (
                  <span className="text-white/60">· try {row.attempts}</span>
                )}
              </span>
            ) : (
              <span className="shrink-0 rounded-full bg-white/[0.06] px-2 py-0.5 text-[0.7rem] text-white/50">
                not started
              </span>
            )}

            {!selectable && launched && (
              <div className="flex shrink-0 items-center gap-1">
                {canCancel && (
                  <button
                    type="button"
                    onClick={() => cancel.mutate(row.job_id!)}
                    disabled={cancel.isPending}
                    className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
                  >
                    Cancel
                  </button>
                )}
                {canRetry && (
                  <button
                    type="button"
                    onClick={() => retry.mutate(row.job_id!)}
                    disabled={retry.isPending}
                    className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
                  >
                    Retry
                  </button>
                )}
                <Link
                  to={`/job/${row.job_id}`}
                  className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
                >
                  Open
                  <ArrowUpRight className="size-3.5" />
                </Link>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
```

- [ ] **Step 4: Typecheck passes**

Run: `cd web; npx tsc -p tsconfig.app.json --noEmit`
Expected: PASS (no output, exit 0).

- [ ] **Step 5: Build**

Run: `cd web; npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/types.ts web/src/components/fleet/batch-lesson-list.tsx
git commit -m "feat(monitor): show all book lessons in batch card, un-launched as 'not started'"
```

---

## Self-Review

**1. Spec coverage:**
- "All lessons show, only launched light up with status" → Task 1 (full-TOC LEFT JOIN) + Task 2 Step 3 (launched vs "not started" branch). ✅
- "Touch `list_jobs`" → Task 1. ✅
- "Do NOT touch `rollup_for_batch`" (launched-only decision) → explicitly preserved; Task 1 Step 5 asserts it. ✅
- "Keep CLI+API as two cards" → no change to `get_or_create_for_book`/uniqueness. ✅
- "Two FE components" sketch → only `batch-lesson-list.tsx` needs logic changes (+ `types.ts`); `batch-funnel.tsx` consumes `BatchLessonList` unchanged. Verified `list_jobs` has no other consumer, so this is correct, not a gap. ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**3. Type consistency:** `list_jobs` dict keys (Task 1) match the `BatchLessonRow` fields (Task 2): `job_id`, `toc_entry_id`, `section_title`, `order_index`, `status`, `attempts`, `current_phase`, `error_message`. Nullability aligns (job-derived keys nullable; TOC-derived keys non-null). `colorFor(row.status!)` is only reached when `launched` (status non-null). ✅
