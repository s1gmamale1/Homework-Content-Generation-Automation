"""Every terminal revision job must move its target — from every exit path.

A revision job's status is job truth; a target's status is campaign truth. They
are written by DIFFERENT transactions, so anything that can end a job has to be
followed by reconciliation or a finished revision sits in `generating` forever
and the campaign never converges. The exits are not one code path: the pipeline
can return a hard failure, the worker can crash, a cancel can win a race inside
`SessionLimitPause`/`SlotSaturation`, a lease can be lost, an API cancel can
finalize a pending job without a worker at all, and two sweeps
(`reclaim_stale_cancelling`, `fail_exhausted_pending_jobs`) write terminal
statuses with no worker in sight.

So there are two layers of proof here:

1. the mapping itself, against a real Postgres (job status + snapshot +
   campaign approval + abandonment request -> target status), including the
   crash-repair bulk sweep that covers the sweeps and any lost update;
2. the worker/API wiring, in-process: reconciliation is the LAST thing that
   happens, after the heartbeat has settled and the semaphore permit is back,
   it never runs for an ordinary job, and it can neither leak a resource nor
   escape as a new failure when a second shutdown cancellation lands on it.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import regeneration_job_state
from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_PHASE_PLAN = build_phase_plan(
    subject=_SUBJECT, selected_phases=["flashcards"]).to_json()

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ═════════════════════════════════════════════════════════════════════════
# 1. the mapping, against a real Postgres
# ═════════════════════════════════════════════════════════════════════════


async def _seed(
    *,
    job_status: str = "done",
    target_status: str = "generating",
    complete: bool = True,
    approved: bool = False,
    campaign_status: str = "canary_running",
    abandon_requested: bool = False,
):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT, original_filename="regen_reconcile.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready")
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        session.add(toc)
        await session.flush()
        source = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="gemini", transport="api",
            output_language="uz")
        session.add(source)
        await session.flush()
        campaign = RegenerationCampaign(
            status=campaign_status, selection_spec={}, requested_phases=[],
            excluded_phases=[], launch_contract={},
            approved_at=now if approved else None)
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id, output_language="uz",
            phase_plan=_PHASE_PLAN, source_job_id=source.id,
            status=target_status,
            abandon_requested_at=now if abandon_requested else None,
            abandon_requested_reason="operator" if abandon_requested else None)
        session.add(target)
        await session.flush()
        revision = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status=job_status, provider="gemini", transport="api",
            output_language="uz", revision_of_job_id=source.id,
            regeneration_target_id=target.id, session_limit_strategy="pause")
        session.add(revision)
        await session.flush()
        phases = _CANONICAL if complete else _CANONICAL[:-1]
        for order, name in enumerate(phases):
            session.add(PhaseOutput(
                job_id=revision.id, phase_name=name, phase_order=order,
                prompt_hash=f"builtin:{name}:v9", model_name="gemini-3.5-flash",
                provider="gemini", output_md=f"# {name}", status="done"))
        await session.commit()
        return {
            "book_id": book.id, "toc_id": toc.id, "source_id": source.id,
            "campaign_id": campaign.id, "target_id": target.id,
            "revision_id": revision.id,
        }


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        await session.execute(delete(AgentUsage).where(
            AgentUsage.homework_job_id.in_([ids["revision_id"], ids["source_id"]])))
        await session.execute(delete(PhaseOutput).where(
            PhaseOutput.job_id.in_([ids["revision_id"], ids["source_id"]])))
        await session.execute(delete(HomeworkJob).where(
            HomeworkJob.id == ids["revision_id"]))
        await session.execute(delete(RegenerationTarget).where(
            RegenerationTarget.id == ids["target_id"]))
        await session.execute(delete(RegenerationCampaign).where(
            RegenerationCampaign.id == ids["campaign_id"]))
        await session.execute(delete(HomeworkJob).where(
            HomeworkJob.book_id == ids["book_id"]))
        await session.execute(delete(TOCEntry).where(
            TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


async def _reconcile(ids):
    from app.db import SessionLocal

    async with SessionLocal() as session:
        await regeneration_job_state.reconcile_revision_job(
            session, ids["revision_id"])
        await session.commit()


async def _target(ids):
    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return await session.get(RegenerationTarget, ids["target_id"])


@db_only
@pytest.mark.parametrize("job_status", ["pending", "running", "cancelling"])
async def test_a_live_revision_leaves_its_target_generating(job_status):
    ids = await _seed(job_status=job_status, target_status="planned")
    try:
        await _reconcile(ids)
        assert (await _target(ids)).status == "generating"
    finally:
        await _purge(ids)


@db_only
async def test_done_with_a_complete_snapshot_before_approval_holds_the_canary():
    ids = await _seed(job_status="done", approved=False)
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "awaiting_canary_approval"
        assert target.publication_released_at is None, (
            "no publication may be released before the operator approves")
        assert target.terminal_at is None
    finally:
        await _purge(ids)


@db_only
async def test_done_with_a_complete_snapshot_after_approval_releases_publication():
    ids = await _seed(
        job_status="done", approved=True, campaign_status="bulk_running")
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "publication_pending"
        assert target.publication_released_at is not None, (
            "ck_regeneration_targets_publication_released demands the stamp")
        assert target.terminal_at is None, "publication_pending is not terminal"
    finally:
        await _purge(ids)


@db_only
async def test_done_with_an_INCOMPLETE_snapshot_is_never_publishable():
    """A `done` job missing a canonical phase is a generation failure, not a
    publication: publishing it would deliver a packet with a hole in it."""
    ids = await _seed(job_status="done", complete=False, approved=True)
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "generation_failed"
        assert target.publication_released_at is None
    finally:
        await _purge(ids)


@db_only
async def test_done_with_a_phase_that_has_no_content_is_never_publishable():
    """`validate_complete_snapshot`'s structured-content rule, not a re-derived
    one: a `done` row with neither `output_md` nor `content_json` is unusable."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput

    ids = await _seed(job_status="done", approved=True)
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(PhaseOutput)
                .where(PhaseOutput.job_id == ids["revision_id"])
                .where(PhaseOutput.phase_name == "reflection")
                .values(output_md="   ", content_json=None))
            await session.commit()
        await _reconcile(ids)
        assert (await _target(ids)).status == "generation_failed"
    finally:
        await _purge(ids)


