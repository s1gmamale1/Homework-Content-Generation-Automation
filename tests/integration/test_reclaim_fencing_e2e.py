"""Real-DB end-to-end: an obsolete worker CANNOT mutate a reclaimed job
(fenced job leases, Task 7).

The core scenario the whole feature exists to prevent:

  * Worker A claims a job (mints token T1).
  * A's claim is forced stale (claimed_at pushed into the past, no live
    registry row for A) and a reclaim sweep promotes it back to pending,
    rotating the claim_token off the row.
  * Worker B re-claims the same job (mints a fresh token T2) and now owns it.
  * A "wakes up" and tries its worker-owned writes with its DEAD token T1 —
    the fenced `done` / `failed` / phase writes all no-op with `LeaseLost`,
    the job is NEVER marked done (or failed) by A, and B still owns it.

This is the anti-double-completion guarantee. The broader race matrix and the
startup-reclaim path are covered by tasks 8/10; this test stays focused.

RUN_DB_INTEGRATION=1 required (real Postgres via the scratch DB recipe).
"""
from __future__ import annotations

import os
import uuid as _uuid

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)


@pytest.fixture
async def seed_pending_job():
    """A committed, claimable (pending, cli) homework job over a fresh book +
    section. Yields the job id; tears the whole graph down afterward."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    book_ids: list = []

    async def make():
        async with SessionLocal() as s:
            book = Book(
                subject="math-algebra",
                original_filename="reclaim-e2e.pdf",
                content_sha256=_uuid.uuid4().hex.ljust(64, "f"),
                file_size_bytes=1,
                status="toc_ready",
            )
            s.add(book)
            await s.flush()
            toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
            s.add(toc)
            await s.flush()
            job = await jobs_repo.create(
                s,
                book_id=book.id,
                toc_entry_id=toc.id,
                subject="math-algebra",
                output_language="uz",
                transport="cli",
            )
            await s.commit()
            book_ids.append(book.id)
            return job.id

    yield make

    async with SessionLocal() as s:
        for bid in book_ids:
            job_ids = select(HomeworkJob.id).where(HomeworkJob.book_id == bid)
            await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_(job_ids)))
            await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        await s.commit()


@pytest.mark.asyncio
async def test_paused_worker_cannot_mutate_after_reclaim(seed_pending_job):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    from app.services.lease import JobLease

    job_id = await seed_pending_job()

    # ── A claims (token T1) ───────────────────────────────────────────────
    async with SessionLocal() as s:
        async with s.begin():
            claimed_a = await jobs_repo.claim_next_job(
                s, worker_id="A-worker:1@shaAAA", max_attempts=3
            )
    assert claimed_a is not None
    token_a = claimed_a.lease.claim_token
    lease_a = JobLease(job_id=job_id, claim_token=token_a, owner_id="A-worker:1@shaAAA")

    # ── force A's claim stale (past claimed_at; no live registry row for A) ─
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE homework_jobs SET claimed_at = now() - interval '1 hour' "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )

    # ── reclaim: job -> pending, token rotated off the row ────────────────
    async with SessionLocal() as s:
        async with s.begin():
            n = await jobs_repo.reclaim_stuck_jobs(s, stale_after_seconds=1)
    assert n >= 1
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status == "pending"
        assert job.claim_token is None

    # ── B re-claims (token T2) ────────────────────────────────────────────
    async with SessionLocal() as s:
        async with s.begin():
            claimed_b = await jobs_repo.claim_next_job(
                s, worker_id="B-worker:2@shaBBB", max_attempts=3
            )
    assert claimed_b is not None
    token_b = claimed_b.lease.claim_token
    assert token_b != token_a, "B must mint a fresh token, not inherit A's"

    # ── A "wakes up" and tries its worker-owned writes with the DEAD T1 ────
    # 1) the critical anti-double-completion write: mark done.
    async with SessionLocal() as s:
        async with s.begin():
            done_res = await jobs_repo.set_status(
                s, job_id, "done",
                completed_at=datetime.now(timezone.utc),
                claim_token=token_a,
            )
    assert done_res is lease.LeaseLost, "A must NOT be able to mark a reclaimed job done"

    # 2) A cannot even fail the job B now owns.
    async with SessionLocal() as s:
        async with s.begin():
            fail_res = await jobs_repo.mark_failed_with_retry(
                s, job_id, error_message="A stale failure", max_attempts=3,
                claim_token=token_a,
            )
    assert fail_res is lease.LeaseLost

    # 3) A cannot open/reset a phase row on the reclaimed job.
    async with SessionLocal() as s:
        async with s.begin():
            phase_res = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="preview", phase_order=1,
                prompt_hash="h", model_name="m", status="running", lease=lease_a,
            )
    assert phase_res is lease.LeaseLost

    # ── B still owns the job; it is NOT done, NOT failed, and carries T2 ──
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status == "running", f"expected B still running, got {job.status!r}"
        assert job.claim_token == token_b, "B's token must remain on the row"
        assert job.claimed_by == "B-worker:2@shaBBB"
        # no phase row was written by A
        rows = (
            await s.execute(
                select(phase_repo.PhaseOutput).where(
                    phase_repo.PhaseOutput.job_id == job_id
                )
            )
        ).scalars().all()
        assert rows == [], "A's stale create_or_reset must have written nothing"


# =============================================================================
# Task 10: remaining deterministic race-suite gaps (not covered by tasks 3-9).
# =============================================================================


@pytest.mark.asyncio
async def test_lease_events_double_append_is_idempotent(seed_pending_job):
    """Lease events are transactional and idempotent: two `append_event` calls
    carrying the identical (job_id, claim_token, event_type) key must insert
    exactly ONE row — `ON CONFLICT DO NOTHING` on
    `uq_job_lease_events_job_token_event` (app/repositories/lease_events.py).
    A retried sweep/write (e.g. a reclaim sweep that races another process's
    identical event) must never double-ledger the same transition."""
    from app.db import SessionLocal
    from app.models.job_lease_event import JobLeaseEvent
    from app.repositories import lease_events

    job_id = await seed_pending_job()
    token = _uuid.uuid4()

    async with SessionLocal() as s:
        async with s.begin():
            await lease_events.append_event(
                s, job_id=job_id, claim_token=token, event_type="claimed",
            )
    async with SessionLocal() as s:
        async with s.begin():
            # Same (job_id, claim_token, event_type) key again — must no-op,
            # not raise a unique-violation and not insert a second row.
            await lease_events.append_event(
                s, job_id=job_id, claim_token=token, event_type="claimed",
            )

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(JobLeaseEvent).where(
                    JobLeaseEvent.job_id == job_id,
                    JobLeaseEvent.claim_token == token,
                    JobLeaseEvent.event_type == "claimed",
                )
            )
        ).scalars().all()
    assert len(rows) == 1, f"expected exactly ONE ledgered row, got {len(rows)}"


@pytest.mark.asyncio
async def test_admin_cancel_paths_unaffected_by_fencing(seed_pending_job):
    """The operator/admin cancel surface (`cancel_if_pending` /
    `request_cancel`) is deliberately UNFENCED (no `claim_token` param) and
    must keep functioning exactly as before the fence landed:
      * `cancel_if_pending`: pending -> cancelled (job was never claimed, so
        no worker lease exists to fence against).
      * `request_cancel`: running -> cancelling, leaving the OWNING worker's
        claim_token on the row untouched — this call only signals; the
        owner's own fenced terminal write (or the heartbeat) finalizes it."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    # pending -> cancelled (never claimed).
    pending_job_id = await seed_pending_job()
    async with SessionLocal() as s:
        async with s.begin():
            ok = await jobs_repo.cancel_if_pending(s, pending_job_id)
    assert ok is True
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, pending_job_id)
        assert job.status == "cancelled"

    # running (claimed, live token) -> cancelling; the lease's token survives.
    running_job_id = await seed_pending_job()
    async with SessionLocal() as s:
        async with s.begin():
            claimed = await jobs_repo.claim_next_job(
                s, worker_id="cancel-admin-worker@shaCCC", max_attempts=3
            )
    assert claimed is not None and claimed.job.id == running_job_id, (
        "isolation leak — claimed an unrelated job instead of the seeded one"
    )
    async with SessionLocal() as s:
        async with s.begin():
            ok = await jobs_repo.request_cancel(s, running_job_id)
    assert ok is True
    async with SessionLocal() as s:
        job = await jobs_repo.get(s, running_job_id)
        assert job.status == "cancelling"
        assert job.claim_token == claimed.lease.claim_token, (
            "request_cancel is a pure signal — it must not touch claim_token"
        )


