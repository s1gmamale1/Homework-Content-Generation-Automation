"""Real-DB proof that the cap-pause guard bites in Postgres, not just in mocks.

The uneven-rollout incident in one test: a stale host (COST_CAP_BATCH_USD=50)
pauses a batch the fleet shares, and a patched host (2000) must NOT be able to
reverse that decision — while a host at least as strict, and the deciding host
itself, still can.

Run (never against edu_copy — the conftest guard refuses production):
  createdb -h 127.0.0.1 -U edu -O edu edu_scratch_cappause
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_cappause \\
    uv run alembic upgrade head
  RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_cappause \\
    uv run python -m pytest tests/integration/test_cap_pause_guard.py -v
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_STALE = "host-a:4242@abc1234"     # COST_CAP_BATCH_USD=50, never rolled forward
_PATCHED = "host-b:777@def5678"    # COST_CAP_BATCH_USD=2000


async def _seed_batch(name: str):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book

    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename=name,
            content_sha256=("c" * 64),
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        batch = Batch(
            id=uuid4(), book_id=book.id, subject="math-algebra",
            provider="gemini", transport="api",
        )
        s.add(batch)
        await s.commit()
        return book.id, batch.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book

    async with SessionLocal() as s:
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_a_looser_host_cannot_reverse_a_stricter_hosts_batch_pause():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    book_id, batch_id = await _seed_batch("cap-pause-guard.pdf")
    try:
        # The stale host trips its $50 cap on a batch that has spent $120.
        async with SessionLocal() as s:
            await batches_repo.pause_batch(
                s, batch_id, "batch-cap", cap_usd=50.0, paused_by=_STALE
            )
            await s.commit()

        # Provenance is on the row, where an operator can read it.
        async with SessionLocal() as s:
            row = await s.get(Batch, batch_id)
            assert row.paused_cap_usd == 50.0
            assert row.paused_by == _STALE

        # The reconcile worklist hands the monitor cap + host, not just the id.
        async with SessionLocal() as s:
            records = await batches_repo.paused_cap_records(s, "batch-cap")
        assert (batch_id, 50.0, _STALE) in records

        # The patched host ($2000) must NOT be able to lift it.
        async with SessionLocal() as s:
            lifted = await batches_repo.clear_cap_pause(
                s, batch_id, reason="batch-cap",
                worker_cap_usd=2000.0, worker_host="host-b",
            )
            await s.commit()
        assert lifted is False, "a looser cap must not reverse a stricter decision"
        async with SessionLocal() as s:
            assert (await s.get(Batch, batch_id)).paused_at is not None

        # Neither may a host that turned the cap off entirely.
        async with SessionLocal() as s:
            lifted = await batches_repo.clear_cap_pause(
                s, batch_id, reason="batch-cap",
                worker_cap_usd=0.0, worker_host="host-c",
            )
            await s.commit()
        assert lifted is False, "cap=0 is no ceiling, not 'no opinion'"

        # A stricter host may (it is not relaxing anything).
        async with SessionLocal() as s:
            lifted = await batches_repo.clear_cap_pause(
                s, batch_id, reason="batch-cap",
                worker_cap_usd=10.0, worker_host="host-c",
            )
            await s.commit()
        assert lifted is True
        async with SessionLocal() as s:
            row = await s.get(Batch, batch_id)
            assert row.paused_at is None and row.paused_cap_usd is None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_the_deciding_host_can_revise_its_own_pause():
    """Self-heal: once the rollout reaches host-a, host-a lifts what host-a set —
    no operator click needed. Its pid/sha have changed since (restart + deploy);
    ownership is per HOST."""
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    book_id, batch_id = await _seed_batch("cap-pause-selfheal.pdf")
    try:
        async with SessionLocal() as s:
            await batches_repo.pause_batch(
                s, batch_id, "batch-cap", cap_usd=50.0, paused_by=_STALE
            )
            await s.commit()

        async with SessionLocal() as s:
            lifted = await batches_repo.clear_cap_pause(
                s, batch_id, reason="batch-cap",
                worker_cap_usd=2000.0, worker_host="host-a",
            )
            await s.commit()
        assert lifted is True
        async with SessionLocal() as s:
            assert (await s.get(Batch, batch_id)).paused_at is None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_the_guard_never_touches_a_manual_pause():
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.repositories import batches as batches_repo

    book_id, batch_id = await _seed_batch("cap-pause-manual.pdf")
    try:
        async with SessionLocal() as s:
            await batches_repo.pause_batch(s, batch_id, "manual")
            await s.commit()

        # Provenance-less, but reason-scoped away: the cap monitor never sees it.
        async with SessionLocal() as s:
            records = await batches_repo.paused_cap_records(s, "batch-cap")
            assert all(r[0] != batch_id for r in records)
            lifted = await batches_repo.clear_cap_pause(
                s, batch_id, reason="batch-cap",
                worker_cap_usd=0.0, worker_host="host-a",
            )
            await s.commit()
        assert lifted is False
        async with SessionLocal() as s:
            row = await s.get(Batch, batch_id)
            assert row.paused_at is not None and row.paused_reason == "manual"

        # The operator escape hatch still works unconditionally.
        async with SessionLocal() as s:
            await batches_repo.unpause_batch(s, batch_id)
            await s.commit()
        async with SessionLocal() as s:
            assert (await s.get(Batch, batch_id)).paused_at is None
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_fleet_gate_keeps_the_strictest_claimant_and_refuses_a_looser_clear():
    from app.db import SessionLocal
    from app.repositories import budget as budget_repo

    try:
        # Stale host trips the $50 daily cap.
        async with SessionLocal() as s:
            await budget_repo.set_api_paused(
                s, "fleet-daily-cap", cap_usd=50.0, paused_by=_STALE
            )
            await s.commit()

        # The patched host is over ITS $2000 cap too and re-stamps — the
        # stricter claimant must keep the record.
        async with SessionLocal() as s:
            await budget_repo.set_api_paused(
                s, "fleet-daily-cap", cap_usd=2000.0, paused_by=_PATCHED
            )
            await s.commit()
        async with SessionLocal() as s:
            state = await budget_repo.get_state(s)
            assert state.api_paused_cap_usd == 50.0
            assert state.api_paused_by == _STALE

        # ...so the patched host cannot clear it either.
        async with SessionLocal() as s:
            lifted = await budget_repo.clear_api_pause_if_entitled(
                s, reason="fleet-daily-cap",
                worker_cap_usd=2000.0, worker_host="host-b",
            )
            await s.commit()
        assert lifted is False
        async with SessionLocal() as s:
            assert (await budget_repo.get_state(s)).api_paused_at is not None

        # The deciding host can, once its own cap is rolled forward.
        async with SessionLocal() as s:
            lifted = await budget_repo.clear_api_pause_if_entitled(
                s, reason="fleet-daily-cap",
                worker_cap_usd=2000.0, worker_host="host-a",
            )
            await s.commit()
        assert lifted is True
        async with SessionLocal() as s:
            state = await budget_repo.get_state(s)
            assert state.api_paused_at is None
            assert state.api_paused_cap_usd is None
            assert state.api_paused_by is None
    finally:
        async with SessionLocal() as s:
            await budget_repo.clear_api_paused(s)
            await s.commit()
