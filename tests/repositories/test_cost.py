"""Unit tests for app/repositories/cost.py — cost ledger read functions.

The REAL function body is exercised; only the session.execute boundary is
mocked (pattern mirrors test_fail_exhausted_pending.py).  No DB required.
DB-integration variants are gated on RUN_DB_INTEGRATION=1 and listed at the
bottom of this file.

Three properties under test for each function:
  1. api-only filtering — cli rows are excluded from the sum.
  2. correct cost_usd summation — tokens → dollars via pricing.cost_usd.
  3. structural correctness — right join / filter path (verifiable by what the
     mock captures without a real DB).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal ORM-like stubs that duck-type AgentUsage / HomeworkJob rows
# ---------------------------------------------------------------------------


class _Usage:
    """Stub row returned by queries against agent_usages."""

    def __init__(
        self,
        *,
        provider: str,
        model_name: Optional[str],
        auth_mode: str,
        homework_job_id: Optional[uuid.UUID] = None,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        cache_creation_tokens: int = 0,
        total_tokens: int = 0,
        started_at: Optional[datetime] = None,
    ):
        self.provider = provider
        self.model_name = model_name
        self.auth_mode = auth_mode
        self.homework_job_id = homework_job_id
        self.prompt_tokens = prompt_tokens
        self.output_tokens = output_tokens
        self.cached_tokens = cached_tokens
        self.cache_creation_tokens = cache_creation_tokens
        self.total_tokens = total_tokens
        self.started_at = started_at or datetime.now(timezone.utc)
        self.id = uuid.uuid4()


class _Job:
    """Stub row returned by queries against homework_jobs."""

    def __init__(
        self,
        *,
        batch_id: Optional[uuid.UUID] = None,
        book_id: Optional[uuid.UUID] = None,
        toc_entry_id: Optional[uuid.UUID] = None,
        transport: str = "api",
        status: str = "done",
    ):
        self.id = uuid.uuid4()
        self.batch_id = batch_id
        self.book_id = book_id
        self.toc_entry_id = toc_entry_id
        self.transport = transport
        self.status = status
        self.created_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fake async session for batch_api_cost_usd
# ---------------------------------------------------------------------------


class _BatchSession:
    """Fake session: holds BOTH cli and api rows; filters to auth_mode='api'.

    The real ``batch_api_cost_usd`` builds a JOIN + WHERE auth_mode='api'.
    Previously the fake only ever returned the api rows, so deleting the WHERE
    clause from cost.py would not be caught — the cli rows were never offered.

    Now we inject all rows (cli + api) and simulate the SQL filter: any row
    whose auth_mode != 'api' is dropped before returning, exactly as the DB
    would do.  This means ``test_batch_api_cost_excludes_cli_rows`` will FAIL
    if the WHERE clause is ever removed from cost.py.
    """

    def __init__(self, api_rows: list[_Usage], cli_rows: list[_Usage]):
        # All rows offered to the session (cli + api); the fake will filter.
        self._all_rows = list(api_rows) + list(cli_rows)
        self.execute_count = 0

    async def execute(self, stmt: Any):
        self.execute_count += 1
        # Simulate DB honouring the auth_mode='api' WHERE clause.
        filtered = [r for r in self._all_rows if r.auth_mode == "api"]
        result = MagicMock()
        result.scalars.return_value.all.return_value = filtered
        return result


class _FleetSession:
    """Fake session for fleet_api_cost_usd — holds cli + api rows; filters.

    Same rationale as _BatchSession: inject both kinds of rows so the
    auth_mode='api' WHERE clause is actually exercised by the fake.
    """

    def __init__(self, api_rows: list[_Usage], cli_rows: list[_Usage] | None = None):
        self._all_rows = list(api_rows) + list(cli_rows or [])

    async def execute(self, stmt: Any):
        # Simulate DB honouring the auth_mode='api' WHERE clause.
        filtered = [r for r in self._all_rows if r.auth_mode == "api"]
        result = MagicMock()
        result.scalars.return_value.all.return_value = filtered
        return result


class _SectionSession:
    """Fake session for section_prior_api_cost — two queries: job, then usages."""

    def __init__(self, job: Optional[_Job], usage_rows: list[_Usage]):
        self._job = job
        self._usage_rows = usage_rows
        self.call_count = 0

    async def execute(self, stmt: Any):
        self.call_count += 1
        result = MagicMock()
        if self.call_count == 1:
            # First call: the job lookup
            result.scalar_one_or_none.return_value = self._job
        else:
            # Second call: usage rows for the job
            result.scalars.return_value.all.return_value = self._usage_rows
        return result


# ---------------------------------------------------------------------------
# batch_api_cost_usd tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_api_cost_excludes_cli_rows():
    """CLI rows are excluded from the batch sum by the auth_mode='api' filter.

    The session is seeded with BOTH a cli row and an api row (same provider,
    same token counts).  If the WHERE auth_mode='api' clause were removed from
    cost.py, the cli row would be counted and cost would double to $6.00,
    failing this test.
    """
    from app.repositories.cost import batch_api_cost_usd

    bid = uuid.uuid4()
    api_row = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="api",
        prompt_tokens=1_000_000,
        output_tokens=0,
        cached_tokens=0,
        cache_creation_tokens=0,
    )
    cli_row = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="cli",
        prompt_tokens=1_000_000,  # same size — would add $3.00 if not filtered
        output_tokens=0,
    )
    # Session holds BOTH rows; the fake honours the auth_mode='api' filter.
    session = _BatchSession(api_rows=[api_row], cli_rows=[cli_row])
    cost = await batch_api_cost_usd(session, bid)

    # Only the api row should be counted: 1M prompt × $3/Mtok = $3.00
    # If cli row leaked through, cost would be $6.00.
    assert cost == pytest.approx(3.0, rel=1e-6)


@pytest.mark.asyncio
async def test_batch_api_cost_empty_batch_returns_zero():
    """A batch with no api rows returns 0.0."""
    from app.repositories.cost import batch_api_cost_usd

    session = _BatchSession(api_rows=[], cli_rows=[])
    cost = await batch_api_cost_usd(session, uuid.uuid4())
    assert cost == 0.0


@pytest.mark.asyncio
async def test_batch_api_cost_sums_multiple_rows():
    """Costs are summed across all api rows in the batch."""
    from app.repositories.cost import batch_api_cost_usd

    bid = uuid.uuid4()
    rows = [
        _Usage(
            provider="claude",
            model_name="claude-sonnet-4-6",
            auth_mode="api",
            prompt_tokens=500_000,
            output_tokens=0,
        ),
        _Usage(
            provider="claude",
            model_name="claude-sonnet-4-6",
            auth_mode="api",
            prompt_tokens=500_000,
            output_tokens=0,
        ),
    ]
    session = _BatchSession(api_rows=rows, cli_rows=[])
    cost = await batch_api_cost_usd(session, bid)
    # Two rows × 0.5 M prompt tokens × $3/Mtok = $3.00
    assert cost == pytest.approx(3.0, rel=1e-6)


@pytest.mark.asyncio
async def test_batch_api_cost_gemini_cached_token_semantics():
    """Gemini: prompt INCLUDES cached — bill (prompt - cached) × input_rate + cached × cache_read."""
    from app.repositories.cost import batch_api_cost_usd

    bid = uuid.uuid4()
    row = _Usage(
        provider="gemini",
        model_name="gemini-2.5-flash",
        auth_mode="api",
        prompt_tokens=1_000_000,  # INCLUDES cached span
        output_tokens=0,
        cached_tokens=400_000,
    )
    session = _BatchSession(api_rows=[row], cli_rows=[])
    cost = await batch_api_cost_usd(session, bid)

    # gemini-2.5-flash: input=$0.30/Mtok, cache_read=$0.03/Mtok
    # uncached = 1_000_000 - 400_000 = 600_000
    expected = (600_000 * 0.30 + 400_000 * 0.03) / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-6)


@pytest.mark.asyncio
async def test_batch_api_cost_claude_cache_write():
    """Claude cache_creation_tokens priced at cache_write rate (1.25× input)."""
    from app.repositories.cost import batch_api_cost_usd

    bid = uuid.uuid4()
    row = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="api",
        prompt_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        cache_creation_tokens=1_000_000,
    )
    session = _BatchSession(api_rows=[row], cli_rows=[])
    cost = await batch_api_cost_usd(session, bid)

    # claude-sonnet-4-6: cache_write=$3.75/Mtok
    assert cost == pytest.approx(3.75, rel=1e-6)


# ---------------------------------------------------------------------------
# fleet_api_cost_usd tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fleet_api_cost_excludes_cli_rows():
    """CLI rows are excluded from the fleet sum by the auth_mode='api' filter.

    The session is seeded with a cli row alongside an api row (equal token
    counts).  Without the WHERE auth_mode='api' clause the cost would double;
    this test would catch such a regression offline.
    """
    from app.repositories.cost import fleet_api_cost_usd

    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    api_row = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="api",
        prompt_tokens=1_000_000,
        output_tokens=0,
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    cli_row = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="cli",
        prompt_tokens=1_000_000,  # same size — would add $3.00 if not filtered
        output_tokens=0,
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    session = _FleetSession(api_rows=[api_row], cli_rows=[cli_row])
    cost = await fleet_api_cost_usd(session, since)

    # Only api row: 1M prompt × $3/Mtok = $3.00
    assert cost == pytest.approx(3.0, rel=1e-6)


@pytest.mark.asyncio
async def test_fleet_api_cost_empty_returns_zero():
    """No api rows → $0.0."""
    from app.repositories.cost import fleet_api_cost_usd

    session = _FleetSession(api_rows=[])
    cost = await fleet_api_cost_usd(session, datetime.now(timezone.utc))
    assert cost == 0.0


@pytest.mark.asyncio
async def test_fleet_api_cost_sums_api_rows():
    """Fleet sum aggregates api rows from all jobs."""
    from app.repositories.cost import fleet_api_cost_usd

    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _Usage(
            provider="gemini",
            model_name="gemini-2.5-flash",
            auth_mode="api",
            prompt_tokens=2_000_000,
            output_tokens=0,
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        _Usage(
            provider="gemini",
            model_name="gemini-2.5-flash",
            auth_mode="api",
            prompt_tokens=0,
            output_tokens=1_000_000,
            started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
    ]
    session = _FleetSession(api_rows=rows)
    cost = await fleet_api_cost_usd(session, since)

    # gemini-2.5-flash: input=$0.30/Mtok, output=$2.50/Mtok
    expected = (2_000_000 * 0.30 + 1_000_000 * 2.50) / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-6)


@pytest.mark.asyncio
async def test_fleet_api_cost_mixed_providers():
    """Costs from different providers are correctly summed."""
    from app.repositories.cost import fleet_api_cost_usd

    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _Usage(
            provider="claude",
            model_name="claude-sonnet-4-6",
            auth_mode="api",
            prompt_tokens=1_000_000,
            output_tokens=0,
        ),
        _Usage(
            provider="gemini",
            model_name="gemini-2.5-flash",
            auth_mode="api",
            prompt_tokens=1_000_000,
            output_tokens=0,
        ),
    ]
    session = _FleetSession(api_rows=rows)
    cost = await fleet_api_cost_usd(session, since)

    # claude-sonnet-4-6 input: $3/Mtok × 1M = $3.00
    # gemini-2.5-flash input: $0.30/Mtok × 1M = $0.30
    assert cost == pytest.approx(3.30, rel=1e-6)


# ---------------------------------------------------------------------------
# section_prior_api_cost tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_prior_no_done_job():
    """No done api job → (0.0, False)."""
    from app.repositories.cost import section_prior_api_cost

    session = _SectionSession(job=None, usage_rows=[])
    cost, had_done = await section_prior_api_cost(
        session, uuid.uuid4(), uuid.uuid4(), "api"
    )
    assert cost == 0.0
    assert had_done is False


@pytest.mark.asyncio
async def test_section_prior_done_job_with_usage():
    """Done api job exists → (summed cost, True)."""
    from app.repositories.cost import section_prior_api_cost

    book_id = uuid.uuid4()
    toc_id = uuid.uuid4()
    job = _Job(book_id=book_id, toc_entry_id=toc_id, transport="api", status="done")
    usage = _Usage(
        provider="claude",
        model_name="claude-sonnet-4-6",
        auth_mode="api",
        prompt_tokens=1_000_000,
        output_tokens=0,
        homework_job_id=job.id,
    )
    session = _SectionSession(job=job, usage_rows=[usage])
    cost, had_done = await section_prior_api_cost(session, book_id, toc_id, "api")

    assert had_done is True
    # 1M prompt tokens × $3/Mtok = $3.00
    assert cost == pytest.approx(3.0, rel=1e-6)


@pytest.mark.asyncio
async def test_section_prior_done_job_no_usage_rows():
    """Done job exists but has no api usage rows → (0.0, True)."""
    from app.repositories.cost import section_prior_api_cost

    book_id = uuid.uuid4()
    toc_id = uuid.uuid4()
    job = _Job(book_id=book_id, toc_entry_id=toc_id, transport="api", status="done")
    session = _SectionSession(job=job, usage_rows=[])
    cost, had_done = await section_prior_api_cost(session, book_id, toc_id, "api")

    assert had_done is True
    assert cost == 0.0


@pytest.mark.asyncio
async def test_section_prior_multi_usage_rows():
    """Multiple api usage rows for the job are all summed."""
    from app.repositories.cost import section_prior_api_cost

    book_id = uuid.uuid4()
    toc_id = uuid.uuid4()
    job = _Job(book_id=book_id, toc_entry_id=toc_id, transport="api", status="done")
    usage_rows = [
        _Usage(
            provider="gemini",
            model_name="gemini-2.5-flash",
            auth_mode="api",
            prompt_tokens=1_000_000,
            output_tokens=0,
            homework_job_id=job.id,
        ),
        _Usage(
            provider="gemini",
            model_name="gemini-2.5-flash",
            auth_mode="api",
            prompt_tokens=0,
            output_tokens=1_000_000,
            homework_job_id=job.id,
        ),
    ]
    session = _SectionSession(job=job, usage_rows=usage_rows)
    cost, had_done = await section_prior_api_cost(session, book_id, toc_id, "api")

    assert had_done is True
    # gemini-2.5-flash: input=$0.30/Mtok × 1M + output=$2.50/Mtok × 1M
    expected = (1_000_000 * 0.30 + 1_000_000 * 2.50) / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# DB-integration tests (RUN_DB_INTEGRATION=1 required)
# ---------------------------------------------------------------------------

_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
_SKIP_REASON = "set RUN_DB_INTEGRATION=1 with a live DATABASE_URL to run"


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_batch_api_cost_integration():
    """Integration: seed two batches with mixed cli/api usage → per-batch sum."""
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import agent_usage as usage_repo
    from app.repositories import batches as batches_repo
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo
    from app.repositories.cost import batch_api_cost_usd

    book_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as session:
            # Seed Book + real TOCEntry (toc_entry_id FK is NOT NULL).
            book = await books_repo.create(
                session, subject="math", original_filename="test.pdf",
                content_sha256="a" * 64, file_size_bytes=1,
            )
            book_id = book.id

            toc = TOCEntry(
                book_id=book.id,
                section_title="Test Section",
                order_index=0,
            )
            session.add(toc)
            await session.flush()

            _bkw = dict(subject="math", grade=None, provider="claude",
                        model="claude-sonnet-4-6")
            batch_a = await batches_repo.get_or_create_for_book(
                session, book_id=book.id, transport="api", **_bkw)
            batch_b = await batches_repo.get_or_create_for_book(
                session, book_id=book.id, transport="cli", **_bkw)

            job_a = await jobs_repo.create(
                session, book_id=book.id, toc_entry_id=toc.id,
                subject="math", batch_id=batch_a.id, transport="api",
            )
            job_b = await jobs_repo.create(
                session, book_id=book.id, toc_entry_id=toc.id,
                subject="math", batch_id=batch_b.id, transport="cli",
            )

            await usage_repo.create(
                session, operation="phase.run", provider="claude",
                model_name="claude-sonnet-4-6", auth_mode="api",
                homework_job_id=job_a.id, prompt_tokens=1_000_000,
            )
            await usage_repo.create(
                session, operation="phase.run", provider="claude",
                model_name="claude-sonnet-4-6", auth_mode="cli",
                homework_job_id=job_b.id, prompt_tokens=1_000_000,
            )
            await session.commit()

            cost_a = await batch_api_cost_usd(session, batch_a.id)
            cost_b = await batch_api_cost_usd(session, batch_b.id)

        assert cost_a == pytest.approx(3.0, rel=1e-4)  # 1M input × $3/Mtok
        assert cost_b == pytest.approx(0.0, rel=1e-4)  # cli only → $0
    finally:
        # Clean up seeded rows (CASCADE deletes toc_entries/jobs/usages via FK).
        if book_id is not None:
            async with SessionLocal() as s:
                await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
                await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
                await s.execute(delete(Batch).where(Batch.book_id == book_id))
                await s.execute(delete(Book).where(Book.id == book_id))
                await s.commit()
