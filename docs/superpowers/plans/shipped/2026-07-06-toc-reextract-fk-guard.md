# TOC Re-extract FK Guard + Dedup Feedback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TOC re-extraction refuse *loudly* (409) instead of FK-crashing when a book's sections are referenced by homework jobs, and give the dedup-by-sha ingest path a distinguishable response so the FE stops showing a phantom "Preparing".

**Architecture:** A synchronous guard in the sole re-extract entrypoint (`POST /books/{id}/toc/retry`) refuses before it fires the background extractor — book status untouched, no data surgery. A new `BookOut.deduplicated` flag lets the FE tell a reused book from a freshly-created one. No migration, no paid model calls, `toc_extractor` internals untouched.

**Tech Stack:** FastAPI, SQLAlchemy (asyncpg), Pydantic v2, React/TS.

---

## Approach & key decisions

- **Root cause (verified against code):** `toc_extractor.run` clears TOC rows with `toc_repo.delete_for_book` (`toc_extractor.py:107`) before re-inserting. `homework_jobs.toc_entry_id` is a NOT-NULL FK with **no cascade** (`app/models/homework_job.py:16`), so any job referencing a to-be-deleted entry raises `ForeignKeyViolationError`; the extractor's `except` flips the book to `failed` and the old TOC survives (WISHLIST `toc-reextract-fk-blocked-1`, hit live on book `bbaa260a`).
- **Sole reachable path (verified):** `toc_extractor.run` is fired only by `_start_toc_extraction`, called from (a) `ingest_pdf` for a *brand-new* book (no jobs can exist yet) and (b) `retry_toc_extraction` (`books.py:249`). `ingest_pdf`'s dedup branch **returns early** (`books.py:86-87`) and never fires extraction. Therefore a guard in the **retry endpoint, before it fires the task**, closes the hole completely — no need to touch `toc_extractor` internals (which lane A may be editing).
- **Chosen fix (user-confirmed): refuse-only LOUD 409.** The endpoint counts jobs referencing the book (`jobs_repo.list_for_book`, index-backed by `ix_homework_jobs_book_toc`) and, if any exist, raises 409 listing their ids+statuses. Book status is **unchanged** (we return before `set_status`). No `?force=true`, no repoint-by-title (rejected for v1 — title drift + attribution risk; recorded as the `toc-reextract-override-1` follow-up). The operator deletes the blocking jobs (existing delete-section flow) then retries — exactly the manual workaround, now visible and safe.
- **All statuses block:** the FK blocks the delete for *any* referencing job — `done`/`cancelled` included — so the guard lists them all (it does **not** filter to active jobs). This is deliberate: re-extracting silently orphans finished homework.
- **Dedup feedback:** add `BookOut.deduplicated: bool = False`, set `True` on `ingest_pdf`'s dedup branch. The FE upload flow (`upload.tsx`) branches its toast on it. **Collision handled (user-confirmed):** the phantom "Preparing" toast lives in `launcher.tsx:128` inside lane C's `prepare` mutation — this plan does **not** edit that file; it ships the `deduplicated` field + FE type for lane C to consume, and flags it in the PR body.
- **Load-bearing facts checked in code:** `jobs_repo` already imported in `books.py` (line 21); `HomeworkJob.book_id` is set together with `toc_entry_id` at create (`jobs.py:52-53`) so `WHERE book_id=` is exactly the FK-blocking set; `HomeworkJob.created_at` exists (used at `jobs.py:270`).

## File structure

- **Modify** `app/repositories/jobs.py` — add `list_for_book(session, book_id) -> list[HomeworkJob]`.
- **Modify** `app/api/v1/books.py` — guard in `retry_toc_extraction`; set `deduplicated=True` on `ingest_pdf` dedup branch.
- **Modify** `app/schemas/book.py` — add `deduplicated: bool = False`.
- **Modify** `app/services/toc_extractor.py` — correct the now-false clear-before-insert comment (lines 102-107).
- **Modify** `web/src/lib/types.ts` — add `deduplicated?: boolean` to `Book`.
- **Modify** `web/src/routes/upload.tsx` — branch the upload toast on `book.deduplicated`.
- **Create** `tests/integration/test_toc_reextract_guard.py` — real-DB FK-reality + `list_for_book` proof.
- **Modify** `tests/api/test_toc_retry.py` — endpoint guard unit tests.
- **Modify** `tests/api/test_ingest_pdf.py` + **create** `tests/test_bookout_deduplicated.py` — dedup-flag tests.

