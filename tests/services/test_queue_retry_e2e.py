"""E2E (real chain, scratch DB): Worker._execute_job -> pipeline.run ->
_execute_one_phase -> _run_with_failover (hung provider boundary) ->
Worker._mark_failed -> jobs_repo.mark_failed_with_retry.

First execution: delayed pending (attempt burned, future scheduled_at,
last_error carries 'per-attempt timeout'). Final allowed attempt: terminal
failed. Closes the review's named test gap (queue-correctness-1) — every
link in the chain is real production code except the hung provider call
itself (agent.summarize_lesson) and the per-attempt clock.

Step 1 (real-DB-marked, RUN_DB_INTEGRATION=1): the chain test.
Step 2 (no flag): three fast per-link regression pins, safe without a DB.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.errors import PhaseAttemptTimeout, TransientPhaseError

_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
_SKIP_REASON = "set RUN_DB_INTEGRATION=1 with a live DATABASE_URL to run"

# The brief's shorthand "book text" would be 10 chars — well under
# settings.extract_min_text_chars (500), which trips Gate A
# (agent.validate_extract_text) and diverts the extract phase into the
# SCANNED/vision branch (agent.summarize_lesson_vision, which BYPASSES
# _run_with_failover entirely per pipeline.py's own comment at the vision
# call site) instead of the normal _run_with_failover(run_fn=summarize_lesson)
# path this test is built to exercise. Deviation from the literal brief text:
# padded to real, plausible English prose so Gate A (length, printable-letter
# ratio, alphabet-plausibility) passes and the chain reaches the hung
# agent.summarize_lesson call inside _run_with_failover, which is the whole
# point of Step 1.
_READABLE_BOOK_TEXT = (
    "This is a plain readable passage of sample lesson text standing in for "
    "the whole book so the extract phase's Gate A local-text checks pass and "
    "the real failover path is exercised instead of the scanned-PDF vision "
    "branch. "
) * 8


async def _seed_chain_fixture(session, *, pdf_bytes: bytes):
    """Seed one toc_ready book + toc_entry + pending api-transport gemini job,
    stamped judge/extract/solver provider='gemini' so every claim-gate role
    gates on the single can_gemini_api capability flag."""
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo
    from app.models.toc_entry import TOCEntry
    from app.services import storage

    book = await books_repo.create(
        session,
        subject="history",
        original_filename="e2e_queue_retry.pdf",
        content_sha256=uuid.uuid4().hex.ljust(64, "0"),
        file_size_bytes=len(pdf_bytes),
        status="toc_ready",
        grade="9",
    )
    pdf_path = storage.book_pdf_path(book.id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)

    toc = TOCEntry(
        book_id=book.id,
        section_title="E2E Queue Retry Lesson",
        section_number="1",
        page_start=1,
        page_end=1,
        order_index=0,
    )
    session.add(toc)
    await session.flush()

    job = await jobs_repo.create(
        session,
        book_id=book.id,
        toc_entry_id=toc.id,
        subject="history",
        output_language="uz",
        provider="gemini",
        model="gemini-2.5-flash",
        transport="api",
        judge_provider="gemini",
        extract_provider="gemini",
        solver_provider="gemini",
    )
    return book, toc, job


async def _seed_decoy_and_park_others(session, job_id, *, pdf_bytes: bytes):
    """Isolation guard for claim_next_job (review finding: an unasserted
    ``job is not None`` silently claims an UNRELATED leftover job on the
    scratch DB, which is deliberately never wiped between test files).

    Seeds an unrelated pending job with an OLDER scheduled_at than `job_id`
    — it would win claim_next_job's FIFO tiebreak (oldest scheduled_at
    first) over the seeded job if nothing parked it — then pushes every
    OTHER claimable pending row (the decoy plus any real leftovers other
    test files left behind) an hour into the future. Scratch-DB leftovers
    are garbage: there is nothing to restore, the parking only needs to
    hold for this test's lifetime.

    Returns the decoy book id for teardown.
    """
    from sqlalchemy import update

    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    decoy_book = await books_repo.create(
        session,
        subject="history",
        original_filename="e2e_queue_retry_decoy.pdf",
        content_sha256=uuid.uuid4().hex.ljust(64, "0"),
        file_size_bytes=len(pdf_bytes),
        status="toc_ready",
        grade="9",
    )
    decoy_toc = TOCEntry(
        book_id=decoy_book.id,
        section_title="Decoy Lesson (parking bait)",
        section_number="1",
        page_start=1,
        page_end=1,
        order_index=0,
    )
    session.add(decoy_toc)
    await session.flush()

    decoy_job = await jobs_repo.create(
        session,
        book_id=decoy_book.id,
        toc_entry_id=decoy_toc.id,
        subject="history",
        output_language="uz",
        provider="gemini",
        model="gemini-2.5-flash",
        transport="api",
        judge_provider="gemini",
        extract_provider="gemini",
        solver_provider="gemini",
    )
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == decoy_job.id)
        .values(scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1))
    )

    # The fix under test: without this park, claim_next_job's FIFO tiebreak
    # picks the decoy above (or any other never-wiped scratch-DB leftover)
    # ahead of `job_id`.
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.id != job_id)
        .values(scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
    )

    return decoy_book.id


async def _cleanup_chain_fixture(book_id) -> None:
    if book_id is None:
        return
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        job_ids = (
            await s.execute(
                delete(HomeworkJob).where(HomeworkJob.book_id == book_id).returning(HomeworkJob.id)
            )
        ).scalars().all()
        if job_ids:
            await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_e2e_chain_attempt_timeout_to_bounded_pending_then_terminal_failed(
    tmp_path, monkeypatch
):
    """The real chain, gate correction 3. Pins every link named in the task-7
    brief:

      1. seed (Gate-A-passing book text + toc + api-transport gemini job)
      2. stub ONLY the provider boundary (agent.summarize_lesson hangs) +
         the per-attempt clock (settings.per_attempt_timeout_seconds=0.05)
      3. claim_next_job for real, with the exact capability shape an
         api-content/api-judge/api-extract/api-solver gemini job needs
      4. drive Worker._execute_job for real
      5. pin: _run_with_failover's asyncio.wait_for catches the hang ->
         PhaseAttemptTimeout (non-blank, typed) -> _execute_one_phase wraps
         it as TransientPhaseError -> pipeline.run's outer
         except (..., TransientPhaseError): raise (pipeline.py:460) lets it
         escape uncaught -> Worker._execute_job's generic `except Exception`
         (worker.py:576) -> self._mark_failed -> jobs_repo.mark_failed_with_retry
         retry branch: status='pending', attempts stays 1 (burned once by the
         claim), scheduled_at pushed ~30s into the future, last_error carries
         the non-blank 'per-attempt timeout' text.
      6. fast-forward attempts to (max_attempts - 1), re-claim (burns the
         final attempt), drive again -> mark_failed_with_retry's terminal
         branch: status='failed', error_message carries 'per-attempt
         timeout', completed_at set.
    """
    from app.config import settings
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from app.services import agent, events_bus, pipeline
    from app.services.worker import Worker
    from sqlalchemy import select, update

    monkeypatch.setattr(settings, "var_dir", str(tmp_path))

    book_id = None
    decoy_book_id = None
    try:
        async with SessionLocal() as session:
            book, toc, job = await _seed_chain_fixture(
                session, pdf_bytes=b"%PDF-1.4 fake e2e queue retry"
            )
            book_id = book.id
            job_id = job.id
            decoy_book_id = await _seed_decoy_and_park_others(
                session, job_id, pdf_bytes=b"%PDF-1.4 fake e2e queue retry decoy"
            )
            await session.commit()

        # ── Stub ONLY the provider boundary + the per-attempt clock ──
        monkeypatch.setattr(settings, "per_attempt_timeout_seconds", 0.05)
        monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda pdf_path: _READABLE_BOOK_TEXT)

        async def _hung_summarize_lesson(**kwargs):
            await asyncio.sleep(60)

        monkeypatch.setattr(pipeline.agent, "summarize_lesson", _hung_summarize_lesson)
        # The scratch DB's LISTEN/NOTIFY setup is not guaranteed under a bare
        # `uv run pytest` invocation — patch the advisory event bus (per the
        # task-7 brief) so the assertions below depend only on the DB rows,
        # not on a live listener.
        monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
        monkeypatch.setattr(pipeline.events_bus, "close", AsyncMock())

        worker = Worker(concurrency=1)

        # ── First pass: claim (attempts 0 -> 1), drive, expect delayed pending ──
        async with SessionLocal() as session:
            job = await jobs_repo.claim_next_job(
                session, worker_id="e2e-worker", max_attempts=3,
                capabilities={"can_gemini_api": True, "can_claude_api": False,
                              "can_clodex_api": False},
            )
            await session.commit()
        assert job is not None and job.status == "running" and job.attempts == 1
        assert job.id == job_id, (
            f"claimed unrelated job {job.id} instead of the seeded job {job_id} "
            f"— isolation-park leak on the scratch DB"
        )

        await worker._execute_job(job_id)

        async with SessionLocal() as session:
            row = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()

        assert row.status == "pending", (
            f"expected delayed pending after the first burned attempt, got "
            f"{row.status!r} (last_error={row.last_error!r})"
        )
        assert row.attempts == 1, f"expected attempts==1 (one claim, no re-claim yet), got {row.attempts}"
        now = datetime.now(timezone.utc)
        scheduled = row.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        assert scheduled > now, (
            f"scheduled_at must be pushed into the future by the backoff; "
            f"scheduled={scheduled}, now={now}"
        )
        last_error = row.last_error or ""
        assert "per-attempt timeout" in last_error, (
            f"expected 'per-attempt timeout' in last_error, got {last_error!r}"
        )
        tail = last_error.split("per-attempt timeout", 1)[1].strip()
        assert tail, (
            f"'per-attempt timeout' must not be followed by blank text (the "
            f"str(asyncio.TimeoutError())=='' bug PhaseAttemptTimeout fixes); "
            f"got last_error={last_error!r}"
        )

        # ── Fast-forward to the final allowed attempt, re-claim, drive again ──
        async with SessionLocal() as session:
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(attempts=3 - 1, scheduled_at=datetime.now(timezone.utc))
            )
            await session.commit()

        async with SessionLocal() as session:
            job = await jobs_repo.claim_next_job(
                session, worker_id="e2e-worker", max_attempts=3,
                capabilities={"can_gemini_api": True, "can_claude_api": False,
                              "can_clodex_api": False},
            )
            await session.commit()
        assert job is not None and job.status == "running" and job.attempts == 3
        assert job.id == job_id, (
            f"claimed unrelated job {job.id} instead of the seeded job {job_id} "
            f"— isolation-park leak on the scratch DB"
        )

        await worker._execute_job(job_id)

        async with SessionLocal() as session:
            row = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()

        assert row.status == "failed", (
            f"expected terminal failed once attempts==max_attempts, got "
            f"{row.status!r} (error_message={row.error_message!r})"
        )
        error_message = row.error_message or ""
        assert "per-attempt timeout" in error_message, (
            f"expected 'per-attempt timeout' in error_message, got {error_message!r}"
        )
        assert row.completed_at is not None, "completed_at must be set on terminal failure"
    finally:
        await _cleanup_chain_fixture(book_id)
        await _cleanup_chain_fixture(decoy_book_id)


# ─────────────────────────────────────────────────────────────────────────
# Step 2: fast per-link regression pins (no RUN_DB_INTEGRATION flag needed)
# ─────────────────────────────────────────────────────────────────────────


def test_run_with_failover_hung_run_fn_raises_phase_attempt_timeout(monkeypatch):
    """Link (a): _run_with_failover's own asyncio.wait_for must convert a hung
    run_fn into a typed, NON-BLANK PhaseAttemptTimeout — not a raw
    asyncio.TimeoutError (whose str() is '', the historical blank-error bug)."""
    from app.config import settings
    from app.services import pipeline

    monkeypatch.setattr(settings, "per_attempt_timeout_seconds", 0.05)

    async def _hang(prov, mdl):
        await asyncio.sleep(60)

    with pytest.raises(PhaseAttemptTimeout) as ei:
        asyncio.run(pipeline._run_with_failover(
            requested_provider="gemini", model=None, run_fn=_hang, transport="api",
        ))
    assert "per-attempt timeout" in str(ei.value)
    assert str(ei.value).split("per-attempt timeout", 1)[1].strip() != ""


def _stub_session_local(monkeypatch, pipeline_mod):
    """Async-context stub pattern copied from
    tests/services/test_pipeline_transient_propagation.py."""
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline_mod, "SessionLocal", MagicMock(return_value=fake_session))
    return fake_session


def test_pipeline_run_propagates_transient_phase_error(monkeypatch):
    """Link (b): pipeline.run's OUTER try/except (pipeline.py:460) must let a
    TransientPhaseError raised by _execute_one_phase escape uncaught — NOT
    fall into the generic `except Exception as exc` a few lines below
    (pipeline.py:465), which marks the job 'failed' and swallows the signal
    the worker needs to apply the bounded queue retry (today's-swallow
    RED-proof: with the tuple-catch removed, this test fails because
    pipeline.run() returns normally / raises nothing instead of propagating).

    Everything upstream of _execute_one_phase (job/book/section/launch-defaults
    context load) is mocked with plain values — this test only cares that the
    exception _execute_one_phase raises for the 'extract' phase makes it all
    the way out of run(), not about the load path itself (that's covered by
    the real-DB test_pipeline_ld_ordering.py and the Step 1 chain test above).
    """
    from app.services import pipeline
    from app.services.errors import TransientPhaseError as _TPE

    job_id = uuid.uuid4()
    book_id = uuid.uuid4()
    toc_id = uuid.uuid4()

    fake_job = SimpleNamespace(
        id=job_id, book_id=book_id, toc_entry_id=toc_id,
        provider="gemini", model=None, transport="api",
        extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit", custom_prompts=None, selected_phases=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        extract_provider=None, extract_model=None,
        batch_id=None, output_language="uz",
    )
    fake_book = SimpleNamespace(
        id=book_id, subject="history", grade="9", file_size_bytes=1,
    )
    fake_section = SimpleNamespace(
        id=toc_id, section_title="L1", section_number="1",
        page_start=1, page_end=1, chapter_title="", order_index=0,
    )
    fake_ld = SimpleNamespace(
        judge_provider="gemini", judge_model=None,
        solver_provider="gemini", solver_model=None,
        solver_boss_arena_enabled=False,
        extract_provider="gemini", extract_model=None,
    )

    from app.repositories import launch_defaults as ld_repo

    monkeypatch.setattr(pipeline.jobs_repo, "get", AsyncMock(return_value=fake_job))
    monkeypatch.setattr(pipeline.books_repo, "get", AsyncMock(return_value=fake_book))
    monkeypatch.setattr(pipeline.toc_repo, "get", AsyncMock(return_value=fake_section))
    monkeypatch.setattr(pipeline.toc_repo, "get_next_in_book", AsyncMock(return_value=None))
    monkeypatch.setattr(ld_repo, "get", AsyncMock(return_value=fake_ld))
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", AsyncMock(return_value=True))
    monkeypatch.setattr(pipeline.phase_repo, "list_for_job", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf_sync",
        lambda *a, **k: pipeline.storage.book_pdf_path(book_id),
    )
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    monkeypatch.setattr(pipeline.events_bus, "close", AsyncMock())

    async def _raise_transient(*a, **k):
        raise _TPE("extract: per-attempt timeout after 0.05s (provider=gemini, transport=api)")

    monkeypatch.setattr(pipeline, "_execute_one_phase", _raise_transient)
    _stub_session_local(monkeypatch, pipeline)

    with pytest.raises(TransientPhaseError):
        asyncio.run(pipeline.run(job_id))
    # A hard-class exception would have gone through the generic `except
    # Exception as exc` branch and called jobs_repo.set_status(..., 'failed',
    # ...) instead of propagating — assert that never happened.
    for call in pipeline.jobs_repo.set_status.await_args_list:
        status_arg = call.args[2] if len(call.args) > 2 else call.kwargs.get("status")
        assert status_arg != "failed", (
            "TransientPhaseError must propagate WITHOUT the job being marked "
            "'failed' — that's the worker's job via mark_failed_with_retry"
        )


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_mark_failed_with_retry_pending_below_max_attempts():
    """Link (c1, real-DB-marked): seeded attempts=1 (below max_attempts=3) ->
    'pending' + scheduled_at pushed into the future by the backoff."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import select, update

    book_id = None
    try:
        async with SessionLocal() as session:
            book, toc, job = await _seed_chain_fixture(
                session, pdf_bytes=b"%PDF-1.4 fake mark-failed-pending"
            )
            book_id = book.id
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="running", attempts=1, claimed_by="w",
                    claimed_at=datetime.now(timezone.utc), current_phase="extract",
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.mark_failed_with_retry(
                session, job_id, error_message="extract: per-attempt timeout after 0.05s",
                max_attempts=3,
            )
            await session.commit()
        assert outcome == "pending", f"expected 'pending', got {outcome!r}"

        async with SessionLocal() as session:
            row = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
        assert row.status == "pending"
        now = datetime.now(timezone.utc)
        scheduled = row.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        assert scheduled > now, f"scheduled_at must be future; scheduled={scheduled}, now={now}"
    finally:
        await _cleanup_chain_fixture(book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_mark_failed_with_retry_terminal_at_max_attempts():
    """Link (c2, real-DB-marked): seeded attempts=3 (== max_attempts=3) ->
    terminal 'failed'."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import select, update

    book_id = None
    try:
        async with SessionLocal() as session:
            book, toc, job = await _seed_chain_fixture(
                session, pdf_bytes=b"%PDF-1.4 fake mark-failed-terminal"
            )
            book_id = book.id
            job_id = job.id
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == job_id)
                .values(
                    status="running", attempts=3, claimed_by="w",
                    claimed_at=datetime.now(timezone.utc), current_phase="extract",
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            outcome = await jobs_repo.mark_failed_with_retry(
                session, job_id, error_message="extract: per-attempt timeout after 0.05s",
                max_attempts=3,
            )
            await session.commit()
        assert outcome == "failed", f"expected 'failed', got {outcome!r}"

        async with SessionLocal() as session:
            row = (
                await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
            ).scalar_one()
        assert row.status == "failed"
        assert row.completed_at is not None
    finally:
        await _cleanup_chain_fixture(book_id)