@db_only
@pytest.mark.parametrize("job_status", ["failed", "cancelled"])
async def test_a_terminal_failure_marks_the_target_generation_failed(job_status):
    ids = await _seed(job_status=job_status)
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "generation_failed"
        assert target.terminal_at is None, (
            "generation_failed is RETRYABLE — it must not free the lineage")
    finally:
        await _purge(ids)


@db_only
@pytest.mark.parametrize("job_status", ["failed", "cancelled"])
async def test_an_abandon_request_converges_a_terminal_failure_to_abandoned(
    job_status,
):
    ids = await _seed(job_status=job_status, abandon_requested=True)
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "abandoned"
        assert target.terminal_at is not None, (
            "abandoned is terminal — the stamp is what frees the lineage")
    finally:
        await _purge(ids)


@db_only
@pytest.mark.parametrize("approved", [False, True])
async def test_an_abandon_request_beats_a_done_and_complete_revision(approved):
    """Abandonment is decided BEFORE publication, not after.

    The normal way an abandon lands is on a job that is still in flight: the
    operator asks, and the revision commits `done` before the abandon-driven
    cancel takes effect. If the mapping looks at `done` + complete first, an
    approved campaign releases that target for publication — and publication is
    irreversible (a public Notion page and a permanently consumed version
    number). Any terminal job whose target carries an abandon request is
    abandoned, whatever the job's own verdict was.
    """
    ids = await _seed(
        job_status="done", complete=True, abandon_requested=True,
        approved=approved,
        campaign_status="bulk_running" if approved else "canary_running")
    try:
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "abandoned"
        assert target.terminal_at is not None, (
            "abandoned is terminal — the stamp is what frees the lineage")
        assert target.publication_released_at is None, (
            "an abandoned target must never be released to the publisher")
        assert target.terminal_reason == "operator", (
            "the operator's own abandon reason is what the lineage records")
    finally:
        await _purge(ids)