---

### Task 0: Plan commit

- [ ] **Step 1: Commit this plan.**

```bash
git add docs/superpowers/plans/2026-07-06-toc-reextract-fk-guard.md
git commit -m "tocfk: plan — TOC re-extract FK guard + dedup feedback"
```

---

### Task 1: `jobs_repo.list_for_book` + FK-reality proof

**Files:**
- Modify: `app/repositories/jobs.py`
- Create: `tests/integration/test_toc_reextract_guard.py`

- [ ] **Step 1: Write the real-DB integration test (RED).**

Create `tests/integration/test_toc_reextract_guard.py`:

```python
"""Real-DB proof for the TOC re-extract guard.

Documents WHY the guard exists (the raw clear-before-insert raises a FK
violation when a job references a to-be-deleted TOC entry) AND that
jobs_repo.list_for_book surfaces the exact blocking jobs.

Run (scratch DB, pinned to 127.0.0.1):
  createdb -U macmini5 edu_tocfk_test
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_tocfk_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_tocfk_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_toc_reextract_guard.py -q
  dropdb -U macmini5 edu_tocfk_test
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed(s, *, with_job: bool):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob

    book = Book(
        subject="math-algebra",
        original_filename=f"alg-{uuid4().hex[:8]}.pdf",
        content_sha256=uuid4().hex + uuid4().hex,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="Lesson 1", order_index=0)
    s.add(toc)
    await s.flush()
    if with_job:
        s.add(HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject="math-algebra",
            output_language="uz", status="done",
        ))
        await s.flush()
    await s.commit()
    return book.id, toc.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_list_for_book_finds_referencing_job_and_delete_would_fk():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as s:
        book_id, _ = await _seed(s, with_job=True)
    try:
        # list_for_book surfaces the blocking job
        async with SessionLocal() as s:
            blocking = await jobs_repo.list_for_book(s, book_id)
        assert len(blocking) == 1
        assert blocking[0].status == "done"
        # the raw clear-before-insert really does violate the FK (the WHY)
        with pytest.raises(IntegrityError):
            async with SessionLocal() as s:
                await toc_repo.delete_for_book(s, book_id)
                await s.commit()
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_list_for_book_empty_when_no_jobs_and_delete_succeeds():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as s:
        book_id, _ = await _seed(s, with_job=False)
    try:
        async with SessionLocal() as s:
            assert await jobs_repo.list_for_book(s, book_id) == []
        # a job-free book re-extracts fine — delete_for_book removes the entry
        async with SessionLocal() as s:
            removed = await toc_repo.delete_for_book(s, book_id)
            await s.commit()
        assert removed == 1
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run RED.**

Run (scratch DB per the module docstring): the first test's `list_for_book` call raises `AttributeError` (function absent). Expected: FAIL.

- [ ] **Step 3: Add the repo function.**

In `app/repositories/jobs.py`, after `latest_by_section` (near line 275), add:

```python
async def list_for_book(session: AsyncSession, book_id: UUID) -> list[HomeworkJob]:
    """Every homework job referencing a book's TOC. `book_id` is set together
    with `toc_entry_id` at create time (see `create`), so a `book_id` filter is
    exactly the set of jobs whose `toc_entry_id` FK would block a
    `delete_for_book` clear-before-insert. Used by the TOC re-extract guard to
    refuse loudly and list the blocking jobs. Index-backed by
    `ix_homework_jobs_book_toc`."""
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.book_id == book_id)
        .order_by(HomeworkJob.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())
