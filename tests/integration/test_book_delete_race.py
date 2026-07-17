"""Real-DB race proofs for BE-02 task 3: book-scoped Postgres advisory locks
close the activation-vs-delete race between `DELETE /books/{id}` (exclusive
lock) and the five activation paths — single-section `/generate`, job retry,
batch launch, batch resume, TOC retry (shared lock each).

RUN_DB_INTEGRATION=1 against a real Postgres. Every race uses TWO INDEPENDENT
sessions/connections (never share one session across concurrent acquirers —
the advisory lock would self-deadlock within one transaction), same
discipline as `tests/integration/test_credential_limiter.py`.

Winner-conditional oracle (binding — a bare any-of-404/409/204 assertion is
BANNED, since that would pass while silently deleting a just-created active
job):
  (a) activator commits first  -> delete returns 409 AND the activated work
      survives (row present, status pending/running, or book status
      toc_extracting for the TOC-retry path).
  (b) delete commits first     -> the activator returns a controlled 404/409
      AND homework_jobs/batches gained ZERO rows for the book.
  (c) plain delete, no competitor -> 204.

Blocking is proven the same way as the credential-limiter tests: start the
loser as a background task, `asyncio.sleep` a short window, assert it is
NOT done yet, then release the winner and await the loser.

RED reproduction (recorded in task-3-report.md): every test below was run
once against the pre-task-3 code (`git stash` the call-site wiring in
jobs.py/batch.py/books.py while keeping this test file and the new
`lock_book_shared`/`lock_book_exclusive` helpers) — with no lock in the
route, the "delete wins" races raised a raw ForeignKeyViolation (the
activator inserted/updated a row referencing a book the delete had already
removed), and the "activator wins" races let delete silently remove the
just-activated row instead of 409ing. See the report for the real output.
"""
from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_USER = {"user": "test"}


async def _seed_book(s, *, status: str = "toc_ready", n_lessons: int = 1):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=f"race-{uuid4().hex[:8]}.pdf",
        content_sha256=uuid4().hex + uuid4().hex,
        file_size_bytes=1,
        status=status,
    )
    s.add(book)
    await s.flush()
    tocs = []
    for i in range(n_lessons):
        t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
        s.add(t)
        tocs.append(t)
    await s.flush()
    return book, tocs


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from sqlalchemy import delete

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _count_jobs_and_batches(book_id) -> tuple[int, int]:
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    from sqlalchemy import func, select

    async with SessionLocal() as s:
        n_jobs = (
            await s.execute(
                select(func.count()).select_from(HomeworkJob).where(HomeworkJob.book_id == book_id)
            )
        ).scalar_one()
        n_batches = (
            await s.execute(
                select(func.count()).select_from(Batch).where(Batch.book_id == book_id)
            )
        ).scalar_one()
        return n_jobs, n_batches


# ─── Part A: lock helper semantics (direct, no routes) ───────────────────────
# Proves the two Postgres advisory-lock primitives actually have the
# blocking/non-blocking semantics every race below depends on.


@pytest.mark.asyncio
async def test_shared_holder_blocks_a_concurrent_exclusive_acquirer():
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    book_id = uuid4()  # advisory locks are keyed by hashtext(str) only — no row needed
    release = asyncio.Event()

    async def _hold_shared():
        async with SessionLocal() as s:
            await books_repo.lock_book_shared(s, book_id)
            await release.wait()
            await s.commit()

    holder = asyncio.create_task(_hold_shared())
    await asyncio.sleep(0.1)  # let the holder actually acquire first

    async def _try_exclusive():
        async with SessionLocal() as s:
            await books_repo.lock_book_exclusive(s, book_id)
            await s.commit()

    waiter = asyncio.create_task(_try_exclusive())
    await asyncio.sleep(0.3)
    assert not waiter.done(), "exclusive acquire must BLOCK while a shared holder is open"

    release.set()
    await asyncio.wait_for(holder, timeout=5)
    await asyncio.wait_for(waiter, timeout=5)  # now unblocks


