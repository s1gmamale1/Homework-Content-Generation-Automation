"""Real-DB: fenced phase writes (fenced job leases, Task 6).

``phase_outputs.create_or_reset`` locks the JOB row (SELECT ... FOR UPDATE)
before touching phase rows and verifies the caller's lease against
``job.claim_token`` — a stale lease writes nothing. Phase ``set_status`` adds
an ``AND claim_token = :token`` guard when a token is supplied.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os
import uuid as _uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only"
)


# ---------------------------------------------------------------------------
# db_session fixture — mirrors tests/repositories/test_lease_fencing.py.
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# fenced_job_factory — mirrors test_lease_fencing.py's Task 5 idiom: a
# committed job in a chosen status with a stamped claim_token, seeded/
# committed in its OWN session so the db_session under test never already
# holds it in its identity map. Optionally seeds an existing phase row so
# create_or_reset exercises the reset branch too.
# ---------------------------------------------------------------------------

@pytest.fixture
async def fenced_job_factory():
    from sqlalchemy import delete, text
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    book_ids: list = []

    async def make(*, status: str = "running", seed_phase: dict | None = None):
        token = _uuid.uuid4()
        async with SessionLocal() as s:
            book = Book(
                subject="math-algebra",
                original_filename="phase-fencing.pdf",
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
            if seed_phase is not None:
                s.add(
                    PhaseOutput(
                        job_id=job.id,
                        phase_name=seed_phase.get("phase_name", "preview"),
                        phase_order=seed_phase.get("phase_order", 0),
                        prompt_hash=seed_phase.get("prompt_hash", "old-hash"),
                        model_name=seed_phase.get("model_name", "old-model"),
                        status=seed_phase.get("status", "pending"),
                        claim_token=seed_phase.get("claim_token", token),
                    )
                )
            await s.commit()
            book_ids.append(book.id)
            return SimpleNamespace(job_id=job.id, claim_token=token, book_id=book.id)

    yield make

    from app.models.homework_job import HomeworkJob as _HJ

    async with SessionLocal() as s:
        for bid in book_ids:
            job_ids = select(_HJ.id).where(_HJ.book_id == bid)
            await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_(job_ids)))
            await s.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
            await s.execute(delete(_HJ).where(_HJ.book_id == bid))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == bid))
            await s.execute(delete(Book).where(Book.id == bid))
        await s.commit()


async def _phase_rows(session, job_id):
    from app.models.phase_output import PhaseOutput

    return (
        await session.execute(
            select(PhaseOutput).where(PhaseOutput.job_id == job_id)
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# create_or_reset fencing
# ---------------------------------------------------------------------------


async def test_create_or_reset_stale_lease_writes_nothing(db_session, fenced_job_factory):
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    from app.services.lease import JobLease

    row = await fenced_job_factory(status="running")
    stale = JobLease(job_id=row.job_id, claim_token=_uuid.uuid4(), owner_id="obsolete:1@sha")
    assert stale.claim_token != row.claim_token

    res = await phase_repo.create_or_reset(
        db_session,
        job_id=row.job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="h",
        model_name="m",
        status="running",
        lease=stale,
    )
    assert res is lease.LeaseLost

    rows = await _phase_rows(db_session, row.job_id)
    assert rows == []  # nothing created

    await db_session.commit()


async def test_create_or_reset_stale_lease_does_not_reset_existing_row(
    db_session, fenced_job_factory
):
    """A stale lease must not touch a pre-existing phase row either — the
    hard-reset body never runs when the job-row lock check fails."""
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    from app.services.lease import JobLease

    row = await fenced_job_factory(
        status="running",
        seed_phase={"phase_name": "preview", "status": "failed", "prompt_hash": "old-hash"},
    )
    stale = JobLease(job_id=row.job_id, claim_token=_uuid.uuid4(), owner_id="obsolete:1@sha")

    res = await phase_repo.create_or_reset(
        db_session,
        job_id=row.job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="new-hash",
        model_name="new-model",
        status="running",
        lease=stale,
    )
    assert res is lease.LeaseLost

    rows = await _phase_rows(db_session, row.job_id)
    assert len(rows) == 1
    assert rows[0].status == "failed"          # untouched
    assert rows[0].prompt_hash == "old-hash"    # untouched

    await db_session.commit()


async def test_create_or_reset_current_lease_stamps_token_on_insert(
    db_session, fenced_job_factory
):
    from app.repositories import phase_outputs as phase_repo
    from app.services.lease import JobLease

    row = await fenced_job_factory(status="running")
    current = JobLease(job_id=row.job_id, claim_token=row.claim_token, owner_id="w:1@sha")

    result = await phase_repo.create_or_reset(
        db_session,
        job_id=row.job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="h",
        model_name="m",
        status="running",
        lease=current,
    )
    assert result.claim_token == current.claim_token
    assert result.status == "running"

    rows = await _phase_rows(db_session, row.job_id)
    assert len(rows) == 1
    assert rows[0].claim_token == current.claim_token

    await db_session.commit()


async def test_create_or_reset_current_lease_stamps_token_on_reset(
    db_session, fenced_job_factory
):
    """The reset (UPDATE) branch also gets the token stamped, not just insert."""
    from app.repositories import phase_outputs as phase_repo
    from app.services.lease import JobLease

    row = await fenced_job_factory(
        status="running",
        seed_phase={"phase_name": "preview", "status": "failed", "claim_token": None},
    )
    current = JobLease(job_id=row.job_id, claim_token=row.claim_token, owner_id="w:1@sha")

    result = await phase_repo.create_or_reset(
        db_session,
        job_id=row.job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="new-hash",
        model_name="new-model",
        status="pending",
        lease=current,
    )
    assert result.claim_token == current.claim_token
    assert result.status == "pending"
    assert result.prompt_hash == "new-hash"

    rows = await _phase_rows(db_session, row.job_id)
    assert len(rows) == 1  # reset, not a new row
    assert rows[0].claim_token == current.claim_token

    await db_session.commit()


async def test_create_or_reset_missing_job_returns_lease_lost(db_session):
    """A job_id with no row at all (deleted / never existed) is treated like a
    lost lease — nothing is written."""
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    from app.services.lease import JobLease

    ghost_job_id = _uuid.uuid4()
    ghost_lease = JobLease(job_id=ghost_job_id, claim_token=_uuid.uuid4(), owner_id="w:1@sha")

    res = await phase_repo.create_or_reset(
        db_session,
        job_id=ghost_job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="h",
        model_name="m",
        status="running",
        lease=ghost_lease,
    )
    assert res is lease.LeaseLost

    rows = await _phase_rows(db_session, ghost_job_id)
    assert rows == []

    await db_session.commit()


async def test_create_or_reset_no_lease_legacy_unchanged(db_session, fenced_job_factory):
    """Transitional guarantee: lease=None (every caller until Task 7) keeps the
    exact pre-fencing behavior — no lock, no token stamped, PhaseOutput back."""
    from app.repositories import phase_outputs as phase_repo

    row = await fenced_job_factory(status="running")

    result = await phase_repo.create_or_reset(
        db_session,
        job_id=row.job_id,
        phase_name="preview",
        phase_order=0,
        prompt_hash="h",
        model_name="m",
        status="pending",
    )
    assert result.claim_token is None
    assert result.status == "pending"

    await db_session.commit()


async def test_create_or_reset_locks_job_row_for_update(db_session, fenced_job_factory):
    """Lock order is job->phase: create_or_reset(lease=...) must issue a
    SELECT ... FOR UPDATE against homework_jobs before it writes the phase
    row. Captured-SQL assertion (deterministic, no concurrency needed)."""
    from app.db import engine
    from app.repositories import phase_outputs as phase_repo
    from app.services.lease import JobLease

    row = await fenced_job_factory(status="running")
    current = JobLease(job_id=row.job_id, claim_token=row.claim_token, owner_id="w:1@sha")

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        result = await phase_repo.create_or_reset(
            db_session,
            job_id=row.job_id,
            phase_name="preview",
            phase_order=0,
            prompt_hash="h",
            model_name="m",
            status="running",
            lease=current,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert result.claim_token == current.claim_token

    job_lock_stmts = [
        s for s in captured
        if "homework_jobs" in s.lower() and "for update" in s.lower()
    ]
    assert job_lock_stmts, f"expected a SELECT ... FOR UPDATE on homework_jobs, got: {captured}"

    await db_session.commit()


# ---------------------------------------------------------------------------
# set_status fencing
# ---------------------------------------------------------------------------


async def test_set_status_stale_token_returns_lease_lost_row_unchanged(
    db_session, fenced_job_factory
):
    from app.models.phase_output import PhaseOutput
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease

    row = await fenced_job_factory(
        status="running", seed_phase={"phase_name": "preview", "status": "running"}
    )
    phase = (await _phase_rows(db_session, row.job_id))[0]
    stale = _uuid.uuid4()
    assert stale != phase.claim_token

    res = await phase_repo.set_status(
        db_session, phase.id, "done", output_md="x", claim_token=stale
    )
    assert res is lease.LeaseLost

    reloaded = (
        await db_session.execute(
            select(PhaseOutput)
            .where(PhaseOutput.id == phase.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert reloaded.status == "running"   # unchanged
    assert reloaded.output_md is None     # unchanged

    await db_session.commit()


async def test_set_status_current_token_applies(db_session, fenced_job_factory):
    from app.models.phase_output import PhaseOutput
    from app.repositories import phase_outputs as phase_repo

    row = await fenced_job_factory(
        status="running", seed_phase={"phase_name": "preview", "status": "running"}
    )
    phase = (await _phase_rows(db_session, row.job_id))[0]

    res = await phase_repo.set_status(
        db_session, phase.id, "done", output_md="hello", claim_token=phase.claim_token
    )
    assert res is True

    reloaded = (
        await db_session.execute(
            select(PhaseOutput)
            .where(PhaseOutput.id == phase.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert reloaded.status == "done"
    assert reloaded.output_md == "hello"

    await db_session.commit()


async def test_set_status_no_token_legacy_unchanged(db_session, fenced_job_factory):
    """Transitional guarantee: claim_token=None keeps the exact pre-fencing
    behavior — plain bool, no token predicate."""
    from app.repositories import phase_outputs as phase_repo

    row = await fenced_job_factory(
        status="running", seed_phase={"phase_name": "preview", "status": "running"}
    )
    phase = (await _phase_rows(db_session, row.job_id))[0]

    ok = await phase_repo.set_status(db_session, phase.id, "done", output_md="hi")
    assert ok is True

    await db_session.commit()


async def test_set_status_matching_token_row_already_done_is_benign_not_lease_lost(
    db_session, fenced_job_factory
):
    """Forward-fix (Task 7 #8): a 0-row match with a token must be
    DISAMBIGUATED. When the token STILL matches but the status guard blocked the
    write (the row is already 'done'), that is the pre-existing benign no-op —
    return the LEGACY result (``False``), NOT ``LeaseLost``. Only a genuine
    token MISMATCH is a lease loss (covered by
    ``test_set_status_stale_token_returns_lease_lost_row_unchanged``).

    RED-proof: without the re-read, the 0-row match returns ``LeaseLost`` here,
    conflating an already-done row with a real reclaim."""
    from app.models.phase_output import PhaseOutput
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease

    # Seed the phase already 'done' (guard=True WHERE status != 'done' will match
    # 0 rows) with the CORRECT (job) token.
    row = await fenced_job_factory(
        status="running", seed_phase={"phase_name": "preview", "status": "done"}
    )
    phase = (await _phase_rows(db_session, row.job_id))[0]
    assert phase.status == "done"
    assert phase.claim_token == row.claim_token

    res = await phase_repo.set_status(
        db_session, phase.id, "done", output_md="ignored", claim_token=phase.claim_token
    )
    # Benign no-op — the row was already done and the token is ours. NOT LeaseLost.
    assert res is not lease.LeaseLost
    assert res is False

    reloaded = (
        await db_session.execute(
            select(PhaseOutput)
            .where(PhaseOutput.id == phase.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert reloaded.status == "done"     # unchanged
    assert reloaded.output_md is None    # the blocked write never applied

    await db_session.commit()
