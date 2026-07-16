# Book deletion integrity (BE-02) — batches, guard, and on-disk cleanup

**Item:** audit BE-02 (root `Wishlist.md`) — deleting a book with any batch 500s on
`batches_book_id_fkey` (scratch-DB reproduced in the audit), and a successful delete leaves
`var/books/<id>/source.pdf` on disk forever. Exploration also found a gap the audit missed:
`DELETE /books/{id}` has **no active-jobs guard** — it will ORM-delete `homework_jobs` rows out
from under a running worker (live DB has a book with active jobs right now).

## Approach & key decisions

- **Explicit repo deletes in dependency order — ZERO migration.** `books_repo.delete` already
  deletes jobs explicitly (its docstring documents the explicit style); it just forgot batches.
  Order: jobs (ORM, phase_outputs cascade) → batches (`sa_delete(Batch).where(book_id)`; safe
  after jobs since `homework_jobs.batch_id` referencers are gone) → book (toc_entries cascade).
  **Rejected:** migration adding `ON DELETE CASCADE` to `batches.book_id`/`homework_jobs.book_id`
  — BE-12 (composite-FK integrity) will revisit the FK topology wholesale; don't pre-empt it with
  a second FK philosophy, and zero-migration ships faster.
- **Active jobs → 409, refuse-loud (locked with user 2026-07-16):** any job in
  `pending/running/cancelling` blocks deletion; the 409 names the count and points at Cancel-all.
  `done/failed/cancelled` never block. Matches the #87 refuse-only-409 precedent.
- **On-disk cleanup: post-commit, best-effort, loud.** After the DB commit succeeds,
  `shutil.rmtree(storage.book_dir(book_id), ignore_errors=False)` in a try/except — failure logs
  ERROR (`book delete: dir cleanup FAILED …`) and still returns 204 (rows are gone; an orphan dir
  is disk-only). Missing dir = silent no-op. **Rejected:** tombstone/sweeper machinery (YAGNI) and
  pre-commit unlink (a rolled-back tx must not have deleted files).
- **Deliberately retained on delete:** `agent_usages` rows (FK `SET NULL` — billing audit,
  existing design); Notion pages (archived content outlives the book row — feature, documented).
- Verified seams: `books_repo.delete` `repositories/books.py` (jobs-then-book today);
  route `api/v1/books.py:571-579` (no guard, no cleanup); `batches.book_id` FK no ondelete
  (`models/batch.py:23`); `storage.book_dir` exists (`storage.py:17`); FE delete already
  `window.confirm`s (`library.tsx:333-337`). No collision: no open lane owns books.py/books repo.
- Branch `feat/book-delete-integrity`, worktree `../HCGA-book-delete`. Migration: **none**.
  Worklog **0144** (0142 = BE-16, 0143 = #97; re-verify tail at finish). Scratch
  `edu_scratch_bookdel` (create as `-U macmini5 -O edu`; pin 127.0.0.1). Suite baseline: re-run
  in worktree (last clean: 1720/237). No model calls anywhere — acceptance is the real-DB
  integration tests + a route-level end-to-end (create→delete→verify DB+disk), $0.

## Tasks (TDD per task, commit each; stage only listed files)

### Task 1 — repo: delete batches inside the transaction (RED → GREEN, scratch DB)

**Tests first** (`tests/integration/test_books_repo_delete.py`, new, RUN_DB_INTEGRATION):
- **RED (the audit's exact repro):** book + batch + done job (batch-stamped) → `books_repo.delete`
  + commit raises `IntegrityError` on `batches_book_id_fkey` today.
- GREEN target: delete succeeds; `books`/`batches`/`homework_jobs`/`phase_outputs`/`toc_entries`
  rows for the book all gone; an `agent_usages` row seeded against the job SURVIVES with
  `book_id/homework_job_id` NULLed.
- Book with TWO batches (uz + ru transports) → both gone.
**Code** (`app/repositories/books.py`): add the batch delete between jobs and book; update the
docstring to name the full order and the retained tables.
Commit: `fix(books): delete batches inside the book-delete transaction (BE-02 task 1)`

### Task 2 — route: 409 active-jobs guard (RED → GREEN)

**Tests first** (extend the existing books API test file; follow its conventions):
- **RED:** book with a `running` job → `DELETE /books/{id}` returns 409, detail contains the
  active count and the word "cancel"; DB rows untouched.
- `pending` and `cancelling` also block; a book with only `done`+`failed`+`cancelled` jobs
  deletes 204.
- 404 for a missing book unchanged.
**Code** (`app/api/v1/books.py`): count active jobs before calling the repo; 409 with
`f"book has {n} active job(s) (pending/running) — cancel the batch first, then delete"`.
Commit: `feat(books): refuse deletion while jobs are active — 409 (BE-02 task 2)`

### Task 3 — post-commit file cleanup + FE confirm copy (RED → GREEN)

**Tests first** (same API test file):
- **RED:** delete a book whose `storage.book_dir` exists (create it with a fake `source.pdf` in
  tmp `VAR_DIR`) → after 204 the directory is GONE.
- Cleanup failure is non-fatal: monkeypatch `shutil.rmtree` to raise → still 204, ERROR logged
  (caplog), DB rows gone.
- No dir on disk → 204, no error.
**Code** (`app/api/v1/books.py`): post-commit rmtree per the decision above.
**FE** (`web/src/routes/library.tsx`): extend the existing confirm text to say the PDF and all
generated content are deleted permanently. `npx tsc --noEmit` + `npm run build`.
Commit: `feat(books): remove the book's on-disk dir after delete; confirm copy (BE-02 task 3)`

### Task 4 — docs + finish

- Docs de-stale: `docs/HOW_IT_WORKS.md` (delete flow: order, guard, cleanup, retained tables),
  `docs/DATABASE.md` (books row: what deletion removes/retains), CLAUDE.md only if its books
  bullet mentions deletion (check).
- **Annotate root `Wishlist.md`:** mark BE-02 addressed (commit ref) — and add the same one-line
  closure notes to BE-03/06/08/09/16 while in there (the file currently carries zero closure
  annotations; flagged 2026-07-16).
- Worklog **0144** + INDEX row (re-verify tail). Full suite; FE gates; `git fetch` + rebase check;
  push; PR → **GK2 gates + merges**; plan → `shipped/`.

## Flagged for the gate

1. Deletion of a fully-generated book silently destroys its `phase_outputs` (the deliverable) —
   pre-existing behavior, now made *safer* (guard + confirm copy), but still irreversible; the
   Notion archive is the surviving copy.
2. Cleanup failure leaves an orphan dir with only an ERROR log — accepted (disk-only; no sweeper).
3. The 409 counts `cancelling` as active (a job mid-cancel still holds a worker) — deliberate.