@pytest.mark.asyncio
async def test_exclusive_holder_blocks_a_concurrent_shared_acquirer():
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    book_id = uuid4()
    release = asyncio.Event()

    async def _hold_exclusive():
        async with SessionLocal() as s:
            await books_repo.lock_book_exclusive(s, book_id)
            await release.wait()
            await s.commit()

    holder = asyncio.create_task(_hold_exclusive())
    await asyncio.sleep(0.1)

    async def _try_shared():
        async with SessionLocal() as s:
            await books_repo.lock_book_shared(s, book_id)
            await s.commit()

    waiter = asyncio.create_task(_try_shared())
    await asyncio.sleep(0.3)
    assert not waiter.done(), "shared acquire must BLOCK while an exclusive holder is open"

    release.set()
    await asyncio.wait_for(holder, timeout=5)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_two_shared_holders_do_not_serialize():
    """Two concurrent ACTIVATORS take the shared lock simultaneously — no
    serialization between them (only the exclusive delete form contends)."""
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    book_id = uuid4()
    acquired_at: dict[str, float] = {}

    async def _holder(name: str):
        async with SessionLocal() as s:
            await books_repo.lock_book_shared(s, book_id)
            acquired_at[name] = time.monotonic()
            await asyncio.sleep(0.4)  # hold it open — would expose serialization
            await s.commit()

    t0 = time.monotonic()
    await asyncio.gather(_holder("a"), _holder("b"))

    # Both acquired promptly — neither waited out the other's 0.4s hold.
    assert acquired_at["a"] - t0 < 0.2, acquired_at
    assert acquired_at["b"] - t0 < 0.2, acquired_at


# ─── Part B: batch-launch vs delete ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_launch_activator_wins_delete_409s_and_batch_job_survive():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s)
        await s.commit()
        book_id, toc_id = book.id, tocs[0].id

    try:
        async with SessionLocal() as sH:
            # Simulate an in-flight batch launch: past its guard read, about
            # to write, holding the shared lock across an un-committed insert.
            await books_repo.lock_book_shared(sH, book_id)
            job = await jobs_repo.create(
                sH, book_id=book_id, toc_entry_id=toc_id, subject="math-algebra",
                output_language="uz", status="pending")
            await sH.flush()
            job_id = job.id

            async with SessionLocal() as sB:
                delete_task = asyncio.create_task(delete_book(book_id, sB))
                await asyncio.sleep(0.3)
                assert not delete_task.done(), (
                    "delete must BLOCK while the activator holds the shared lock")

                await sH.commit()  # activator wins: release the shared lock

                with pytest.raises(HTTPException) as exc_info:
                    await delete_task
                assert exc_info.value.status_code == 409
                assert "active job(s)" in exc_info.value.detail

        async with SessionLocal() as s:
            row = await s.get(HomeworkJob, job_id)
            assert row is not None, "the activated job must SURVIVE"
            assert row.status == "pending"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_batch_launch_delete_wins_launch_404s_and_creates_zero_rows():
    from app.api.v1.batch import BatchLaunchRequest, launch_batch
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as sH:
        # Simulate an in-flight delete: past its guards, mid-transaction,
        # holding the exclusive lock across the un-committed row removal.
        await books_repo.lock_book_exclusive(sH, book_id)
        deleted = await books_repo.delete(sH, book_id)
        assert deleted is True

        async with SessionLocal() as sA:
            body = BatchLaunchRequest(book_id=book_id, provider="claude", transport="cli")
            launch_task = asyncio.create_task(launch_batch(body, sA))
            await asyncio.sleep(0.3)
            assert not launch_task.done(), (
                "launch must BLOCK while the delete holds the exclusive lock")

            await sH.commit()  # delete wins: release the exclusive lock

            with pytest.raises(HTTPException) as exc_info:
                await launch_task
            assert exc_info.value.status_code == 404

    n_jobs, n_batches = await _count_jobs_and_batches(book_id)
    assert (n_jobs, n_batches) == (0, 0), "the book must gain ZERO rows"
    # book itself is already gone — nothing left to clean up.


