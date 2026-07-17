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
  `pending/running/cancelling` blocks deletion; the 409 names the count and says
  "cancel the active job(s) or their batch first" (gate fix: `batch_id` is nullable — single
  `/generate` jobs have no batch, so the message must not assume one). `done/failed/cancelled`
  never block. Matches the #87 refuse-only-409 precedent.
- **Ingest-in-flight → 409 too (gate fix #1):** a book in `uploading`/`toc_extracting` has NO
  jobs, yet `_TOC_TASKS` (books.py:49) is actively reading its PDF and `toc_extractor.run` will
  later insert TOC rows for it — deleting then crashes the extractor and/or yanks the PDF
  mid-read. Guard: status in (`uploading`, `toc_extracting`) → 409 "book is still being
  ingested". Escape for a genuinely wedged book: extraction is time-bounded (model timeouts,
  CQ-D) — status inevitably flips to `failed`/`toc_review`, which delete accepts.
- **Concurrency: book-scoped advisory lock, shared for launches / exclusive for delete (gate
  fix #2).** The launch routes read the book unlocked and create batches/jobs much later
  (batch.py:140, jobs.py:133) — a launch racing the delete between guard-count and the deletes
  re-creates the FK 500 or deletes a running job. Fix extends the house idiom (jobs.py:115
  already uses per-section advisory locks): **every path that ACTIVATES work for a book takes
  the SHARED lock** `pg_advisory_xact_lock_shared(hashtext('book:'||<id>))` at entry — the two
  launch paths (batch.py:140, jobs.py:133) AND the three activation paths the second gate pass
  caught (single-job retry `jobs.py:342`, batch resume `batch.py:476`, TOC retry `books.py:375`,
  which flips a deletable `failed` book to `toc_extracting` and spawns `_TOC_TASKS`); each
  RE-READS its target's state AFTER acquiring the lock (a stale pre-lock read of a row the
  delete just removed must 404/409, never proceed). Delete takes the EXCLUSIVE form. Activators
  never serialize each other; delete excludes all of them and vice versa. Proven by a
  real-Postgres race test with an OUTCOME-CONDITIONAL oracle — activator wins → delete 409s and
  the work survives; delete wins → the activator 404/409s and creates nothing; 204 only with no
  competitor — never an FK error or a deleted running job in any interleaving.
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
  Worklog **0145** (gate fix #3: 0144 is reserved by the approved prepare-status plan; re-verify
  tail at finish). Scratch
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

### Task 2 — route: 409 guards — active jobs AND ingest-in-flight (RED → GREEN)

**Tests first** (extend the existing books API test file; follow its conventions):
- **RED:** book with a `running` job → `DELETE /books/{id}` returns 409, detail contains the
  active count and the word "cancel"; DB rows untouched.
- `pending` and `cancelling` also block; a book with only `done`+`failed`+`cancelled` jobs
  deletes 204.
