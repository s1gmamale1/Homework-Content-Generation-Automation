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
