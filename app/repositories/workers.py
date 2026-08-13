"""Fleet worker registry: register/heartbeat a worker row + derive liveness.

`is_online` is a pure helper (DB-free, unit-tested). `upsert_heartbeat` is the
register-or-beat (Postgres upsert). `list_with_liveness` is the head-side view.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, delete, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.homework_job import HomeworkJob
from app.models.worker import WorkerNode

# Statuses `mark_stale_offline` must never overwrite.
#   - "draining" is an OPERATOR signal the worker has not read yet. Clobbering
#     it would silently cancel a drain (the lever used to force config reloads).
#   - "offline" is already the marker — re-writing it every sweep is pure churn.
_OFFLINE_MARK_PROTECTED_STATUSES = ("draining", "offline")

# A worker owning a job in one of these statuses is demonstrably alive, no
# matter how stale its beat looks; `prune_stale` refuses to delete its row.
_LIVE_JOB_STATUSES = ("running", "cancelling")


async def lock_host_shared(session: AsyncSession, hostname: str) -> None:
    """Host-scoped Postgres advisory lock, SHARED form (BE-02 book-lock
    pattern, host key namespace instead of book).

    Taken by the job-claim path so worker job-claiming on a host serializes
    against a credential scrub for that same host (`lock_host_exclusive`,
    taken by the scrub path) instead of interleaving with it. Concurrent
    SHARED holders never block each other — multiple claims on the same
    host can proceed in parallel — but a SHARED holder blocks (and is
    blocked by) the EXCLUSIVE holder.

    `pg_advisory_xact_lock_shared` is transaction-scoped: it releases
    automatically on commit/rollback, so the caller MUST take it inside the
    same transaction that performs (and commits) its claim — never take it
    and then return without committing/rolling back soon after.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(:key))"),
        {"key": f"host:{hostname}"},
    )


async def lock_host_exclusive(session: AsyncSession, hostname: str) -> None:
    """Host-scoped Postgres advisory lock, EXCLUSIVE form (BE-02 book-lock
    pattern, host key namespace instead of book).

    Taken by the SA-key scrub path at the top of its transaction, before it
    clears the shared credential files for that host. Blocks (and is
    blocked by) any `lock_host_shared` holder (a job claim in flight on
    this host), and blocks any other `lock_host_exclusive` holder — so two
    concurrent scrubs, or a scrub racing a claim, for the same host always
    serialize instead of interleaving. Same key namespace as
    `lock_host_shared` (`f"host:{hostname}"`) so the two forms actually
    contend with each other; transaction-scoped, released on
    commit/rollback.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"host:{hostname}"},
    )


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


async def mark_stale_offline(
    session: AsyncSession, *, older_than_seconds: int
) -> int:
    """Stamp `status='offline'` on rows whose heartbeat aged past the window.

    The NON-DESTRUCTIVE half of registry cleanup, and the one that runs first.
    A row marked offline keeps its pc_id, its capabilities blob and its last
    known heartbeat, so the dashboard can show "this host went quiet" instead
    of the card simply vanishing — and the instant the worker beats again,
    `upsert_heartbeat` flips it straight back to `online` with no re-register
    round trip. Deleting used to be the only tool here, which is what turned a
    merely-slow worker into a leave/rejoin flap (see `prune_stale`).

    Two exclusions, both load-bearing:
      - `draining` is never touched: it is an operator instruction the worker
        has not read yet, and overwriting it would silently cancel a drain.
      - `offline` is never re-written: without this the sweep would UPDATE
        every dead row on every pass forever.

    Compares against the DB clock, same as the heartbeat stamps.
    """
    result = await session.execute(
        update(WorkerNode)
        .where(
            WorkerNode.last_heartbeat
            < func.now() - timedelta(seconds=older_than_seconds)
        )
        .where(WorkerNode.status.not_in(_OFFLINE_MARK_PROTECTED_STATUSES))
        .values(status="offline")
    )
    return result.rowcount or 0


async def prune_stale(
    session: AsyncSession,
    *,
    older_than_seconds: int,
    min_seconds: int | None = None,
) -> int:
    """Delete worker rows long past the retention horizon. LAST resort.

    pc_id is hostname:pid — a dead process never beats again, so its row is
    eventually pure dashboard clutter (every restart minted a new permanent
    card). Graceful-shutdown deregistration rarely fires in practice
    (kills/crashes skip it), so something must reap. Safe to delete: job
    attribution lives in homework_jobs.claimed_by, a plain string with no FK
    to this table. Compares against the DB clock, same as the heartbeat stamps.

    2026-08-13 — this is also the statement that erased LIVE workers. Any peer
    may delete any row, so a host that merely lost the race for one of its
    four pooled connections got its registry row deleted and re-registered:
    the observed 38 -> 27 -> 34 -> 16 -> 22 roster flap. Two guards now make
    that impossible:

      `min_seconds` — a floor the caller cannot undercut. The window is
      CLAMPED UP (never down) to it, so a misconfigured
      `WORKER_REGISTRY_PRUNE_SECONDS` can shorten nothing. The worker passes
      its own offline horizon here; see `worker._delete_after_seconds`.

      live-job anti-join — a row whose pc_id still owns a `running` or
      `cancelling` job is alive by definition (a dead worker's jobs are
      released by `jobs_repo.reclaim_stuck_jobs` within
      `reclaim_stale_seconds`, so this guard always clears itself).

    Deletion is deliberately NOT the first response to staleness any more:
    `mark_stale_offline` runs much earlier and non-destructively.
    """
    window = older_than_seconds
    if min_seconds is not None and window < min_seconds:
        window = min_seconds

    owns_live_job = exists(
        select(HomeworkJob.id).where(
            HomeworkJob.claimed_by == WorkerNode.pc_id,
            HomeworkJob.status.in_(_LIVE_JOB_STATUSES),
        )
    )
    result = await session.execute(
        delete(WorkerNode).where(
            and_(
                WorkerNode.last_heartbeat < func.now() - timedelta(seconds=window),
                ~owns_live_job,
            )
        )
    )
    return result.rowcount or 0


async def deregister(session: AsyncSession, pc_id: str) -> None:
    """Remove this worker's own row on graceful shutdown. Best-effort bonus —
    `mark_stale_offline` + `prune_stale` are what actually guarantee cleanup.
    A worker may only ever delete its OWN row here; peer deletion lives in
    `prune_stale` and is deliberately slow and guarded."""
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
            "capabilities": w.capabilities,
            "online": is_online(
                w.last_heartbeat, now=db_now, stale_after_seconds=stale_after_seconds
            ),
        }
        for w in rows
    ]