@db_only
async def test_reconciliation_is_idempotent():
    ids = await _seed(job_status="done", approved=True)
    try:
        await _reconcile(ids)
        first = await _target(ids)
        await _reconcile(ids)
        second = await _target(ids)
        assert second.status == first.status == "publication_pending"
        assert second.publication_released_at == first.publication_released_at, (
            "a repeat must not re-release the publication")
    finally:
        await _purge(ids)


@db_only
async def test_a_target_already_publishing_is_never_dragged_backwards():
    """The publisher owns the row once it is `publishing`; a late reconcile of
    the same `done` job must not reset it to `publication_pending` and hand the
    same delivery to a second publisher."""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    ids = await _seed(job_status="done", approved=True)
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_id"])
                .values(status="publishing",
                        publication_released_at=datetime.now(timezone.utc),
                        publication_claim_token=uuid.uuid4()))
            await session.commit()
        await _reconcile(ids)
        assert (await _target(ids)).status == "publishing"
    finally:
        await _purge(ids)


@db_only
async def test_a_terminal_target_is_never_touched():
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    now = datetime.now(timezone.utc)
    ids = await _seed(job_status="failed", approved=True)
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == ids["target_id"])
                .values(status="abandoned", terminal_at=now,
                        terminal_reason="operator"))
            await session.commit()
        await _reconcile(ids)
        target = await _target(ids)
        assert target.status == "abandoned"
        assert target.terminal_reason == "operator"
    finally:
        await _purge(ids)


@db_only
async def test_reconciling_an_ORDINARY_job_is_a_no_op():
    ids = await _seed(job_status="done")
    try:
        from app.db import SessionLocal

        async with SessionLocal() as session:
            await regeneration_job_state.reconcile_revision_job(
                session, ids["source_id"])
            await session.commit()
        assert (await _target(ids)).status == "generating"
    finally:
        await _purge(ids)


@db_only
async def test_reconciling_a_missing_job_is_a_no_op():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        await regeneration_job_state.reconcile_revision_job(session, uuid.uuid4())
        await session.commit()


# ── the crash-repair bulk sweep ──────────────────────────────────────────


@db_only
async def test_bulk_sweep_repairs_a_crash_between_job_commit_and_target_update():
    from app.db import SessionLocal

    ids = await _seed(job_status="done", approved=True)
    other = await _seed(job_status="failed")
    try:
        async with SessionLocal() as session:
            moved = await regeneration_job_state.reconcile_terminal_revision_jobs(
                session)
        assert moved >= 2
        assert (await _target(ids)).status == "publication_pending"
        assert (await _target(other)).status == "generation_failed"
    finally:
        await _purge(ids)
        await _purge(other)


@db_only
async def test_bulk_sweep_leaves_live_jobs_and_settled_targets_alone():
    from app.db import SessionLocal

    live = await _seed(job_status="running")
    try:
        async with SessionLocal() as session:
            moved = await regeneration_job_state.reconcile_terminal_revision_jobs(
                session)
        assert moved == 0
        assert (await _target(live)).status == "generating"
    finally:
        await _purge(live)


