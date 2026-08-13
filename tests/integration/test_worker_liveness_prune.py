"""Real-DB proof that registry cleanup cannot evict a live-but-slow worker.

The 2026-08-13 roster flap (38 -> 27 -> 34 -> 16 -> 22 in minutes) came from
`prune_stale` letting any peer DELETE any row whose heartbeat had merely aged
out — and heartbeats aged out because the beat lost the race for one of the
worker's four pooled connections, not because the host was gone. These tests
drive the real SQL against real Postgres and pin the new contract:

  * a late row is STAMPED `offline`, not deleted;
  * the worker's own next beat undoes that stamp with no re-register;
  * a pending `draining` signal is never clobbered by the marker;
  * a row that still owns a `running` job is never deleted, however stale;
  * a too-small prune window is clamped UP, so a config typo cannot re-arm
    the flap;
  * a genuinely dead row is still reaped past the long horizon.

Run:
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:5432/scratch_db \\
    uv run pytest tests/integration/test_worker_liveness_prune.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# Sentinel pc_ids — namespaced so a shared scratch DB never collides.
_PC_LATE = "test-liveness:60001"
_PC_RETURNS = "test-liveness:60002"
_PC_DRAINING = "test-liveness:60003"
_PC_BUSY = "test-liveness:60004"
_PC_CLAMP = "test-liveness:60005"
_PC_DEAD = "test-liveness:60006"


# ── helpers ────────────────────────────────────────────────────────────────


async def _cleanup_workers(*pc_ids: str) -> None:
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    async with SessionLocal() as s:
        await s.execute(delete(WorkerNode).where(WorkerNode.pc_id.in_(list(pc_ids))))
        await s.commit()


async def _seed_worker(pc_id: str, *, age_seconds: int, status: str = "online") -> None:
    """Register `pc_id` and back-date its heartbeat by `age_seconds`."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    async with SessionLocal() as s:
        await workers_repo.upsert_heartbeat(s, pc_id, status=status)
        await s.execute(
            text(
                "UPDATE workers SET last_heartbeat = now() - "
                "make_interval(secs => :age) WHERE pc_id = :pc"
            ),
            {"age": age_seconds, "pc": pc_id},
        )
        await s.commit()


async def _row(pc_id: str):
    from app.db import SessionLocal
    from app.models.worker import WorkerNode

    async with SessionLocal() as s:
        return await s.scalar(select(WorkerNode).where(WorkerNode.pc_id == pc_id))


# ── 1. late worker: marked offline, NOT deleted ────────────────────────────


@pytest.mark.asyncio
async def test_a_late_worker_is_marked_offline_not_deleted():
    """The exact incident shape: a live host 700s behind on its beats.

    Old behaviour: `prune_stale(older_than_seconds=600)` DELETED this row and
    the worker re-registered — the flap. New behaviour: it is stamped offline
    and survives.
    """
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_LATE, age_seconds=700)

        async with SessionLocal() as s:
            marked = await workers_repo.mark_stale_offline(s, older_than_seconds=600)
            deleted = await workers_repo.prune_stale(
                s, older_than_seconds=3600, min_seconds=600
            )
            await s.commit()

        assert marked >= 1, "a 700s-stale row must be marked offline"
        assert deleted == 0, "nothing may be DELETED at the offline horizon"

        row = await _row(_PC_LATE)
        assert row is not None, (
            "a merely-late worker must KEEP its registry row — deleting it is "
            "what produced the leave/rejoin flap"
        )
        assert row.status == "offline"
    finally:
        await _cleanup_workers(_PC_LATE)


@pytest.mark.asyncio
async def test_the_offline_mark_is_undone_by_the_workers_next_beat():
    """No re-register round trip: one ordinary beat restores the row."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_RETURNS, age_seconds=700)
        async with SessionLocal() as s:
            await workers_repo.mark_stale_offline(s, older_than_seconds=600)
            await s.commit()
        assert (await _row(_PC_RETURNS)).status == "offline"

        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, _PC_RETURNS)
            await s.commit()

        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == _PC_RETURNS]
        assert mine and mine[0]["status"] == "online"
        assert mine[0]["online"] is True
    finally:
        await _cleanup_workers(_PC_RETURNS)


# ── 2. drain signal survives the marker ────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_stale_offline_never_clobbers_a_pending_drain():
    """`draining` is an operator instruction the worker has not read yet.
    Overwriting it would silently cancel a drain — the lever used to force
    config reloads."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_DRAINING, age_seconds=700, status="draining")

        async with SessionLocal() as s:
            await workers_repo.mark_stale_offline(s, older_than_seconds=600)
            await s.commit()

        async with SessionLocal() as s:
            status = await workers_repo.get_status(s, _PC_DRAINING)
        assert status == "draining", (
            f"the drain signal must survive the offline marker; got {status!r}"
        )
    finally:
        await _cleanup_workers(_PC_DRAINING)