```

(`select`, `HomeworkJob`, `AsyncSession`, `UUID` are already imported in `jobs.py` — verify at the top of the file; add nothing if present.)

- [ ] **Step 4: Run GREEN.** Both tests pass on the scratch DB.

- [ ] **Step 5: Commit.**

```bash
git add app/repositories/jobs.py tests/integration/test_toc_reextract_guard.py
git commit -m "tocfk: jobs_repo.list_for_book + FK-reality proof"
```

---

### Task 2: Re-extract endpoint guard (409) + stale-comment fix

**Files:**
- Modify: `app/api/v1/books.py:226-250` (`retry_toc_extraction`)
- Modify: `app/services/toc_extractor.py:102-107` (comment only)
- Modify: `tests/api/test_toc_retry.py`

- [ ] **Step 1: Extend the test harness + write the guard tests (RED).**

In `tests/api/test_toc_retry.py`, update `_retry` to also stub the new repo call (default: no blocking jobs, so existing tests stay green), and add two guard tests.

Replace the `_retry` helper (lines 33-45) with:

```python
def _retry(book_id, *, book, pdf_exists=True, blocking_jobs=None):
    """Drive the endpoint with the repo/extractor/storage stubbed out. Returns
    (response, run_spy, set_status_spy)."""
    run_spy = AsyncMock()
    pdf_path = SimpleNamespace(exists=lambda: pdf_exists)
    with patch("app.api.v1.books.books_repo.get", AsyncMock(return_value=book)), \
         patch("app.api.v1.books.books_repo.set_status", AsyncMock()) as set_status, \
         patch("app.api.v1.books.storage.book_pdf_path", return_value=pdf_path), \
         patch("app.api.v1.books.jobs_repo.list_for_book",
               AsyncMock(return_value=blocking_jobs or [])), \
         patch("app.api.v1.books.toc_extractor.run", run_spy), \
         patch("app.api.v1.books._book_out_with_toc",
               AsyncMock(return_value=_bookout(book_id, "toc_extracting"))):
        r = client.post(f"/api/v1/books/{book_id}/toc/retry")
    return r, run_spy, set_status
```

Append these tests:

```python
def test_retry_blocked_by_referencing_jobs_409():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="toc_review", subject="math-algebra")
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="done")
    r, run_spy, set_status = _retry(bid, book=book, blocking_jobs=[job])
    assert r.status_code == 409
    # the operator sees which job blocks
    assert str(jid) in r.json()["detail"]
    # book status is NOT flipped and no extraction fires
    run_spy.assert_not_awaited()
    set_status.assert_not_awaited()


def test_retry_proceeds_when_no_referencing_jobs():
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    r, run_spy, _ = _retry(bid, book=book, blocking_jobs=[])
    assert r.status_code == 200
    run_spy.assert_awaited_once()


def test_retry_409_caps_job_listing_at_20_with_total():
    # B1: a full-TOC book carries 50-60+ jobs; the payload lists ~20 + the total,
    # never the whole roster. RED-provable: without the [:20] cap all 25 enumerate.
    bid = uuid4()
    book = SimpleNamespace(id=bid, status="failed", subject="math-algebra")
    jobs = [SimpleNamespace(id=uuid4(), status="done") for _ in range(25)]
    r, run_spy, _ = _retry(bid, book=book, blocking_jobs=jobs)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "25 homework job(s)" in detail   # total count present
    assert detail.count("(done)") == 20     # listing capped at 20
    assert "(+5 more)" in detail            # overflow summarized
    run_spy.assert_not_awaited()
```

- [ ] **Step 2: Run RED.**

Run: `uv run python -m pytest tests/api/test_toc_retry.py -q`
Expected: `test_retry_blocked_by_referencing_jobs_409` FAILS (endpoint returns 200, fires the task) — the others pass.

- [ ] **Step 3: Add the guard.**

In `app/api/v1/books.py`, inside `retry_toc_extraction`, insert the guard **after** the `pdf_path.exists()` check and **before** `set_status` (after line 246, before line 247):

```python
    # Re-extraction clears the book's TOC entries (toc_extractor's
    # clear-before-insert). homework_jobs.toc_entry_id is a NOT-NULL FK with no
    # cascade, so any referencing job — of ANY status — would make that DELETE
    # raise a ForeignKeyViolation, flip the book to `failed`, and leave the old
    # TOC in place (WISHLIST toc-reextract-fk-blocked-1). Refuse LOUDLY instead:
    # the book keeps its current status and the operator deletes the blocking
    # jobs (delete the affected sections) before retrying.
    blocking = await jobs_repo.list_for_book(session, book_id)
    if blocking:
        listed = ", ".join(f"{j.id} ({j.status})" for j in blocking[:20])
        more = f" (+{len(blocking) - 20} more)" if len(blocking) > 20 else ""
        raise HTTPException(
            409,
            f"cannot re-extract the TOC: {len(blocking)} homework job(s) "
            "reference this book's sections and would be orphaned. Delete the "
            "affected sections (or their jobs) first, then retry. Blocking jobs: "
            f"{listed}{more}",
        )