@db_only
async def test_stale_cancelling_sweep_leaves_no_target_generating():
    """`reclaim_stale_cancelling` writes `cancelled` with no worker involved."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    ids = await _seed(job_status="cancelling")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(HomeworkJob).where(HomeworkJob.id == ids["revision_id"])
                .values(claimed_at=__import__("sqlalchemy").func.now()
                        - __import__("sqlalchemy").func.make_interval(
                            0, 0, 0, 0, 0, 0, 7200)))
            n = await jobs_repo.reclaim_stale_cancelling(session, 60)
            await session.commit()
        assert n >= 1
        async with SessionLocal() as session:
            await regeneration_job_state.reconcile_terminal_revision_jobs(session)
        assert (await _target(ids)).status == "generation_failed"
    finally:
        await _purge(ids)


@db_only
async def test_exhausted_pending_sweep_leaves_no_target_generating():
    """`fail_exhausted_pending_jobs` writes `failed` with no worker involved."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    ids = await _seed(job_status="pending")
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(HomeworkJob).where(HomeworkJob.id == ids["revision_id"])
                .values(attempts=9))
            n = await jobs_repo.fail_exhausted_pending_jobs(session, max_attempts=3)
            await session.commit()
        assert n >= 1
        async with SessionLocal() as session:
            await regeneration_job_state.reconcile_terminal_revision_jobs(session)
        assert (await _target(ids)).status == "generation_failed"
    finally:
        await _purge(ids)


@db_only
async def test_no_active_lineage_lock_is_orphaned_by_a_terminal_failure():
    """A generation failure must NOT free the lineage — a competing campaign
    stays blocked until the operator retries or explicitly abandons."""
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget

    ids = await _seed(job_status="failed")
    rival_id = None
    try:
        await _reconcile(ids)
        async with SessionLocal() as session:
            rival = RegenerationCampaign(
                status="draft", selection_spec={}, requested_phases=[],
                excluded_phases=[], launch_contract={})
            session.add(rival)
            await session.flush()
            rival_id = rival.id
            session.add(RegenerationTarget(
                campaign_id=rival.id, toc_entry_id=ids["toc_id"],
                output_language="uz", phase_plan=_PHASE_PLAN,
                source_job_id=ids["source_id"], status="planned"))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
    finally:
        if rival_id is not None:
            from sqlalchemy import delete

            async with SessionLocal() as session:
                await session.execute(delete(RegenerationCampaign).where(
                    RegenerationCampaign.id == rival_id))
                await session.commit()
        await _purge(ids)


# ═════════════════════════════════════════════════════════════════════════
# 2. the worker / API wiring (in-process, no DB)
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def wharness(monkeypatch):
    """A Worker whose reconciliation is a spy that snapshots resource state.

    The spy records what the world looked like AT THE MOMENT reconciliation
    ran, which is the only way to prove the ordering requirement: the heartbeat
    must already be settled and the semaphore permit already back.
    """
    from app.services import worker as worker_mod

    class H:
        pass

    h = H()
    h.job_id = uuid.uuid4()
    h.calls: list[dict] = []
    h.beats: list[asyncio.Task] = []
    h.sessions = 0
    h.gate: asyncio.Event | None = None

    w = worker_mod.Worker(
        concurrency=1, poll_interval=0.01, job_timeout_seconds=5, max_attempts=3)
    h.worker = w

    async def _fake_heartbeat(job_id, lease=None):
        h.beats.append(asyncio.current_task())
        while True:
            await asyncio.sleep(3600)

    monkeypatch.setattr(w, "_heartbeat", _fake_heartbeat)

    async def _spy(session, job_id):
        h.calls.append({
            "job_id": job_id,
            "slots": w._slots._value,
            "running_jobs": dict(worker_mod.RUNNING_JOBS),
            "beats_done": [t.done() for t in h.beats],
        })
        if h.gate is not None:
            await h.gate.wait()

    monkeypatch.setattr(
        worker_mod.regeneration_job_state, "reconcile_revision_job", _spy)

    def _session_factory():
        h.sessions += 1
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    monkeypatch.setattr(
        worker_mod, "SessionLocal", MagicMock(side_effect=_session_factory))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "get_status", AsyncMock(return_value="done"))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "mark_failed_with_retry",
        AsyncMock(return_value="failed"))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "requeue_session_limited", AsyncMock(return_value="pending"))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "requeue_slot_saturated", AsyncMock(return_value="pending"))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "mark_cancelled", AsyncMock(return_value=None))
    return h


