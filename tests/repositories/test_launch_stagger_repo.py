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
    """The stagger must not weaken the fenced-lease rotation (jobs.py:299-301)."""
    job = _FakeJob()
    await jobs_repo.reset_for_retry(_GetStubSession(job), job.id,
                                    start_offset_seconds=60)
    assert job.claim_token is None
    assert job.claimed_at is None
    assert job.claimed_by is None
    assert job.attempts == 0


class _ResumeStubSession:
    """Stands in for the `select(...)` in resume_failed_in_batch."""

    def __init__(self, jobs):
        self._jobs = jobs
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _Result:
            def __init__(self, jobs):
                self._jobs = jobs

            def scalars(self):
                return self

            def all(self):
                return self._jobs

        return _Result(self._jobs)


@pytest.mark.asyncio
async def test_resume_assigns_waves_in_order(monkeypatch):
    jobs = [_FakeJob() for _ in range(8)]
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    out = await jobs_repo.resume_failed_in_batch(
        _ResumeStubSession(jobs), uuid.uuid4(), wave_size=3, interval_seconds=60)

    assert out["resumed"] == 8
    assert seen == [0, 0, 0, 60, 60, 60, 120, 120]


@pytest.mark.asyncio
async def test_retired_jobs_do_not_consume_a_wave_slot(monkeypatch):
    """A skipped retired job adds no load, so the next real job must stay in
    the same wave it would have occupied anyway."""
    jobs = [_FakeJob() for _ in range(4)]
    retired_id = jobs[1].id
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job",
        lambda job: (("content", "gemini", "gemini-2.5-flash"),) if job.id == retired_id else ())

    out = await jobs_repo.resume_failed_in_batch(
        _ResumeStubSession(jobs), uuid.uuid4(), wave_size=2, interval_seconds=60)

    assert out["resumed"] == 3
    assert len(out["skipped_retired"]) == 1
    # 3 resumable jobs at wave_size 2 -> waves 0, 0, 1 (NOT 0, 1, 1)
    assert seen == [0, 0, 60]


@pytest.mark.asyncio
async def test_resume_without_wave_args_does_not_stagger(monkeypatch):
    """Default call site behaviour is unchanged."""
    jobs = [_FakeJob() for _ in range(5)]
    seen = []

    async def _fake_reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        seen.append(start_offset_seconds)

    monkeypatch.setattr(jobs_repo, "reset_for_retry", _fake_reset)
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    await jobs_repo.resume_failed_in_batch(_ResumeStubSession(jobs), uuid.uuid4())

    assert seen == [0, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_resume_select_is_deterministically_ordered(monkeypatch):
    """Without an ORDER BY, which lesson lands in wave 0 varies per run."""
    session = _ResumeStubSession([])
    monkeypatch.setattr(
        "app.services.job_reactivation.retired_models_in_job", lambda job: ())

    await jobs_repo.resume_failed_in_batch(session, uuid.uuid4())

    assert "order by" in str(session.statements[0]).lower()
