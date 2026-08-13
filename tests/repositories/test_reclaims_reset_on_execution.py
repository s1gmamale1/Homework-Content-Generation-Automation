"""`reclaims` counts CONSECUTIVE never-executed reclaims, so real execution must clear it.

Found by an independent audit of the retry-accounting fix. `reclaim_stuck_jobs()` resets the
counter when the sweep itself observes execution evidence, but the ordinary executed-failure
path — `mark_failed_with_retry()` — requeued without resetting it. A job could therefore bank
20 never-started reclaims, then execute and fail a phase normally, and have its NEXT
never-started reclaim treated as over the cap: that reclaim would charge an execution attempt
for a scheduling failure, which is precisely what the counter exists to prevent.

Reaching the retry branch of `mark_failed_with_retry` proves the job ran and failed inside a
phase, so it is the correct place to clear the streak.
"""

from __future__ import annotations

import uuid

import pytest

from app.repositories import jobs as jobs_repo


class _Job:
    def __init__(self, attempts: int):
        self.id = uuid.uuid4()
        self.attempts = attempts
        self.claim_token = uuid.uuid4()
        self.status = "running"


class _FakeSession:
    """Only needs to hand `mark_failed_with_retry` a job row."""

    def __init__(self, job: _Job):
        self._job = job

    async def get(self, _model, _pk, **_kw):
        return self._job

    async def flush(self):
        return None


async def _capture_values(monkeypatch, *, attempts: int, max_attempts: int) -> dict:
    """Run mark_failed_with_retry and return the `values` dict it would write."""
    job = _Job(attempts=attempts)
    captured: dict = {}

    async def _fake_fenced_update(_session, _job_id, _claim_token, values, **_kw):
        captured.update(values)
        return object()

    monkeypatch.setattr(jobs_repo, "_fenced_update", _fake_fenced_update)
    await jobs_repo.mark_failed_with_retry(
        _FakeSession(job),
        job.id,
        error_message="phase.run boom",
        max_attempts=max_attempts,
        claim_token=job.claim_token,
    )
    return captured


@pytest.mark.asyncio
async def test_executed_failure_requeue_clears_the_reclaim_streak(monkeypatch):
    # attempts(1) < max(3) -> the retry branch, i.e. the job ran and failed a phase.
    values = await _capture_values(monkeypatch, attempts=1, max_attempts=3)
    assert values["status"] == "pending", "sanity: this is the retry branch"
    assert values.get("reclaims") == 0, (
        "an executed failure must reset the consecutive never-executed streak; "
        "without this a job that banked reclaims then genuinely executed would have "
        "its next scheduling failure charged against the execution retry budget"
    )


@pytest.mark.asyncio
async def test_terminal_failure_does_not_need_to_touch_reclaims(monkeypatch):
    # attempts(3) == max(3) -> terminal. The row is finished; the streak is moot,
    # and writing it would only add noise to the terminal update.
    values = await _capture_values(monkeypatch, attempts=3, max_attempts=3)
    assert values["status"] == "failed", "sanity: this is the terminal branch"
    assert "reclaims" not in values