async def _run_exit(h, monkeypatch, run_impl, *, is_revision=True):
    from app.services import worker as worker_mod

    monkeypatch.setattr(worker_mod.pipeline, "run", run_impl)
    await h.worker._slots.acquire()
    await h.worker._execute_job(h.job_id, is_revision=is_revision)


async def _ok(job_id, lease=None):
    return None


async def test_a_normal_pipeline_return_reconciles_last(wharness, monkeypatch):
    """`pipeline.run` RETURNING — the shape both a success and a hard-return
    failure take (on a hard phase failure the pipeline marks the job failed
    itself and returns, so the worker sees the identical control flow).

    The two are distinguished where the difference actually lives: the real
    end-to-end run in `tests/services/test_regeneration_pipeline.py`
    (`test_a_hard_phase_failure_fails_the_job_and_the_target`).
    """
    await _run_exit(wharness, monkeypatch, _ok)
    assert len(wharness.calls) == 1
    assert wharness.calls[0]["job_id"] == wharness.job_id


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: RuntimeError("worker crash"),
        lambda: __import__(
            "app.services.errors", fromlist=["x"]).CancelWonSignal(),
        lambda: __import__(
            "app.services.errors", fromlist=["x"]).LeaseLostSignal(),
        lambda: __import__(
            "app.services.errors", fromlist=["x"]).SessionLimitPause("limit"),
        lambda: __import__(
            "app.services.errors", fromlist=["x"]).SlotSaturation("saturated"),
        lambda: asyncio.TimeoutError(),
    ],
)
async def test_every_worker_exit_path_reconciles_exactly_once(
    wharness, monkeypatch, exc_factory
):
    async def _boom(job_id, lease=None):
        raise exc_factory()

    await _run_exit(wharness, monkeypatch, _boom)
    assert len(wharness.calls) == 1, (
        "a revision that ended on this path left its target `generating`")
    # Same cleanup contract on EVERY exit path, not just the happy one: the
    # requeue branches (SessionLimitPause / SlotSaturation) are the ones whose
    # `_finalize_if_cancelling` can finalize a concurrent cancellation, and they
    # must not leak a permit or a heartbeat on the way out either.
    snapshot = wharness.calls[0]
    assert snapshot["slots"] == 1
    assert all(snapshot["beats_done"])
    assert wharness.job_id not in snapshot["running_jobs"]


async def test_running_cancel_finalization_reconciles(wharness, monkeypatch):
    """A user cancel of a RUNNING job: the task is cancelled, the worker
    finalizes `cancelled`, and the target must follow."""
    from app.services import worker as worker_mod

    monkeypatch.setattr(
        worker_mod.jobs_repo, "get_status", AsyncMock(return_value="cancelling"))

    async def _cancelled(job_id, lease=None):
        raise asyncio.CancelledError()

    await _run_exit(wharness, monkeypatch, _cancelled)
    assert len(wharness.calls) == 1


async def test_shutdown_cancel_still_reconciles_and_reraises(wharness, monkeypatch):
    """A SHUTDOWN cancel re-raises (the row is left for reclaim) — but the
    reconciliation still has to run on the way out."""
    from app.services import worker as worker_mod

    monkeypatch.setattr(
        worker_mod.jobs_repo, "get_status", AsyncMock(return_value="running"))

    async def _cancelled(job_id, lease=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker_mod.pipeline, "run", _cancelled)
    await wharness.worker._slots.acquire()
    with pytest.raises(asyncio.CancelledError):
        await wharness.worker._execute_job(wharness.job_id, is_revision=True)
    assert len(wharness.calls) == 1


