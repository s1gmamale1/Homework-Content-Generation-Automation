"""Retry budget must count EXECUTION failures, not SCHEDULING failures.

Production incident (fleet saturation test): 27 of 254 lessons were marked
permanently `failed` with "attempts exhausted while pending (stale-pending
sweep)" at attempts=3 of QUEUE_MAX_ATTEMPTS=3 — and NONE of them had ever made
a successful API call. The sequence per lesson was:

  1. a worker claims it (`claim_next_job` charges attempts += 1, jobs.py:620);
  2. it blocks on a contended DB lock BEFORE starting any phase, so
     `current_phase` stays NULL (18 of 37 "running" jobs were in this state);
  3. `reclaim_stuck_jobs` returns it to `pending` — and the attempt stays
     charged;
  4. after 3 rounds `fail_exhausted_pending_jobs` marks it terminally failed.

So the budget that exists to bound *execution* failures was being consumed by
*scheduling* failures, and transient infrastructure contention destroyed queued
work instead of deferring it.

These tests drive the REAL `jobs_repo.reclaim_stuck_jobs` body; only the
session boundary is faked (the established offline style in this package — see
test_requeue_session_limited.py and test_fail_exhausted_pending.py). The fake
executor INTERPRETS the emitted UPDATE, applying it to an in-memory job row, so
a full claim -> block -> reclaim loop can be run for real and the resulting
`attempts` asserted round by round.

RED-proof (verified against the pre-fix body, which emitted a single UPDATE
with no `attempts` clause for every reclaimed job):
  * test_never_executed_job_keeps_its_full_retry_budget — pre-fix the loop
    ends at attempts=10, and the FIRST round already leaves attempts=1
    instead of 0.
  * test_never_executed_reclaim_refunds_the_claims_increment — pre-fix the
    emitted UPDATE contains no GREATEST(...) refund at all.
  * test_reclaim_budget_is_bounded_so_a_wedged_job_still_terminates — pre-fix
    there is no `reclaims` counter, so the ceiling behaviour does not exist.
The two "executed" tests are regression guards for the opposite direction: a
job that ran and failed must STILL burn attempts and still terminate.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Select, Update
from sqlalchemy.dialects import postgresql

from app.repositories.jobs import reclaim_stuck_jobs

# The exact SET fragments the reclaim emits, verified by compiling the real
# statement against the postgresql dialect.
_REFUND_SQL = "attempts=greatest(homework_jobs.attempts - 1, 0)"
_BUMP_RECLAIMS_SQL = "reclaims=(homework_jobs.reclaims + 1)"
_RESET_RECLAIMS_SQL = "reclaims=0"


# ---------------------------------------------------------------------------
# Fake session: interprets the sweep's statements against in-memory job rows
# ---------------------------------------------------------------------------

class _Result:
    """Stands in for the CursorResult of the snapshot SELECTs."""

    rowcount = 0

    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """Applies `reclaim_stuck_jobs`'s statements to `self.jobs`.

    The snapshot SELECTs are answered from the in-memory rows in the shape the
    real query returns — (id, claim_token, executed, reclaims). The first
    SELECT is the NORMAL/stale set (the production incident: claim went stale,
    owner absent from the registry); the second is the FORCED past-hard-
    deadline set, empty here. UPDATEs are interpreted from their compiled SQL
    so `attempts` / `reclaims` actually move.
    """

    def __init__(self, jobs):
        self.jobs = jobs
        self.selects = 0
        self.updates: list[str] = []

    async def execute(self, stmt):
        if isinstance(stmt, Select):
            self.selects += 1
            if self.selects == 1:  # NORMAL/stale snapshot
                return _Result(
                    (j["id"], j["claim_token"], j["executed"], j["reclaims"])
                    for j in self.jobs
                    if j["status"] == "running"
                )
            return _Result()  # FORCED snapshot — nothing past the hard deadline
        if isinstance(stmt, Update):
            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            self.updates.append(sql)
            for job in self.jobs:
                if str(job["id"]) not in sql:
                    continue
                job["status"] = "pending"
                job["claim_token"] = None
                job["current_phase"] = None
                if _REFUND_SQL in sql:
                    job["attempts"] = max(job["attempts"] - 1, 0)
                if _BUMP_RECLAIMS_SQL in sql:
                    job["reclaims"] += 1
                elif _RESET_RECLAIMS_SQL in sql:
                    job["reclaims"] = 0
        return _Result()

    def update_for(self, job) -> str:
        """The single UPDATE that targeted this job in the last sweep."""
        hits = [sql for sql in self.updates if str(job["id"]) in sql]
        assert len(hits) == 1, (
            f"expected exactly one UPDATE for job {job['id']}, got {len(hits)}"
        )
        return hits[0]


def _job(*, executed: bool, attempts: int = 0, reclaims: int = 0) -> dict:
    return {
        "id": uuid.uuid4(),
        "claim_token": uuid.uuid4(),
        "status": "running",
        "current_phase": "flashcards" if executed else None,
        "executed": executed,
        "attempts": attempts,
        "reclaims": reclaims,
    }


async def _claim_then_reclaim(session, job, *, max_reclaims=None) -> None:
    """One full queue round-trip: a worker claims the job (claim_next_job
    charges the attempt, jobs.py:620) and the stale sweep takes it back."""
    job["status"] = "running"
    job["claim_token"] = uuid.uuid4()
    job["attempts"] += 1
    session.updates.clear()
    session.selects = 0
    kwargs = {} if max_reclaims is None else {"max_reclaims": max_reclaims}
    await reclaim_stuck_jobs(session, stale_after_seconds=120, **kwargs)


@pytest.fixture(autouse=True)
def _no_phase_reconciliation(monkeypatch):
    """The sweep's same-transaction phase reconciliation is out of scope here
    (covered by test_jobs_orphan_reconciliation.py); patch the exact reference
    jobs.py reaches it through."""
    from unittest.mock import AsyncMock

    import app.repositories.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module.phase_repo, "reset_abandoned_phases", AsyncMock(return_value=0)
    )


# ---------------------------------------------------------------------------
# A job that NEVER started executing must not burn its retry budget
# ---------------------------------------------------------------------------

async def test_never_executed_reclaim_refunds_the_claims_increment():
    """The incident case: claimed, blocked on a lock before any phase, swept.

    `attempts` was charged at claim time for work that never ran, so the
    reclaim must refund it — the same GREATEST(attempts - 1, 0) idiom
    requeue_session_limited / requeue_slot_saturated already use.
    """
    job = _job(executed=False)
    session = _FakeSession([job])

    await _claim_then_reclaim(session, job)

    sql = session.update_for(job)
    assert _REFUND_SQL in sql, f"reclaim must refund the claim's increment; got:\n{sql}"
    assert job["attempts"] == 0, "a job that never executed must not owe an attempt"
    assert job["status"] == "pending", "the job must be requeued, not failed"


async def test_never_executed_job_keeps_its_full_retry_budget():
    """Ten rounds of pure scheduling failure must leave the budget untouched.

    This is the whole defect: under QUEUE_MAX_ATTEMPTS=3 the pre-fix code hit
    the ceiling on round 3 and `fail_exhausted_pending_jobs` destroyed the
    lesson. The job must stay claimable (attempts < max_attempts) instead.
    """
    max_attempts = 3
    job = _job(executed=False)
    session = _FakeSession([job])

    for _ in range(10):
        await _claim_then_reclaim(session, job)
        assert job["attempts"] < max_attempts, (
            "a job that never executed must never reach QUEUE_MAX_ATTEMPTS "
            f"(attempts={job['attempts']}, reclaims={job['reclaims']})"
        )

    assert job["attempts"] == 0
    # The scheduling failures are still counted — just on their own budget, so
    # the incident stays visible instead of being silently swallowed.
    assert job["reclaims"] == 10


async def test_never_executed_reclaim_counts_against_its_own_budget():
    """The refund is recorded on `reclaims`, never on `attempts`."""
    job = _job(executed=False, reclaims=4)
    session = _FakeSession([job])

    await _claim_then_reclaim(session, job)

    sql = session.update_for(job)
    assert _BUMP_RECLAIMS_SQL in sql, f"reclaim must bump `reclaims`; got:\n{sql}"
    assert job["reclaims"] == 5


# ---------------------------------------------------------------------------
# Regression guard: a job that RAN and failed must still burn attempts
# ---------------------------------------------------------------------------

async def test_executed_job_still_burns_its_attempt():
    """Execution evidence present -> no refund. The retry budget must still
    bound genuine execution failures; refunding here would let a poison-pill
    job re-run forever."""
    job = _job(executed=True, attempts=1)
    session = _FakeSession([job])

    await _claim_then_reclaim(session, job)

    sql = session.update_for(job)
    assert _REFUND_SQL not in sql, (
        f"a job that began executing must NOT get its attempt refunded; got:\n{sql}"
    )
    assert "attempts" not in sql, (
        f"the executed path must not touch `attempts` at all; got:\n{sql}"
    )
    assert job["attempts"] == 2, "the executed attempt stays charged"


async def test_executed_job_still_terminates_at_max_attempts():
    """A job that keeps executing and failing must still exhaust its budget and
    become unclaimable at QUEUE_MAX_ATTEMPTS — the poison-pill guarantee."""
    max_attempts = 3
    job = _job(executed=True)
    session = _FakeSession([job])

    for _ in range(max_attempts):
        await _claim_then_reclaim(session, job)

    assert job["attempts"] == max_attempts, (
        "every execution attempt must be charged"
    )
    # `attempts >= max_attempts` is exactly what claim_next_job's gate
    # (jobs.py:588) and fail_exhausted_pending_jobs both key on, so the job is
    # now unclaimable and the stale-pending sweep will fail it terminally.
    assert job["attempts"] >= max_attempts


async def test_execution_resets_the_scheduling_streak():
    """`reclaims` measures a CONSECUTIVE no-execution streak, so a job that
    manages to start a phase gets its refund budget back."""
    job = _job(executed=True, reclaims=7)
    session = _FakeSession([job])

    await _claim_then_reclaim(session, job)

    sql = session.update_for(job)
    assert _RESET_RECLAIMS_SQL in sql, f"execution must reset `reclaims`; got:\n{sql}"
    assert job["reclaims"] == 0


# ---------------------------------------------------------------------------
# The free requeue is bounded — a genuinely wedged job still terminates
# ---------------------------------------------------------------------------

async def test_reclaim_budget_is_bounded_so_a_wedged_job_still_terminates():
    """A job that is claimed and reclaimed forever without EVER starting a
    phase is wedged, not merely unlucky. Past `max_reclaims` the refund stops
    and the ordinary `attempts` machinery terminates it, so an unbounded
    free-requeue loop can never pin worker slots indefinitely."""
    max_attempts, max_reclaims = 3, 5
    job = _job(executed=False)
    session = _FakeSession([job])

    # Under the ceiling: fully protected.
    for _ in range(max_reclaims):
        await _claim_then_reclaim(session, job, max_reclaims=max_reclaims)
    assert job["attempts"] == 0
    assert job["reclaims"] == max_reclaims

    # At the ceiling: refunds stop, so attempts start accruing again.
    for _ in range(max_attempts):
        await _claim_then_reclaim(session, job, max_reclaims=max_reclaims)
        assert _REFUND_SQL not in session.update_for(job)

    assert job["attempts"] == max_attempts, (
        "past the reclaim ceiling a wedged job must fall back to burning "
        "attempts so it terminates instead of looping forever"
    )
    assert job["reclaims"] == max_reclaims, "the capped path must not reset the streak"


async def test_ceiling_defaults_to_settings_queue_max_reclaims():
    """The ceiling is operator-tunable and defaults far above
    queue_max_attempts — transient contention must never destroy work."""
    from app.config import settings

    assert settings.queue_max_reclaims > settings.queue_max_attempts

    job = _job(executed=False, reclaims=settings.queue_max_reclaims)
    session = _FakeSession([job])

    await _claim_then_reclaim(session, job)  # no explicit max_reclaims

    assert _REFUND_SQL not in session.update_for(job), (
        "at settings.queue_max_reclaims the refund must stop without the "
        "caller passing max_reclaims explicitly"
    )


# ---------------------------------------------------------------------------
# The signal itself
# ---------------------------------------------------------------------------

async def test_execution_evidence_is_read_before_it_is_destroyed():
    """The snapshot SELECT must carry the evidence, because the reclaim UPDATE
    NULLs `current_phase` and reset_abandoned_phases clears the phase tokens —
    read afterwards, every job would look like it never executed."""
    job = _job(executed=True)
    session = _FakeSession([job])
    captured: list[str] = []

    original = session.execute

    async def _spy(stmt):
        if isinstance(stmt, Select):
            captured.append(
                str(stmt.compile(dialect=postgresql.dialect(),
                                 compile_kwargs={"literal_binds": True}))
            )
        return await original(stmt)

    session.execute = _spy
    await _claim_then_reclaim(session, job)

    snapshot = captured[0]
    assert "current_phase IS NOT NULL" in snapshot, (
        f"snapshot must read the current_phase signal; got:\n{snapshot}"
    )
    assert "phase_outputs.claim_token = homework_jobs.claim_token" in snapshot, (
        "snapshot must corroborate with a phase row stamped by THIS lease; "
        f"got:\n{snapshot}"
    )
    assert "FOR UPDATE" in snapshot, (
        "the evidence must be read under the same row lock that serialises "
        f"the sweep against a phase start; got:\n{snapshot}"
    )
