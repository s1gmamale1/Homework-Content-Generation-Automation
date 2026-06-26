"""Fleet worker registry: register/heartbeat a worker row + derive liveness.

`is_online` is a pure helper (DB-free, unit-tested). `upsert_heartbeat` is the
register-or-beat (Postgres upsert). `list_with_liveness` is the head-side view.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, exists, func, select, update
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


async def upsert_heartbeat(
    session: AsyncSession,
    pc_id: str,
    *,
    status: str = "online",
    capabilities: dict | None = None,
) -> None:
    """Register the worker (first call) or refresh its heartbeat (every call).
    Stamps `last_heartbeat` with the DB clock (func.now()) so every worker's
    beat is on the single head-DB clock regardless of its host clock.

    `capabilities` is published on the first (full) beat; subsequent status-only
    beats pass `capabilities=None` and must NOT overwrite the stored blob — only
    the first/explicit write sets the column (no-clobber guard)."""
    stmt = pg_insert(WorkerNode).values(
        pc_id=pc_id,
        last_heartbeat=func.now(),
        status=status,
        capabilities=capabilities,
    )
    # Always update last_heartbeat + status; only update capabilities when
    # explicitly provided (capabilities=None means "don't touch the existing blob").
    set_: dict = {"last_heartbeat": func.now(), "status": status}
    if capabilities is not None:
        set_["capabilities"] = capabilities
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_=set_,
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


async def has_live_workers(session: AsyncSession, *, stale_after_seconds: int) -> bool:
    """True iff at least one workers row has a heartbeat within the staleness window.
    Uses a single EXISTS query against the DB clock — never the host clock."""
    cutoff = func.now() - timedelta(seconds=stale_after_seconds)
    result = await session.scalar(
        select(exists().where(WorkerNode.last_heartbeat >= cutoff))
    )
    return bool(result)


async def get_status(session: AsyncSession, pc_id: str) -> str | None:
    """Return the `status` string for the given pc_id, or None if no such row."""
    return await session.scalar(
        select(WorkerNode.status).where(WorkerNode.pc_id == pc_id)
    )


async def set_status(session: AsyncSession, pc_id: str, status: str) -> bool:
    """UPDATE `status` for pc_id. Does NOT touch `last_heartbeat`. Returns True
    if a row was matched (pc_id known), False if pc_id unknown — the endpoint
    uses the False case to send a 404."""
    result = await session.execute(
        update(WorkerNode).where(WorkerNode.pc_id == pc_id).values(status=status)
    )
    return (result.rowcount or 0) > 0


async def aggregate_fleet_capability(
    session: AsyncSession,
    *,
    stale_after_seconds: int,
) -> dict:
    """Union of capabilities across all online workers.

    Selects worker rows whose heartbeat is within the staleness window (same
    predicate as `has_live_workers`, evaluated against the DB clock). If no
    workers are online returns the fail-open shape so the launcher can surface
    a "no workers" banner without crashing. A NULL-capabilities row counts
    toward `workers_online` (the worker IS online) but contributes no true
    flags — the banner fires only at ZERO online workers.

    Return shape:
      zero online → {"online": False, "workers_online": 0, "cli": {}, "api": {}}
      else        → {"online": True, "workers_online": n,
                     "cli": {provider: bool}, "api": {provider: bool}}
    """
    cutoff = func.now() - timedelta(seconds=stale_after_seconds)
    rows = (
        await session.execute(
            select(WorkerNode).where(WorkerNode.last_heartbeat >= cutoff)
        )
    ).scalars().all()

    workers_online = len(rows)
    if workers_online == 0:
        return {"online": False, "workers_online": 0, "cli": {}, "api": {}}

    cli_union: dict[str, bool] = {}
    api_union: dict[str, bool] = {}

    for w in rows:
        blob = w.capabilities or {}
        for provider, val in (blob.get("cli") or {}).items():
            cli_union[provider] = cli_union.get(provider, False) or bool(val)
        for provider, val in (blob.get("api") or {}).items():
            api_union[provider] = api_union.get(provider, False) or bool(val)

    return {
        "online": True,
        "workers_online": workers_online,
        "cli": cli_union,
        "api": api_union,
    }


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
