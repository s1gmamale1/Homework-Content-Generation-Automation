"""Fleet worker registry: register/heartbeat a worker row + derive liveness.

`is_online` is a pure helper (DB-free, unit-tested). `upsert_heartbeat` is the
register-or-beat (Postgres upsert). `list_with_liveness` is the head-side view.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker import WorkerNode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_online(last_heartbeat: Optional[datetime], *, stale_after_seconds: int) -> bool:
    """True if the heartbeat is fresh enough. None (never beat) -> offline."""
    if last_heartbeat is None:
        return False
    return last_heartbeat >= _utcnow() - timedelta(seconds=stale_after_seconds)


async def upsert_heartbeat(session: AsyncSession, pc_id: str, *, status: str = "online") -> None:
    """Register the worker (first call) or refresh its heartbeat (every call)."""
    now = _utcnow()
    stmt = pg_insert(WorkerNode).values(pc_id=pc_id, last_heartbeat=now, status=status)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_={"last_heartbeat": now, "status": status},
    )
    await session.execute(stmt)


async def list_with_liveness(session: AsyncSession, *, stale_after_seconds: int) -> list[dict]:
    """Every worker row + a derived `online` flag, ordered by pc_id."""
    rows = (await session.execute(select(WorkerNode).order_by(WorkerNode.pc_id))).scalars().all()
    return [
        {
            "pc_id": w.pc_id,
            "last_heartbeat": w.last_heartbeat,
            "status": w.status,
            "notes": w.notes,
            "online": is_online(w.last_heartbeat, stale_after_seconds=stale_after_seconds),
        }
        for w in rows
    ]