- **RED (gate fix #1):** book with status `uploading` → 409 "still being ingested";
  same for `toc_extracting`; `failed` and `toc_review` books delete fine (the wedged-book escape).
- 404 for a missing book unchanged.
**Code** (`app/api/v1/books.py`): status guard first, then count active jobs; 409 message
`f"book has {n} active job(s) (pending/running/cancelling) — cancel the active job(s) or their
batch first, then delete"` (batch_id is nullable — never assume a batch exists).
Commit: `feat(books): refuse deletion while ingesting or jobs active — 409 (BE-02 task 2)`

### Task 3 — book-scoped advisory locks: launches shared, delete exclusive (RED → GREEN, scratch DB)

**Tests first** (`tests/integration/test_book_delete_race.py`, new, RUN_DB_INTEGRATION,
real Postgres, two independent sessions/connections — same separate-connection discipline as the
credential-limiter race tests):
- **RED (the race):** open tx A holding the shared book lock (simulating an activator
  mid-request, after its guard read, before its write) → a concurrent `DELETE` route call must
  BLOCK until A commits. Today (no locks) the delete interleaves → assert the
  FK-500/deleted-running-job failure reproduces.
- **Outcome-conditional oracle (gate fix, second pass):** run the race both ways and assert PER
  WINNER — (a) activator commits first → delete returns 409 AND the activated work survives
  (job row present, status pending/running); (b) delete commits first → the activator returns a
  controlled 404/409 AND `homework_jobs`/`batches` gained ZERO rows for the book; (c) plain
  delete with no competitor → 204. A bare "any of 404/409/204" assertion is BANNED — it would
  pass while deleting a just-created active job.
- Two concurrent ACTIVATORS take the shared lock simultaneously (no serialization between them).
- Per-path coverage: each of the five activation paths (both launches, single-job retry, batch
  resume, TOC retry) acquires the shared lock and RE-READS its target after acquisition —
  e.g. retry of a job whose book was just deleted → 404, no resurrection. Note: retry/resume
  know job/batch ids, not book ids — derive book_id from the row FIRST, then lock, then re-read
  the row (it may have vanished while waiting).
**Code:** tiny helper (e.g. `app/repositories/books.py::lock_book_shared/lock_book_exclusive`
issuing `pg_advisory_xact_lock_shared/pg_advisory_xact_lock` on `hashtext('book:'||<uuid>)`);
call the SHARED form at entry of ALL FIVE activation paths — `app/api/v1/batch.py` launch (~:140) and
resume (`:476`), `app/api/v1/jobs.py` generate (~:133) and retry (`:342`), `app/api/v1/books.py`
TOC retry (`:375`) — inside their transactions, before the state read they act on; the
EXCLUSIVE form at the top of the delete route's transaction. House idiom precedent:
`jobs.py:115`'s per-section advisory lock.
Commit: `feat(books): book-scoped advisory locks — activation/delete races closed (BE-02 task 3)`

### Task 4 — post-commit file cleanup + FE confirm copy (RED → GREEN)

**Tests first** (same API test file):
- **RED:** delete a book whose `storage.book_dir` exists (create it with a fake `source.pdf` in
  tmp `VAR_DIR`) → after 204 the directory is GONE.
- Cleanup failure is non-fatal: monkeypatch `shutil.rmtree` to raise → still 204, ERROR logged
  (caplog), DB rows gone.
- No dir on disk → 204, no error.
**Code** (`app/api/v1/books.py`): post-commit rmtree per the decision above.
**FE** (`web/src/routes/library.tsx`): extend the existing confirm text to say the PDF and all
generated content are deleted permanently. `npx tsc --noEmit` + `npm run build`.
Commit: `feat(books): remove the book's on-disk dir after delete; confirm copy (BE-02 task 4)`

### Task 5 — docs + finish

- Docs de-stale: `docs/HOW_IT_WORKS.md` (delete flow: order, guard, cleanup, retained tables),
  `docs/DATABASE.md` (books row: what deletion removes/retains), CLAUDE.md only if its books
  bullet mentions deletion (check).
- **Annotate root `Wishlist.md` — in the MAIN CHECKOUT only, never staged** (gate minor: the
  file is untracked and absent from worktrees; the annotation is an out-of-band operator-file
  edit, excluded from the feature commits — same handling as BE-16's closure): mark BE-02
  addressed (commit ref) + add the missing closure notes to BE-03/06/08/09/16.
- Worklog **0145** + INDEX row (0144 = prepare-status lane; re-verify tail at finish). Full
  suite; FE gates; `git fetch` + rebase check; push; PR → **GK2 gates + merges**; plan →
  `shipped/`.

## Flagged for the gate

1. Deletion of a fully-generated book silently destroys its `phase_outputs` (the deliverable) —
   pre-existing behavior, now made *safer* (guard + confirm copy), but still irreversible; the
   Notion archive is the surviving copy.
2. Cleanup failure leaves an orphan dir with only an ERROR log — accepted (disk-only; no sweeper).
3. The 409 counts `cancelling` as active (a job mid-cancel still holds a worker) — deliberate.