```

- [ ] **Step 4: Run GREEN.**

Run: `uv run python -m pytest tests/api/test_toc_retry.py -q`
Expected: all pass (the new 409 test + the eight existing).

- [ ] **Step 5: Fix the now-false comment in `toc_extractor.py`.**

Replace the comment block at `app/services/toc_extractor.py:102-107` (the `Clear-before-insert … never surfaced for generation.` paragraph + the `delete_for_book` line's lead-in) with:

```python
            # Clear-before-insert so a re-extract replaces rather than appends
            # (bulk_create is a naive append; toc_entries has no unique
            # constraint). The re-extract entrypoint (POST /books/{id}/toc/retry)
            # refuses upstream with a 409 when any homework_jobs row references
            # this book's TOC, so this DELETE never hits the toc_entry_id FK for
            # a book with jobs (WISHLIST toc-reextract-fk-blocked-1). A brand-new
            # book (ingest_pdf) has no jobs yet.
            await toc_repo.delete_for_book(session, book_id)
```

- [ ] **Step 6: Run the file's tests + full API slice.**

Run: `uv run python -m pytest tests/api/test_toc_retry.py tests/services/ -q -k "toc"`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add app/api/v1/books.py app/services/toc_extractor.py tests/api/test_toc_retry.py
git commit -m "tocfk: re-extract refuses with 409 when jobs reference the TOC"
```

---

### Task 3: `BookOut.deduplicated` flag on the dedup ingest path

**Files:**
- Modify: `app/schemas/book.py`
- Modify: `app/api/v1/books.py:85-87` (`ingest_pdf` dedup branch)
- Create: `tests/test_bookout_deduplicated.py`
- Modify: `tests/api/test_ingest_pdf.py`

- [ ] **Step 1: Write the schema + ingest tests (RED).**

Create `tests/test_bookout_deduplicated.py`:

```python
"""BookOut.deduplicated defaults False and is set True only on the dedup path."""
from uuid import uuid4

from app.schemas import BookOut


def test_deduplicated_defaults_false():
    out = BookOut(id=uuid4(), subject="biology",
                  original_filename="b.pdf", status="uploading")
    assert out.deduplicated is False
```

Rewrite `test_ingest_pdf_dedup_hit_returns_with_toc` in `tests/api/test_ingest_pdf.py` so the dedup branch returns a real `BookOut` whose flag gets set:

```python
@pytest.mark.asyncio
async def test_ingest_pdf_dedup_hit_flags_deduplicated():
    from app.schemas import BookOut
    session = AsyncMock()
    existing = SimpleNamespace(id=uuid4())
    reused = BookOut(id=existing.id, subject="biology",
                     original_filename="b.pdf", status="toc_ready")
    with patch.object(books_api.books_repo, "find_ready_by_hash",
                      AsyncMock(return_value=existing)), \
         patch.object(books_api, "_book_out_with_toc",
                      AsyncMock(return_value=reused)) as wt:
        out = await books_api.ingest_pdf(session, body=b"%PDF-1.4 x", subject="biology",
                                         grade="9", filename="b.pdf")
    assert out.deduplicated is True
    wt.assert_awaited_once()
```

- [ ] **Step 2: Run RED.**

Run: `uv run python -m pytest tests/test_bookout_deduplicated.py tests/api/test_ingest_pdf.py -q`
Expected: `test_deduplicated_defaults_false` FAILS (unknown field → pydantic ignores/errors) and `test_ingest_pdf_dedup_hit_flags_deduplicated` FAILS (`out.deduplicated` is False).

- [ ] **Step 3: Add the field.**

In `app/schemas/book.py`, add after the `toc:` line (line 26):

```python
    deduplicated: bool = False
    """True only when this response reused an existing book (sha dedup hit) —
    lets the FE show 'already exists — reusing' instead of a 'Preparing' state
    for extraction that never runs."""
```

- [ ] **Step 4: Set it on the dedup branch.**

In `app/api/v1/books.py`, replace the dedup branch (lines 85-87):