@pytest.mark.asyncio
async def test_completed_phases_survive_reclaim_not_regenerated(seed_pending_job):
    """A `done` phase row with real content must NOT be reset/regenerated by
    `reclaim_stuck_jobs` — only the job's UNFINISHED phase rows go back to
    `pending`. Mirrors production: a worker that died mid-packet must not
    lose (and force a paid re-generation of) the phases it already finished."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo

    job_id = await seed_pending_job()

    async with SessionLocal() as s:
        async with s.begin():
            claimed = await jobs_repo.claim_next_job(
                s, worker_id="phases-survive-worker@shaDDD", max_attempts=3
            )
    assert claimed is not None and claimed.job.id == job_id

    async with SessionLocal() as s:
        async with s.begin():
            done_row = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="preview", phase_order=1,
                prompt_hash="h1", model_name="m1", status="done",
            )
            await phase_repo.set_status(
                s, done_row.id, "done", output_md="# already generated",
                guard=False,
            )
            running_row = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="flashcards", phase_order=2,
                prompt_hash="h2", model_name="m1", status="running",
            )
    done_id, running_id = done_row.id, running_row.id

    # Force the claim stale, then reclaim (mirrors the anchor test above).
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text(
                    "UPDATE homework_jobs SET claimed_at = now() - interval '1 hour' "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )
    async with SessionLocal() as s:
        async with s.begin():
            n = await jobs_repo.reclaim_stuck_jobs(s, stale_after_seconds=1)
    assert n >= 1

    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status == "pending"
        rows = {r.id: r for r in await phase_repo.list_for_job(s, job_id)}
    assert rows[done_id].status == "done"
    assert rows[done_id].output_md == "# already generated", (
        "a done phase's content must survive reclaim untouched"
    )
    assert rows[running_id].status == "pending", (
        "an in-flight (running) phase must be reset so it re-runs under the new owner"
    )


@pytest.mark.asyncio
async def test_pipeline_run_done_write_fenced_end_to_end(seed_pending_job, monkeypatch):
    """Full-pipeline-DRIVEN variant of the anchor test above (deferred from
    Task 7): this drives the REAL `pipeline.run(job_id, lease)` — not just the
    repo primitive — through a mid-flight reclaim, and proves the anti-double-
    completion fence still bites at that layer.

    Every phase generator is stubbed (no model/PDF I/O — $0), but
    `jobs_repo`/`phase_repo` writes and the claim machinery are all real DB
    calls. Sequence: A claims (token T1) -> `pipeline.run` writes the
    `running` status fenced with T1 (still valid) -> inside the stubbed
    extract phase, a concurrent reclaim is simulated by rotating
    `claim_token` off the row (exactly what `reclaim_stuck_jobs` does) ->
    `pipeline.run` reaches its final anti-double-completion `done` write,
    still fenced with the now-DEAD T1 -> the write no-ops (`LeaseLost`) and
    `pipeline.run` raises `LeaseLostSignal` instead of completing. The job
    must never be marked `done` (or archived) under A's lease."""
    from pathlib import Path
    from unittest.mock import AsyncMock

    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo
    from app.services import pipeline
    from app.services.errors import LeaseLostSignal

    job_id = await seed_pending_job()

    # selected_phases matching NO real phase name empties out content_planned,
    # so pipeline.run's content-phase wave never launches — only the (stubbed)
    # extract head phase + the final done-write run, which is the point here.
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                update(HomeworkJob).where(HomeworkJob.id == job_id)
                .values(selected_phases=["__none__"])
            )

    # A claims (token T1).
    async with SessionLocal() as s:
        async with s.begin():
            claimed_a = await jobs_repo.claim_next_job(
                s, worker_id="A-worker:pipeline@shaAAA", max_attempts=3
            )
    assert claimed_a is not None and claimed_a.job.id == job_id
    lease_a = claimed_a.lease

    rotated_token = _uuid.uuid4()

    async def _stub_execute_one_phase(**kwargs):
        # Simulate a concurrent reclaim landing WHILE A is mid-phase: rotate
        # claim_token off the row directly (the brief's "pre-rotate
        # claim_token in the DB before the done-write" minimal-flow
        # allowance), standing in for a real reclaim_stuck_jobs sweep run by
        # a peer worker mid-flight.
        async with SessionLocal() as s2:
            async with s2.begin():
                await s2.execute(
                    update(HomeworkJob).where(HomeworkJob.id == job_id)
                    .values(claim_token=rotated_token, claimed_by="B-worker:pipeline@shaBBB")
                )
        return ("stub extract output", 1, 1, None)

    monkeypatch.setattr(pipeline, "_execute_one_phase", _stub_execute_one_phase)
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf_sync",
        lambda *a, **k: Path("/fake/book.pdf"),
    )
    publish_spy = AsyncMock()
    close_spy = AsyncMock()
    monkeypatch.setattr(pipeline.events_bus, "publish", publish_spy)
    monkeypatch.setattr(pipeline.events_bus, "close", close_spy)
    archive_spy = AsyncMock()
    monkeypatch.setattr(pipeline.notion_archive, "archive_job", archive_spy)

    with pytest.raises(LeaseLostSignal):
        await pipeline.run(job_id, lease_a)

    # The completion side-effects must never fire — the fence bit BEFORE them.
    completed_events = [
        c for c in publish_spy.await_args_list
        if len(c.args) > 1 and c.args[1] == "job_completed"
    ]
    assert completed_events == [], (
        f"job_completed must not publish on LeaseLost (got {completed_events})"
    )
    archive_spy.assert_not_awaited()
    close_spy.assert_awaited()  # the SSE bus is still closed on the way out

    async with SessionLocal() as s:
        job = await jobs_repo.get(s, job_id)
        assert job.status != "done", (
            f"A must not mark the reclaimed job done via the real pipeline.run, "
            f"got {job.status!r}"
        )
        assert job.claim_token == rotated_token, "B's rotated token must remain on the row"
