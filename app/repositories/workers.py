"""Fleet worker registry: register/heartbeat a worker row + derive liveness.

`is_online` is a pure helper (DB-free, unit-tested). `upsert_heartbeat` is the
register-or-beat (Postgres upsert). `list_with_liveness` is the head-side view.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker import WorkerNode


def is_online(
    last_heartbeat: Optional[datetime],
    *,
    now: datetime,
    stale_after_seconds: int,
) -> bool:
    """True if the heartbeat is fresh enough, measured against `now`. None
    (never beat) -> offline. `now` is injected (the DB clock on the head-side
    path) so liveness never mixes a DB-stamped heartbeat with a host clock."""
    if last_heartbeat is None:
        return False
    return last_heartbeat >= now - timedelta(seconds=stale_after_seconds)


async def upsert_heartbeat(session: AsyncSession, pc_id: str, *, status: str = "online") -> None:
    """Register the worker (first call) or refresh its heartbeat (every call).
    Stamps `last_heartbeat` with the DB clock (func.now()) so every worker's
    beat is on the single head-DB clock regardless of its host clock."""
    stmt = pg_insert(WorkerNode).values(pc_id=pc_id, last_heartbeat=func.now(), status=status)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_={"last_heartbeat": func.now(), "status": status},
    )
    await session.execute(stmt)


async def prune_stale(session: AsyncSession, *, older_than_seconds: int) -> int:
    """Delete worker rows whose heartbeat is older than the retention window.
    pc_id is hostname:pid — a dead process never beats again, so its row is
    pure dashboard clutter (every restart minted a new permanent card). This
    is the LOAD-BEARING cleanup: graceful-shutdown deregistration rarely fires
    in practice (kills/crashes skip it). Safe to delete: job attribution lives
    in homework_jobs.claimed_by, a plain string with no FK to this table.
    Compares against the DB clock, same as the heartbeat stamps."""
    result = await session.execute(
        delete(WorkerNode).where(
            WorkerNode.last_heartbeat
            < func.now() - timedelta(seconds=older_than_seconds)
        )
    )
    return result.rowcount or 0


async def deregister(session: AsyncSession, pc_id: str) -> None:
    """Remove this worker's own row on graceful shutdown. Best-effort bonus —
    `prune_stale` is what actually guarantees cleanup."""
    await session.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc_id))


async def list_with_liveness(session: AsyncSession, *, stale_after_seconds: int) -> list[dict]:
    """Every worker row + a derived `online` flag, ordered by pc_id. Liveness is
    evaluated against the DB clock (db_now) so it matches the DB-stamped beats."""
    db_now = await session.scalar(select(func.now()))
    rows = (await session.execute(select(WorkerNode).order_by(WorkerNode.pc_id))).scalars().all()
    return [
        {
            "pc_id": w.pc_id,
            "last_heartbeat": w.last_heartbeat,
            "status": w.status,
            "notes": w.notes,
            "online": is_online(
                w.last_heartbeat, now=db_now, stale_after_seconds=stale_after_seconds
            ),
        }
        for w in rows
    ]