```python
    existing = await books_repo.find_ready_by_hash(session, sha, subject)
    if existing is not None:
        out = await _book_out_with_toc(session, existing.id)
        out.deduplicated = True
        return out
```

- [ ] **Step 5: Run GREEN.**

Run: `uv run python -m pytest tests/test_bookout_deduplicated.py tests/api/test_ingest_pdf.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/schemas/book.py app/api/v1/books.py tests/test_bookout_deduplicated.py tests/api/test_ingest_pdf.py
git commit -m "tocfk: BookOut.deduplicated flag on sha-dedup ingest path"
```

---

### Task 4: FE — Book type + upload toast branch

**Files:**
- Modify: `web/src/lib/types.ts:94-109` (`Book`)
- Modify: `web/src/routes/upload.tsx:77-79`

- [ ] **Step 1: Add the field to the FE type.**

In `web/src/lib/types.ts`, inside `interface Book`, after `toc: TOCEntry[] | null;` (line 108):

```ts
  /** True when the upload/fetch reused an existing book (sha dedup) — no
   *  extraction runs, so the UI must not show a "Preparing" state. */
  deduplicated?: boolean;
```

- [ ] **Step 2: Branch the upload toast.**

In `web/src/routes/upload.tsx`, replace lines 77-79:

```tsx
      const book = await api.uploadBook(file, subject as Subject, grade || undefined, sourceLanguage);
      toast.success(
        book.deduplicated ? "This book already exists — reusing it." : "Uploaded.",
      );
      navigate(`/book/${book.id}`);
```

- [ ] **Step 3: Typecheck.**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit.**

```bash
git add web/src/lib/types.ts web/src/routes/upload.tsx
git commit -m "tocfk: FE surfaces book.deduplicated on upload (launcher toast → lane C)"
```

---

### Task 5: Finish — docs de-stale, worklog, WISHLIST, plan rename

**Files:**
- Modify: `docs/HOW_IT_WORKS.md:587-590`, `docs/CODE_MAP.md:19`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/WISHLIST.md`
- Rename: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: De-stale `HOW_IT_WORKS.md`.** Update the `POST /books/{id}/toc/retry` bullet (lines 587-590) to note it **refuses with 409 when the book's sections are referenced by homework jobs** (delete the sections first), alongside the existing "idempotent, clears prior entries" note.

- [ ] **Step 2: De-stale `CODE_MAP.md`.** In the `books.py` bullet (line 19), extend the `POST /{id}/toc/retry` clause to mention the 409 job-reference guard and the `BookOut.deduplicated` dedup flag.

- [ ] **Step 3: Worklog 0122** in `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`.

- [ ] **Step 4: Close `toc-reextract-fk-blocked-1`** in `docs/memory/WISHLIST.md` (remove/annotate the shipped line; leave `toc-reextract-override-1` untouched).

- [ ] **Step 5: Rename the plan** (history-preserving):

```bash
git mv docs/superpowers/plans/2026-07-06-toc-reextract-fk-guard.md docs/superpowers/plans/shipped/
```

- [ ] **Step 6: Commit the finish.**

```bash
git add docs/HOW_IT_WORKS.md docs/CODE_MAP.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md docs/superpowers/plans/shipped/2026-07-06-toc-reextract-fk-guard.md
git commit -m "tocfk: finish — docs de-stale + worklog 0122 + close toc-reextract-fk-blocked-1"
```

---

## Acceptance gate

No paid model calls anywhere. Proof is:
1. Full suite green: `uv run python -m pytest tests/ -q` (canonical bar = *without* `RUN_DB_INTEGRATION`; expect only the 2 pre-existing `test_failover_api` reds).
2. Real-DB guard proof on the scratch DB (Task 1's module docstring commands) — FK-reality + `list_for_book` both green.
3. FE typecheck green (Task 4 Step 3).

## PR body must state
- Closes WISHLIST `toc-reextract-fk-blocked-1`.
- **Lane-C handoff:** ships `BookOut.deduplicated` + the `Book.deduplicated` FE type; the phantom "Preparing" toast at `launcher.tsx:128` (lane C's `prepare` mutation) should branch on `book.deduplicated` — **left for lane C**, not touched here.
- No migration; `toc_extractor` internals unchanged (comment-only edit).
