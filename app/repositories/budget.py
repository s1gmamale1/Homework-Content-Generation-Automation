"""Repository for the fleet-level budget_state singleton (Task 5 / C4).

The table has exactly one row (id=1, enforced by CHECK(id=1) and seeded in
migration 0032_budget_state). Workers read `get_state` once per claim
attempt to check the api_paused_at flag; if non-NULL no api-spending job
is claimed by any worker in the fleet.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, case, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget_state import BudgetState


async def get_state(session: AsyncSession) -> BudgetState:
    """Return the singleton BudgetState row (id=1).

    Always exists — seeded by migration 0032_budget_state. Raises if missing
    (indicates a broken migration state).
    """
    row = await session.get(BudgetState, 1)
    if row is None:
        raise RuntimeError(
            "budget_state singleton (id=1) is missing — run 'alembic upgrade head'"
        )
    return row


async def set_api_paused(
    session: AsyncSession,
    reason: str,
    *,
    cap_usd: Optional[float] = None,
    paused_by: Optional[str] = None,
) -> None:
    """Set the fleet-level api pause with a timestamp and reason string.

    Idempotent: calling again while already paused just refreshes the timestamp
    and overwrites the reason.

    `cap_usd`/`paused_by` are the pause's provenance (migration 0062). The
    STRICTEST claimant keeps the record: while the pause is already held for
    the same reason under a lower (stricter) cap, a looser worker's re-stamp
    refreshes the timestamp but does NOT take ownership — otherwise the looser
    worker could hand itself the right to clear a stricter host's pause.
    """
    held_same_reason = and_(
        BudgetState.api_paused_at.is_not(None),
        BudgetState.api_paused_reason == reason,
        BudgetState.api_paused_cap_usd.is_not(None),
    )
    keep_held = (
        held_same_reason
        if cap_usd is None
        else and_(held_same_reason, BudgetState.api_paused_cap_usd <= cap_usd)
    )
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(
            api_paused_at=func.now(),
            api_paused_reason=reason,
            api_paused_cap_usd=case(
                (keep_held, BudgetState.api_paused_cap_usd), else_=cap_usd
            ),
            api_paused_by=case(
                (keep_held, BudgetState.api_paused_by), else_=paused_by
            ),
        )
    )


async def clear_api_paused(session: AsyncSession) -> None:
    """Clear the fleet-level api pause unconditionally (all columns NULL).

    The OPERATOR escape hatch — it ignores provenance on purpose. The budget
    monitor uses `clear_api_pause_if_entitled` instead, which refuses to relax
    a stricter host's decision.
    """
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(api_paused_at=None, api_paused_reason=None,
                api_paused_cap_usd=None, api_paused_by=None)
    )


async def clear_api_pause_if_entitled(
    session: AsyncSession,
    *,
    reason: str,
    worker_cap_usd: float,
    worker_host: str,
) -> bool:
    """Automatic (worker-driven) clear of the fleet cap pause, guarded in SQL.

    Same fleet-safety rule as `batches_repo.clear_cap_pause`: the clear only
    fires when this worker is not relaxing another host's decision — no
    provenance recorded (pre-0062), or this worker is on the deciding host, or
    its own daily cap is at least as strict as the recorded one. Returns True
    iff the pause was actually cleared.
    """
    entitled = [
        BudgetState.api_paused_cap_usd.is_(None),
        func.split_part(func.coalesce(BudgetState.api_paused_by, ""), ":", 1)
        == worker_host,
    ]
    if worker_cap_usd > 0:
        entitled.append(BudgetState.api_paused_cap_usd >= worker_cap_usd)
    result = await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .where(BudgetState.api_paused_at.is_not(None))
        .where(BudgetState.api_paused_reason == reason)
        .where(or_(*entitled))
        .values(api_paused_at=None, api_paused_reason=None,
                api_paused_cap_usd=None, api_paused_by=None)
    )
    return (result.rowcount or 0) > 0


async def raise_version_floor(
    session: AsyncSession, *, version: int, stamped_by: str
) -> bool:
    """Raise-only floor stamp (the main.lifespan auto-stamp). The WHERE guard
    makes a stale-process restart a no-op — the floor can never go DOWN through
    this path. Returns True iff the floor actually moved. Caller commits."""
    result = await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .where(
            or_(
                BudgetState.min_worker_version.is_(None),
                BudgetState.min_worker_version < version,
            )
        )
        .values(
            min_worker_version=version,
            min_worker_version_stamped_by=stamped_by,
            min_worker_version_stamped_at=func.now(),
        )
    )
    return (result.rowcount or 0) > 0


async def set_version_floor(
    session: AsyncSession, *, version: Optional[int], stamped_by: str
) -> None:
    """Unconditional floor set/clear — the OPERATOR escape hatch (unlike the
    lifespan auto-stamp, this may LOWER or clear). Caller commits."""
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(
            min_worker_version=version,
            min_worker_version_stamped_by=stamped_by,
            min_worker_version_stamped_at=func.now(),
        )
    )