# ─── Part C: job-retry vs delete ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_retry_activator_wins_delete_409s_and_job_survives_pending():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s)
        job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=tocs[0].id, subject="math-algebra",
            output_language="uz", status="failed")
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        async with SessionLocal() as sH:
            await books_repo.lock_book_shared(sH, book_id)
            await jobs_repo.reset_for_retry(sH, job_id)  # failed -> pending, uncommitted

            async with SessionLocal() as sB:
                delete_task = asyncio.create_task(delete_book(book_id, sB))
                await asyncio.sleep(0.3)
                assert not delete_task.done()

                await sH.commit()

                with pytest.raises(HTTPException) as exc_info:
                    await delete_task
                assert exc_info.value.status_code == 409

        async with SessionLocal() as s:
            row = await s.get(HomeworkJob, job_id)
            assert row is not None
            assert row.status == "pending"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_job_retry_delete_wins_retry_404s_no_resurrection():
    from app.api.v1.jobs import retry_job
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s)
        job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=tocs[0].id, subject="math-algebra",
            output_language="uz", status="failed")
        await s.commit()
        book_id, job_id = book.id, job.id

    async with SessionLocal() as sH:
        await books_repo.lock_book_exclusive(sH, book_id)
        await books_repo.delete(sH, book_id)  # removes the job row too, uncommitted

        async with SessionLocal() as sA:
            retry_task = asyncio.create_task(retry_job(job_id, sA, _USER))
            await asyncio.sleep(0.3)
            assert not retry_task.done(), (
                "retry must BLOCK while the delete holds the exclusive lock")

            await sH.commit()

            with pytest.raises(HTTPException) as exc_info:
                await retry_task
            assert exc_info.value.status_code == 404, (
                "retry of a job whose book was just deleted must 404, never resurrect it")

    n_jobs, n_batches = await _count_jobs_and_batches(book_id)
    assert (n_jobs, n_batches) == (0, 0)


# ─── Part D: TOC retry vs delete (gate detail) ───────────────────────────────