async def test_reconciliation_runs_after_the_heartbeat_and_the_slot(wharness, monkeypatch):
    await _run_exit(wharness, monkeypatch, _ok)
    snapshot = wharness.calls[0]
    assert snapshot["slots"] == 1, (
        "the semaphore permit must be back BEFORE reconciliation — otherwise a "
        "slow/hanging reconcile silently costs the worker a concurrency slot")
    assert all(snapshot["beats_done"]), (
        "the heartbeat must be cancelled AND settled before reconciliation")
    assert wharness.job_id not in snapshot["running_jobs"]


async def test_an_ordinary_job_opens_no_reconciliation_session(wharness, monkeypatch):
    before = wharness.sessions
    await _run_exit(wharness, monkeypatch, _ok, is_revision=False)
    assert wharness.calls == []
    assert wharness.sessions == before, (
        "an ordinary job must short-circuit on the claim-time marker without "
        "opening a session")


async def test_a_reconciliation_failure_never_escapes_or_skips_cleanup(
    wharness, monkeypatch
):
    from app.services import worker as worker_mod

    async def _explode(session, job_id):
        raise RuntimeError("regeneration table unavailable")

    monkeypatch.setattr(
        worker_mod.regeneration_job_state, "reconcile_revision_job", _explode)
    await _run_exit(wharness, monkeypatch, _ok)  # must not raise
    assert wharness.worker._slots._value == 1
    assert all(t.done() for t in wharness.beats)


async def test_a_second_shutdown_cancel_during_reconciliation_leaks_nothing(
    wharness, monkeypatch
):
    """The nastiest shape: the task is cancelled AGAIN while reconciliation is
    in flight. Cleanup already happened, so nothing may leak — and the
    cancellation must not surface as a new job failure."""
    from app.services import worker as worker_mod

    wharness.gate = asyncio.Event()
    monkeypatch.setattr(worker_mod.pipeline, "run", _ok)
    await wharness.worker._slots.acquire()
    task = asyncio.create_task(
        wharness.worker._execute_job(wharness.job_id, is_revision=True))
    for _ in range(200):
        await asyncio.sleep(0)
        if wharness.calls:
            break
    assert wharness.calls, "reconciliation never started"
    task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
    assert wharness.worker._slots._value == 1, "semaphore permit leaked"
    assert all(t.done() for t in wharness.beats), "heartbeat leaked"
    wharness.gate.set()
    await asyncio.sleep(0)


async def test_claim_stashes_the_revision_marker(monkeypatch):
    """The marker comes from the CLAIMED row, beside the lease handoff, so an
    ordinary job never has to open a session to find out it is ordinary."""
    from app.services import lease as lease_mod
    from app.services import worker as worker_mod

    w = worker_mod.Worker(concurrency=1, poll_interval=0.01)
    job_id = uuid.uuid4()
    job = MagicMock()
    job.id = job_id
    job.attempts = 1
    job.priority = 0
    job.revision_of_job_id = uuid.uuid4()

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=begin)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    monkeypatch.setattr(worker_mod, "SessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(
        worker_mod.workers_repo, "lock_host_shared", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod.sa_keys_repo, "scrub_pending_for_host", AsyncMock(return_value=False))
    monkeypatch.setattr(
        worker_mod.budget_repo, "get_state",
        AsyncMock(return_value=MagicMock(api_paused_at=None, min_worker_version=None)))
    monkeypatch.setattr(
        worker_mod.jobs_repo, "claim_next_job",
        AsyncMock(return_value=lease_mod.ClaimedJob(
            job=job,
            lease=lease_mod.JobLease(
                job_id=job_id, claim_token=uuid.uuid4(), owner_id="w"))))

    assert await w._claim_one() == job_id
    assert w._revisions.get(job_id) is True

    job.revision_of_job_id = None
    assert await w._claim_one() == job_id
    assert w._revisions.get(job_id) is False


# ── the maintenance / startup wiring ─────────────────────────────────────


