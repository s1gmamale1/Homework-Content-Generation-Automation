"""Real-DB: claim_next_job mints a per-claim token and records the `claimed`
ledger event in the same transaction (fenced job leases, Task 3).

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)

# Credential-only capability shape (`worker._compute_capabilities`). The
# seeded job below is transport='cli' throughout (content/judge/extract/
# solver all default cli/inherit), so the claim gate's api-capability arms
# never fire either way — kept True to prove they aren't what's gating.
ANY_CAPS = {
    "can_claude_api": True,
    "can_gemini_api": True,
    "can_clodex_api": True,
}


# ---------------------------------------------------------------------------
# db_session fixture — provides a real AsyncSession for each test, rolling
# back after each test to keep tests isolated. Mirrors the idiom in
# tests/repositories/test_launch_defaults.py / test_migration_0052_lease.py.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# seed_pending_job fixture — a single claimable pending job with valid FKs
# (book + toc_entry), mirroring tests/integration/test_clock_skew.py's
# _seed_section helper. Seeded + committed in ITS OWN session (not
# db_session) and cleaned up afterward — mirrors production: the worker's
# claiming session never already holds the job in its identity map, so
# `claim_next_job`'s post-UPDATE `session.get()` reads the fresh row rather
# than a stale in-memory copy from before the UPDATE.
# ---------------------------------------------------------------------------

@pytest.fixture
async def seed_pending_job():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo
    from sqlalchemy import delete

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="lease-fencing.pdf",
            content_sha256="2" * 64,
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
        )
        await s.commit()
        book_id, toc_id, job_id = book.id, toc.id, job.id

    yield job

    async with SessionLocal() as s:
        from app.models.job_lease_event import JobLeaseEvent

        await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id == job_id))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_claim_mints_token_and_records_event(db_session, seed_pending_job):
    from app.repositories import jobs as jobs_repo
    from app.models.job_lease_event import JobLeaseEvent

    claimed = await jobs_repo.claim_next_job(
        db_session, worker_id="h:1@sha", capabilities=ANY_CAPS, max_attempts=5
    )

    assert claimed is not None
    assert claimed.lease.claim_token is not None
    assert claimed.job.claim_token == claimed.lease.claim_token
    assert claimed.job.id == seed_pending_job.id
    assert claimed.lease.job_id == seed_pending_job.id
    assert claimed.lease.owner_id == "h:1@sha"

    ev = (
        await db_session.execute(
            select(JobLeaseEvent).where(JobLeaseEvent.job_id == claimed.job.id)
        )
    ).scalars().all()
    assert any(
        e.event_type == "claimed" and e.claim_token == claimed.lease.claim_token
        for e in ev
    )

    # Commit (rather than letting the db_session fixture roll back) so the
    # claim's row lock on homework_jobs is released before seed_pending_job's
    # teardown tries to DELETE that same row — an open db_session rollback
    # racing an in-flight DELETE from a second connection self-deadlocks.
    await db_session.commit()


# ---------------------------------------------------------------------------
# Task 4: reclaim/requeue rotate the token + registry-liveness cross-check
# ---------------------------------------------------------------------------


async def _reload(session, job_id):
    """Re-read a job as a fresh row (bypass the identity map)."""
    from app.models.homework_job import HomeworkJob

    return (
        await session.execute(
            select(HomeworkJob)
            .where(HomeworkJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _has_event(session, job_id, event_type) -> bool:
    from app.models.job_lease_event import JobLeaseEvent

    rows = (
        await session.execute(
            select(JobLeaseEvent).where(
                JobLeaseEvent.job_id == job_id,
                JobLeaseEvent.event_type == event_type,
            )
        )
    ).scalars().all()
    return len(rows) > 0


async def _has_event_with_token(session, job_id, event_type, token) -> bool:
    from app.models.job_lease_event import JobLeaseEvent

    rows = (
        await session.execute(
            select(JobLeaseEvent).where(
                JobLeaseEvent.job_id == job_id,
                JobLeaseEvent.event_type == event_type,
            )
        )
    ).scalars().all()
    return any(r.claim_token == token for r in rows)


@pytest.fixture
async def lease_reclaim_factory():
    """Factory: build a committed `running` job with a stamped claim_token and
    back-dated claimed_at/started_at, optionally with a live `workers` registry
    row for its owner. Yields ``make(...)`` returning the job (with a fresh
    ``.claim_token`` uuid). Cleans up jobs, events, workers, toc, and books.
    """
    import uuid as _uuid

    from sqlalchemy import delete, text
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.toc_entry import TOCEntry
    from app.models.worker import WorkerNode
    from app.repositories import jobs as jobs_repo
    from app.repositories import workers as workers_repo

    book_ids: list = []
    worker_pc_ids: list = []

    async def make(
        *,
        claimed_by: str,
        claimed_at_age_seconds: int,
        started_at_age_seconds: int,
        live_owner: bool = False,
    ):
        token = _uuid.uuid4()
        async with SessionLocal() as s:
            if live_owner:
                # Fresh heartbeat (func.now()) for this owner pc_id.
                await workers_repo.upsert_heartbeat(s, claimed_by)
                worker_pc_ids.append(claimed_by)

            book = Book(
                subject="math-algebra",
                original_filename="lease-reclaim.pdf",
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
            )
            # Force running + back-dated claim/start + a stamped token, all in
            # the DB clock so the reclaim predicates compare like-for-like.
            await s.execute(
                text(
                    "UPDATE homework_jobs SET status='running', "
                    "claimed_by=:cb, claim_token=:tok, "
                    "claimed_at = now() - make_interval(secs => :ca), "
                    "started_at = now() - make_interval(secs => :sa) "
                    "WHERE id=:id"
                ),
                {
                    "cb": claimed_by,
                    "tok": token,
                    "ca": claimed_at_age_seconds,
                    "sa": started_at_age_seconds,
                    "id": job.id,
                },
            )
            await s.commit()
            book_ids.append(book.id)
            # Re-read bypassing the identity map — the raw SQL UPDATE above
            # bypassed the ORM, so `s.get` would return the stale cached row
            # (claim_token=None). populate_existing forces a fresh load.
            return (
                await s.execute(
                    select(HomeworkJob)
                    .where(HomeworkJob.id == job.id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()

    yield make

    async with SessionLocal() as s:
        for bid in book_ids:
            await s.execute(
                delete(JobLeaseEvent).where(
                    JobLeaseEvent.job_id.in_(
                        select(HomeworkJob.id).where(HomeworkJob.book_id == bid)
                    )
                )
            )
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        for pc in worker_pc_ids:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
        await s.commit()


async def test_reclaim_clears_token_and_records_event_with_OLD_token(
    db_session, lease_reclaim_factory
):
    """Normal reclaim: owner absent from the registry, claimed_at stale,
    started_at recent (not past the hard deadline). The token is rotated to
    NULL and the `reclaimed_stale` event carries the OLD (pre-reclaim) token."""
    from app.repositories import jobs as jobs_repo

    job = await lease_reclaim_factory(
        claimed_by="dead-worker:1@sha",
        claimed_at_age_seconds=9999,   # stale
        started_at_age_seconds=10,     # nowhere near the hard deadline
        live_owner=False,              # no workers row → owner absent
    )
    old_token = job.claim_token
    assert old_token is not None

    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n >= 1

    reloaded = await _reload(db_session, job.id)
    assert reloaded.status == "pending"
    assert reloaded.claim_token is None
    assert reloaded.claimed_at is None and reloaded.claimed_by is None
    # The event must carry the OLD token, not the NULL post-update value.
    assert await _has_event_with_token(
        db_session, job.id, "reclaimed_stale", old_token
    )

    await db_session.commit()


async def test_fresh_registry_owner_blocks_normal_reclaim(
    db_session, lease_reclaim_factory
):
    """claimed_at is stale, BUT the owning pc_id still heartbeats the workers
    registry → normal reclaim is blocked, the job stays running and keeps its
    token."""
    from app.repositories import jobs as jobs_repo

    job = await lease_reclaim_factory(
        claimed_by="live-worker:1@sha",
        claimed_at_age_seconds=9999,   # stale claim
        started_at_age_seconds=30,     # not past the hard deadline
        live_owner=True,               # fresh workers heartbeat for this owner
    )
    old_token = job.claim_token

    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)

    reloaded = await _reload(db_session, job.id)
    assert reloaded.status == "running", "live owner must block normal reclaim"
    assert reloaded.claim_token == old_token, "token must be untouched"
    assert not await _has_event(db_session, job.id, "reclaimed_stale")
    assert not await _has_event(db_session, job.id, "reclaimed_forced")
    del n  # count may include unrelated rows only if others exist; state is authoritative

    await db_session.commit()


async def test_hard_deadline_forces_reclaim_despite_live_owner(
    db_session, lease_reclaim_factory
):
    """Past the hard deadline (started_at older than job_timeout + stale), a
    live owner no longer protects the job — forced reclaim fires, rotates the
    token, and records `reclaimed_forced` with the OLD token."""
    from app.config import settings
    from app.repositories import jobs as jobs_repo

    # started_at older than job_timeout_seconds (1800) + stale (120) with margin.
    started_age = settings.job_timeout_seconds + 120 + 300
    job = await lease_reclaim_factory(
        claimed_by="wedged-worker:1@sha",
        claimed_at_age_seconds=5,      # claim looks fresh…
        started_at_age_seconds=started_age,  # …but the job blew its hard deadline
        live_owner=True,               # and the owner is still heartbeating
    )
    old_token = job.claim_token

    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n >= 1

    reloaded = await _reload(db_session, job.id)
    assert reloaded.status == "pending"
    assert reloaded.claim_token is None
    assert await _has_event_with_token(
        db_session, job.id, "reclaimed_forced", old_token
    )

    await db_session.commit()


# ---------------------------------------------------------------------------
# Task 5: fence worker-owned writes with the claim token + cancel-wins
# reconciliation (single-finalize contract) + heartbeat_check.
# ---------------------------------------------------------------------------


@pytest.fixture
async def fenced_job_factory():
    """Factory: build a committed job in a chosen status with a stamped
    claim_token (and optional phase rows), seeded/committed in its OWN session
    so the db_session under test never already holds it in its identity map.
    Yields ``make(...)`` returning a namespace with ``job_id`` / ``claim_token``
    / ``book_id``. Cleans up phases, events, jobs, toc, and books.
    """
    import uuid as _uuid
    from types import SimpleNamespace

    from sqlalchemy import delete, text
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    book_ids: list = []

    async def make(*, status: str = "running", phases=None):
        token = _uuid.uuid4()
        async with SessionLocal() as s:
            book = Book(
                subject="math-algebra",
                original_filename="fenced.pdf",
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
            )
            await s.execute(
                text(
                    "UPDATE homework_jobs SET status=:st, claimed_by='w:1@sha', "
                    "claim_token=:tok, claimed_at=now(), started_at=now(), "
                    "attempts=1 WHERE id=:id"
                ),
                {"st": status, "tok": token, "id": job.id},
            )
            for i, pstatus in enumerate(phases or []):
                s.add(
                    PhaseOutput(
                        job_id=job.id,
                        phase_name=f"p{i}",
                        phase_order=i,
                        prompt_hash="h",
                        model_name="m",
                        status=pstatus,
                        claim_token=token,
                    )
                )
            await s.commit()
            book_ids.append(book.id)
            return SimpleNamespace(job_id=job.id, claim_token=token, book_id=book.id)

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


async def test_stale_token_write_is_noop(db_session, fenced_job_factory):
    """A write presenting a token the row no longer carries returns LeaseLost
    and does NOT mutate the job (an obsolete worker can't complete the job that
    was reclaimed out from under it)."""
    import uuid as _uuid

    from app.repositories import jobs as jobs_repo
    from app.services import lease

    row = await fenced_job_factory(status="running")
    stale = _uuid.uuid4()  # a token that never owned this row
    assert stale != row.claim_token

    res = await jobs_repo.set_status(db_session, row.job_id, "done", claim_token=stale)
    assert res is lease.LeaseLost

    job = await _reload(db_session, row.job_id)
    assert job.status != "done"          # the obsolete worker did NOT complete it
    assert job.claim_token == row.claim_token  # real lease untouched
    assert await _has_event(db_session, row.job_id, "lease_lost")

    await db_session.commit()


async def test_cancel_still_wins_over_fenced_requeue(db_session, fenced_job_factory):
    """A user cancel set status='cancelling' out of band. A fenced retry with
    the CURRENT owner's token must resolve to CancelRequested (not a retry): the
    repo finalizes cancelling->cancelled AND fails every non-done phase row (the
    shipped 0155 cancel contract). done phases are preserved."""
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.services import lease

    row = await fenced_job_factory(status="cancelling", phases=["running", "done"])

    res = await jobs_repo.mark_failed_with_retry(
        db_session,
        row.job_id,
        error_message="boom",
        max_attempts=5,
        claim_token=row.claim_token,
    )
    assert res is lease.CancelRequested  # cancel wins, NOT a retry

    job = await _reload(db_session, row.job_id)
    assert job.status == "cancelled"     # the repo finalized internally
    assert job.claim_token is None

    phases = (
        await db_session.execute(
            select(PhaseOutput)
            .where(PhaseOutput.job_id == row.job_id)
            .order_by(PhaseOutput.phase_order)
        )
    ).scalars().all()
    assert phases[0].status == "failed"  # non-done phase swept to failed
    assert phases[1].status == "done"    # done phase preserved
    assert await _has_event(db_session, row.job_id, "released_cancelled")

    await db_session.commit()


async def test_heartbeat_check_distinguishes_lost_from_cancelling(
    db_session, fenced_job_factory
):
    """heartbeat_check re-reads and classifies: RENEWED for a live owned+running
    job, CANCELLING when a user cancel is pending, LOST when the token no longer
    matches (reclaimed under us). It never finalizes."""
    import uuid as _uuid

    from app.repositories import jobs as jobs_repo
    from app.services.lease import HeartbeatOutcome

    live = await fenced_job_factory(status="running")
    cancelling = await fenced_job_factory(status="cancelling")
    reclaimed = await fenced_job_factory(status="running")

    assert (
        await jobs_repo.heartbeat_check(db_session, live.job_id, live.claim_token)
        is HeartbeatOutcome.RENEWED
    )
    assert (
        await jobs_repo.heartbeat_check(
            db_session, cancelling.job_id, cancelling.claim_token
        )
        is HeartbeatOutcome.CANCELLING
    )
    assert (
        await jobs_repo.heartbeat_check(db_session, reclaimed.job_id, _uuid.uuid4())
        is HeartbeatOutcome.LOST
    )
    # heartbeat does NOT finalize the cancelling job — it only signals.
    still = await _reload(db_session, cancelling.job_id)
    assert still.status == "cancelling"

    await db_session.commit()


async def test_heartbeat_renew_race_never_finalizes(db_session, fenced_job_factory):
    """READ COMMITTED race guard: a cancel that commits between heartbeat_check's
    re-read (saw running) and touch_claim's fenced UPDATE must NOT be finalized
    by the heartbeat. Deterministic form: run the fenced touch_claim / the whole
    heartbeat_check against a job that is ALREADY `cancelling` (the window's end
    state) and assert the heartbeat neither finalized the job nor swept phases
    nor wrote a released_cancelled event — only the worker's terminal write may
    finalize. RED-proof: the old always-RENEWED path routed through a finalizing
    touch_claim, which would flip the job to `cancelled` and fail the phase."""
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.services import lease
    from app.services.lease import HeartbeatOutcome

    row = await fenced_job_factory(status="cancelling", phases=["running"])

    # 1) The fenced touch_claim itself must be a pure signal, never a finalize.
    res = await jobs_repo.touch_claim(
        db_session, row.job_id, claim_token=row.claim_token
    )
    assert res is lease.CancelRequested

    job = await _reload(db_session, row.job_id)
    assert job.status == "cancelling"          # NOT finalized to cancelled
    assert job.claim_token == row.claim_token  # lease untouched

    phase = (
        await db_session.execute(
            select(PhaseOutput).where(PhaseOutput.job_id == row.job_id)
        )
    ).scalar_one()
    assert phase.status == "running"           # NOT swept to failed
    assert not await _has_event(db_session, row.job_id, "released_cancelled")

    # 2) And the full heartbeat_check reports CANCELLING (not a false RENEWED)
    #    while STILL leaving the job un-finalized.
    outcome = await jobs_repo.heartbeat_check(
        db_session, row.job_id, row.claim_token
    )
    assert outcome is HeartbeatOutcome.CANCELLING
    job2 = await _reload(db_session, row.job_id)
    assert job2.status == "cancelling"
    assert not await _has_event(db_session, row.job_id, "released_cancelled")

    await db_session.commit()


async def test_tokenless_calls_keep_legacy_behavior(db_session, fenced_job_factory):
    """Transitional guarantee: token-less callers (pipeline/worker pre-Tasks 6-7)
    get the EXACT pre-fencing behavior — no token predicate, bool/str returns."""
    from app.repositories import jobs as jobs_repo

    row = await fenced_job_factory(status="running")
    # set_status without a token still returns a bool and mutates the row.
    ok = await jobs_repo.set_status(db_session, row.job_id, "done")
    assert ok is True
    job = await _reload(db_session, row.job_id)
    assert job.status == "done"

    await db_session.commit()