@pytest.fixture
def _tmp_var_dir(tmp_path, monkeypatch):
    """Point storage at a throwaway VAR_DIR so the TOC-retry races can write a
    real `source.pdf` without touching the repo's real `var/` tree."""
    import app.api.v1.books as books_mod

    monkeypatch.setattr(books_mod.settings, "var_dir", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_toc_retry_activator_wins_status_extracting_pdf_and_task_retained(_tmp_var_dir):
    """Drives the REAL `retry_toc_extraction` route as the activator (not a
    manual `set_status` stand-in — that only proves the write shape, not that
    the route itself holds the lock across its guard-checks-then-write). To
    keep the lock held open while the competing delete blocks, gate the
    route's own `jobs_repo.list_for_book` call (the last read before its write
    + commit) behind an `asyncio.Event` — same "hold it open, prove the loser
    blocks, then release" discipline as every other race in this file, just
    triggered from inside the real call instead of a hand-rolled stand-in."""
    import app.api.v1.books as books_mod
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo
    from app.services import storage

    async with SessionLocal() as s:
        book, _tocs = await _seed_book(s, status="failed", n_lessons=0)
        await s.commit()
        book_id = book.id

    pdf_path = storage.book_pdf_path(book_id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    spawn_calls: list = []

    def _record_start_toc_extraction(bid, path, subject):
        # Replaces the real fire-and-forget `asyncio.create_task` with a
        # synchronous recorder: proves the spawn call actually happened,
        # deterministically, with no dependency on scheduler timing.
        spawn_calls.append((bid, path, subject))

    lock_taken = asyncio.Event()
    resume_write = asyncio.Event()
    real_list_for_book = jobs_repo.list_for_book

    async def _gated_list_for_book(session, bid):
        # Fires after the route's lock-acquire + expire/re-fetch + status
        # guard, right before its write — the shared lock is held and
        # un-committed for as long as this coroutine is paused here.
        lock_taken.set()
        await resume_write.wait()
        return await real_list_for_book(session, bid)

    try:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(books_mod, "_start_toc_extraction", _record_start_toc_extraction)
            mp.setattr(books_mod.jobs_repo, "list_for_book", _gated_list_for_book)

            async with SessionLocal() as sH:
                retry_task = asyncio.create_task(
                    books_mod.retry_toc_extraction(book_id, sH, _USER))
                await asyncio.wait_for(lock_taken.wait(), timeout=5)

                async with SessionLocal() as sB:
                    from app.api.v1.books import delete_book

                    delete_task = asyncio.create_task(delete_book(book_id, sB))
                    await asyncio.sleep(0.3)
                    assert not delete_task.done(), (
                        "delete must BLOCK while the TOC-retry activator holds "
                        "the shared lock")

                    resume_write.set()  # let the real route finish its write + commit
                    book_out = await asyncio.wait_for(retry_task, timeout=5)
                    assert book_out.status == "toc_extracting", (
                        "the route call itself must succeed (200) and report "
                        "the flipped status")

                    with pytest.raises(HTTPException) as exc_info:
                        await delete_task
                    # ingest-in-flight guard (Task 2), same 409 family as the
                    # active-jobs guard — book is now 'toc_extracting'.
                    assert exc_info.value.status_code == 409

        # GATE DETAIL: status is toc_extracting, PDF retained on disk, and the
        # real route actually spawned the extraction task (positive proof).
        async with SessionLocal() as s:
            fresh = await books_repo.get(s, book_id)
            assert fresh is not None
            assert fresh.status == "toc_extracting"
        assert pdf_path.exists(), "the source PDF must still be on disk"
        assert len(spawn_calls) == 1 and spawn_calls[0][0] == book_id, (
            "the real route must have spawned the TOC extraction task")
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_toc_retry_delete_wins_retry_404s_no_task_spawned(_tmp_var_dir):
    import app.api.v1.books as books_mod
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.services import storage

    async with SessionLocal() as s:
        book, _tocs = await _seed_book(s, status="failed", n_lessons=0)
        await s.commit()
        book_id = book.id

    pdf_path = storage.book_pdf_path(book_id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    spawned: list = []

    async def _inert_run(bid, path, subject):
        spawned.append(bid)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(books_mod.toc_extractor, "run", _inert_run)

        async with SessionLocal() as sH:
            await books_repo.lock_book_exclusive(sH, book_id)
            await books_repo.delete(sH, book_id)

            async with SessionLocal() as sA:
                retry_task = asyncio.create_task(
                    books_mod.retry_toc_extraction(book_id, sA, _USER))
                await asyncio.sleep(0.3)
                assert not retry_task.done(), (
                    "TOC retry must BLOCK while the delete holds the exclusive lock")

                tasks_before = len(books_mod._TOC_TASKS)
                await sH.commit()

                with pytest.raises(HTTPException) as exc_info:
                    await retry_task
                assert exc_info.value.status_code == 404

        # GATE DETAIL: no extraction task was spawned, and the book row is gone.
        assert spawned == [], "no TOC extraction task may be spawned for a deleted book"
        assert len(books_mod._TOC_TASKS) == tasks_before

    n_jobs, n_batches = await _count_jobs_and_batches(book_id)
    assert (n_jobs, n_batches) == (0, 0)


# ─── Part E: batch-resume vs delete (shares the retry shape) ────────────────
# resume_batch derives book_id from the batch row exactly like retry_job
# derives it from the job row (fetch -> lock -> re-fetch -> proceed), and
# delete's own guard/lock behavior is already exhaustively proven in Parts B
# through D — this pair only needs one direction to confirm resume_batch's
# OWN re-fetch-after-lock actually fires (the thing unique to this path).


@pytest.mark.asyncio
async def test_batch_resume_delete_wins_resume_404s_no_resurrection():
    from app.api.v1.batch import resume_batch
    from app.db import SessionLocal
    from app.repositories import batches as batches_repo
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s)
        batch = await batches_repo.get_or_create_for_book(
            s, book_id=book.id, subject="math-algebra", grade=None,
            provider="claude", model=None, transport="cli", output_language="uz")
        job = await jobs_repo.create(
            s, book_id=book.id, toc_entry_id=tocs[0].id, subject="math-algebra",
            output_language="uz", status="failed", batch_id=batch.id)
        await s.commit()
        book_id, batch_id, job_id = book.id, batch.id, job.id

    async with SessionLocal() as sH:
        await books_repo.lock_book_exclusive(sH, book_id)
        await books_repo.delete(sH, book_id)  # removes job + batch, uncommitted

        async with SessionLocal() as sA:
            resume_task = asyncio.create_task(resume_batch(batch_id, sA))
            await asyncio.sleep(0.3)
            assert not resume_task.done(), (
                "resume must BLOCK while the delete holds the exclusive lock")

            await sH.commit()

            with pytest.raises(HTTPException) as exc_info:
                await resume_task
            assert exc_info.value.status_code == 404, (
                "resume of a batch whose book was just deleted must 404, never resurrect its jobs")

    n_jobs, n_batches = await _count_jobs_and_batches(book_id)
    assert (n_jobs, n_batches) == (0, 0)


# ─── Part F: two concurrent activators via the REAL /generate route ─────────


@pytest.mark.asyncio
async def test_two_concurrent_generates_on_same_book_do_not_serialize():
    """Real-route proof (not just the raw-lock timing test in Part A) that the
    shared lock taken by /generate doesn't serialize two activators on the
    SAME book.

    A bare wall-clock bound (`finished_at - t0 < 2.0s`) is non-diagnostic: it
    would pass even if `/generate` silently took an EXCLUSIVE lock, since two
    fast calls both land inside any generous bound whether or not they
    actually serialize behind each other. Replaced with the same
    barrier-overlap discipline as the raw-lock Part A test
    (`test_two_shared_holders_do_not_serialize`), just with one side driving
    the REAL route instead of both sides being raw lock calls: hold a shared
    lock open (uncommitted, indefinitely, via an `asyncio.Event` barrier) to
    stand in for one in-flight `/generate` transaction — same idiom as the
    "simulate an in-flight activator" holds in Parts B-D — then drive `/generate`
    for a DIFFERENT section through its own independent session and prove it
    actually COMPLETES. That's only possible if the route's own shared-lock
    acquisition does NOT block behind the held-open shared lock.
    """
    from app.api.v1.jobs import generate
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.schemas import GenerateRequest

    async with SessionLocal() as s:
        book, tocs = await _seed_book(s, n_lessons=2)
        await s.commit()
        book_id, toc_b = book.id, tocs[1].id

    release_holder = asyncio.Event()

    async def _hold_shared_open():
        async with SessionLocal() as s:
            await books_repo.lock_book_shared(s, book_id)
            await release_holder.wait()
            await s.commit()

    try:
        holder = asyncio.create_task(_hold_shared_open())
        await asyncio.sleep(0.1)  # let the holder actually acquire first

        async with SessionLocal() as sB:
            resp = Response()
            job_out = await asyncio.wait_for(
                generate(book_id, toc_b, resp, GenerateRequest(transport="cli"),
                         None, sB, _USER),
                timeout=3,
            )
            assert job_out.id is not None, (
                "/generate must COMPLETE for a different section while another "
                "shared holder keeps its lock open on the same book")

        assert not holder.done(), (
            "the held-open shared lock must still be live — proves the "
            "/generate call above genuinely overlapped it, not that the "
            "holder had already released by the time /generate ran")
        release_holder.set()
        await asyncio.wait_for(holder, timeout=5)
    finally:
        await _cleanup(book_id)


# ─── Part G: plain delete, no competitor ─────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_delete_no_competitor_204():
    from app.api.v1.books import delete_book
    from app.db import SessionLocal
    from app.models.book import Book

    async with SessionLocal() as s:
        book, _tocs = await _seed_book(s)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as s:
        result = await delete_book(book_id, s)
    assert result is None  # 204 No Content

    async with SessionLocal() as s:
        assert await s.get(Book, book_id) is None


# ─── Part G: adjacent (non-activation) mutations vs delete (post-#100 fix) ───
# The four sibling mutation routes (toc/accept, PATCH book, PATCH/DELETE toc
# entry) mutate book-scoped rows. Unlocked, a delete committing between their
# read and their UPDATE raised StaleDataError -> 500. With the shared lock they
# serialize: delete-wins => clean 404; mutate-wins => delete proceeds after.


@pytest.mark.asyncio
async def test_update_book_delete_wins_patch_404s_never_500():
    from app.api.v1.books import update_book, BookUpdateRequest
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book(s)
        await s.commit()
        book_id = book.id

    async with SessionLocal() as sH:
        await books_repo.lock_book_exclusive(sH, book_id)
        await books_repo.delete(sH, book_id)  # uncommitted

        async with SessionLocal() as sA:
            patch_task = asyncio.create_task(update_book(
                book_id, BookUpdateRequest(original_filename="renamed.pdf"), sA))
            await asyncio.sleep(0.3)
            assert not patch_task.done(), (
                "PATCH must BLOCK while the delete holds the exclusive lock")

            await sH.commit()

            with pytest.raises(HTTPException) as exc_info:
                await patch_task
            assert exc_info.value.status_code == 404, (
                "PATCH of a just-deleted book must 404 cleanly, never "
                "StaleDataError/500")


@pytest.mark.asyncio
async def test_update_book_patch_wins_then_delete_succeeds():
    from app.api.v1.books import update_book, delete_book, BookUpdateRequest
    from app.db import SessionLocal
    from app.repositories import books as books_repo

    async with SessionLocal() as s:
        book, _ = await _seed_book(s)
        await s.commit()
        book_id = book.id

    # PATCH completes first (uncontested), then delete proceeds normally.
    async with SessionLocal() as sA:
        out = await update_book(
            book_id, BookUpdateRequest(original_filename="renamed.pdf"), sA)
        assert out.original_filename == "renamed.pdf"

    async with SessionLocal() as sD:
        await delete_book(book_id, sD)

    async with SessionLocal() as s:
        assert await books_repo.get(s, book_id) is None
