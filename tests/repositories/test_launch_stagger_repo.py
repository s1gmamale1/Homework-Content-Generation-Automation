"""Repo-level launch-stagger stamping. No DB: `create` only calls
`session.add` + `session.flush`, so a stub session exercises it fully."""
import uuid

import pytest
from sqlalchemy.sql.elements import ClauseElement

from app.repositories import jobs as jobs_repo


class _StubSession:
    """Minimal AsyncSession stand-in for `create` (add + flush only)."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _kwargs():
    return dict(book_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
                subject="geografiya", output_language="ru")


def _sql(expr) -> str:
    return str(expr.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.mark.asyncio
async def test_no_offset_leaves_scheduled_at_to_the_server_default():
    """Existing callers must be unaffected: the column is never assigned, so
    Postgres applies its NOW() server default exactly as before."""
    job = await jobs_repo.create(_StubSession(), **_kwargs())
    assert job.scheduled_at is None


@pytest.mark.asyncio
async def test_offset_uses_the_db_clock_not_the_host_clock():
    """The claim gate compares against func.now(); worker host clocks drift."""
    job = await jobs_repo.create(_StubSession(), start_offset_seconds=120, **_kwargs())
    assert isinstance(job.scheduled_at, ClauseElement)
    sql = _sql(job.scheduled_at)
    assert "now()" in sql
    assert "make_interval" in sql
    assert "120" in sql


@pytest.mark.asyncio
async def test_zero_offset_is_indistinguishable_from_omitting_it():
    job = await jobs_repo.create(_StubSession(), start_offset_seconds=0, **_kwargs())
    assert job.scheduled_at is None


class _GetStubSession(_StubSession):
    """Adds `get`, which `reset_for_retry` uses to load the row."""

    def __init__(self, job):
        super().__init__()
        self._job = job

    async def get(self, model, pk):
        return self._job


class _FakeJob:
    """Only the attributes reset_for_retry writes."""

    def __init__(self):
        self.id = uuid.uuid4()
        self.status = "failed"
        self.error_message = "boom"
        self.current_phase = "flashcards"
        self.started_at = object()
        self.completed_at = object()
        self.attempts = 3
        self.claim_token = uuid.uuid4()
        self.claimed_at = object()
        self.claimed_by = "Host-02:1"
        self.batch_id = None
        self.scheduled_at = "ORIGINAL"


@pytest.mark.asyncio
async def test_resume_without_offset_leaves_scheduled_at_alone():
    """Pre-plan behaviour, preserved exactly: a resumed job keeps its original
    (past) timestamp and is claimable immediately."""
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id)
    assert job.scheduled_at == "ORIGINAL"
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_resume_with_offset_pushes_scheduled_at_on_the_db_clock():
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id,
                                    start_offset_seconds=180)
    assert isinstance(job.scheduled_at, ClauseElement)
    sql = _sql(job.scheduled_at)
    assert "now()" in sql and "make_interval" in sql and "180" in sql


@pytest.mark.asyncio
async def test_resume_offset_does_not_disturb_the_lease_reset():
    """The stagger must not weaken the fenced-lease rotation (jobs.py:278-282)."""
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id,
                                    start_offset_seconds=60)
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.claimed_by is None
    assert job.attempts == 0