async def test_worker_maintenance_sweep_is_its_own_session_and_guard(monkeypatch):
    from app.services import worker as worker_mod

    w = worker_mod.Worker(concurrency=1)
    calls = []

    async def _explode(session):
        calls.append(session)
        raise RuntimeError("regeneration migration not applied yet")

    monkeypatch.setattr(
        worker_mod.regeneration_job_state, "reconcile_terminal_revision_jobs",
        _explode)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(worker_mod, "SessionLocal", MagicMock(return_value=session))

    await w._sweep_revision_targets()  # must not raise
    assert len(calls) == 1


def test_stuck_job_sweep_does_not_share_a_transaction_with_reconciliation():
    """`fail_exhausted_pending_jobs` / `reclaim_stale_cancelling` / the worker
    registry maintenance must not be able to be rolled back by a regeneration
    hiccup, so the revision sweep is a SEPARATE method with a separate session."""
    import inspect

    from app.services import worker as worker_mod

    stuck = inspect.getsource(worker_mod.Worker._sweep_stuck_jobs)
    assert "reconcile_terminal_revision_jobs" not in stuck
    revision = inspect.getsource(worker_mod.Worker._sweep_revision_targets)
    assert "SessionLocal" in revision
    assert "fail_exhausted_pending_jobs" not in revision
    loop = inspect.getsource(worker_mod.Worker.run)
    assert "_sweep_revision_targets()" in loop


async def test_startup_reconciliation_is_a_separate_guarded_step(monkeypatch):
    import main as main_mod

    async def _explode(session):
        raise RuntimeError("regeneration table missing")

    monkeypatch.setattr(
        main_mod.regeneration_job_state, "reconcile_terminal_revision_jobs",
        _explode)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(main_mod, "SessionLocal", MagicMock(return_value=session))

    await main_mod._reconcile_revision_targets_on_startup()  # must not raise

    import inspect

    critical = inspect.getsource(main_mod._reconcile_on_startup)
    assert "reconcile_terminal_revision_jobs" not in critical, (
        "a regeneration hiccup must not be able to roll back the critical "
        "startup job/book reconcile"
    )
    assert "_reconcile_revision_targets_on_startup()" in inspect.getsource(
        main_mod.lifespan)


async def test_api_cancel_of_a_pending_revision_reconciles(monkeypatch):
    from app.api.v1 import jobs as jobs_api

    calls = []

    async def _spy(session, job_id):
        calls.append(job_id)

    monkeypatch.setattr(
        jobs_api.regeneration_job_state, "reconcile_revision_job", _spy)
    job_id = uuid.uuid4()
    job = MagicMock()
    job.id = job_id
    job.revision_of_job_id = uuid.uuid4()
    job.status = "cancelled"
    monkeypatch.setattr(
        jobs_api.jobs_repo, "cancel_if_pending", AsyncMock(return_value=True))
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    monkeypatch.setattr(jobs_api.JobOut, "model_validate", lambda j: j)

    session = AsyncMock()
    await jobs_api.cancel_job(job_id, session=session, user={})
    assert calls == [job_id]


async def test_api_cancel_of_an_ORDINARY_pending_job_does_not_reconcile(monkeypatch):
    from app.api.v1 import jobs as jobs_api

    calls = []

    async def _spy(session, job_id):
        calls.append(job_id)

    monkeypatch.setattr(
        jobs_api.regeneration_job_state, "reconcile_revision_job", _spy)
    job_id = uuid.uuid4()
    job = MagicMock()
    job.id = job_id
    job.revision_of_job_id = None
    monkeypatch.setattr(
        jobs_api.jobs_repo, "cancel_if_pending", AsyncMock(return_value=True))
    monkeypatch.setattr(jobs_api.jobs_repo, "get", AsyncMock(return_value=job))
    monkeypatch.setattr(jobs_api.JobOut, "model_validate", lambda j: j)

    await jobs_api.cancel_job(job_id, session=AsyncMock(), user={})
    assert calls == []