@pytest.mark.asyncio
async def test_mark_stale_offline_is_idempotent():
    """A second sweep must not re-UPDATE rows it already marked."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_LATE, age_seconds=700)
        async with SessionLocal() as s:
            first = await workers_repo.mark_stale_offline(s, older_than_seconds=600)
            await s.commit()
        async with SessionLocal() as s:
            second = await workers_repo.mark_stale_offline(s, older_than_seconds=600)
            await s.commit()
        assert first >= 1
        assert second == 0, "already-offline rows must not be re-written every sweep"
    finally:
        await _cleanup_workers(_PC_LATE)


# ── 3. prune guards ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_stale_spares_a_worker_that_still_owns_a_running_job():
    """A row whose pc_id owns a `running` job is alive by definition, however
    stale its beat looks. Once the job leaves `running` the guard clears and
    the same call reaps it."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo
    from app.repositories import workers as workers_repo

    book_id = None
    try:
        await _seed_worker(_PC_BUSY, age_seconds=7200)

        async with SessionLocal() as s:
            book = Book(
                subject="math-algebra",
                original_filename="liveness-prune.pdf",
                content_sha256="b" * 64,
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
                    "UPDATE homework_jobs SET status='running', claimed_at=now(), "
                    "claimed_by=:pc WHERE id=:id"
                ),
                {"pc": _PC_BUSY, "id": job.id},
            )
            await s.commit()
            book_id = book.id
            job_id = job.id

        async with SessionLocal() as s:
            deleted = await workers_repo.prune_stale(s, older_than_seconds=3600)
            await s.commit()
        assert deleted == 0
        assert await _row(_PC_BUSY) is not None, (
            "a worker still running a job must never be deleted from the registry"
        )

        # Release the job — the guard must clear itself.
        async with SessionLocal() as s:
            await s.execute(
                text("UPDATE homework_jobs SET status='done' WHERE id=:id"),
                {"id": job_id},
            )
            await s.commit()
        async with SessionLocal() as s:
            deleted = await workers_repo.prune_stale(s, older_than_seconds=3600)
            await s.commit()
        assert deleted >= 1
        assert await _row(_PC_BUSY) is None
    finally:
        await _cleanup_workers(_PC_BUSY)
        if book_id is not None:
            async with SessionLocal() as s:
                await s.execute(
                    delete(HomeworkJob).where(HomeworkJob.book_id == book_id)
                )
                await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
                await s.execute(delete(Book).where(Book.id == book_id))
                await s.commit()


@pytest.mark.asyncio
async def test_prune_stale_clamps_a_too_small_window_up_to_the_floor():
    """A misconfigured `WORKER_REGISTRY_PRUNE_SECONDS` must not be able to
    delete a worker that is only seconds late.

    RED on the old code: `prune_stale` had no floor, so a 60s window deleted
    this 700s-stale-but-live row outright.
    """
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_CLAMP, age_seconds=700)

        async with SessionLocal() as s:
            deleted = await workers_repo.prune_stale(
                s, older_than_seconds=60, min_seconds=3600
            )
            await s.commit()

        assert deleted == 0
        assert await _row(_PC_CLAMP) is not None, (
            "the safety floor must override a too-small configured window"
        )
    finally:
        await _cleanup_workers(_PC_CLAMP)


@pytest.mark.asyncio
async def test_a_genuinely_dead_row_is_still_reaped_past_the_long_horizon():
    """Conservative must not mean never: retention still works."""
    from app.db import SessionLocal
    from app.repositories import workers as workers_repo

    try:
        await _seed_worker(_PC_DEAD, age_seconds=7200)

        async with SessionLocal() as s:
            deleted = await workers_repo.prune_stale(
                s, older_than_seconds=3600, min_seconds=600
            )
            await s.commit()

        assert deleted >= 1
        assert await _row(_PC_DEAD) is None
    finally:
        await _cleanup_workers(_PC_DEAD)
