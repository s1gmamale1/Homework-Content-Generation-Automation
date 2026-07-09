"""Repository for the fleet-level budget_state singleton (Task 5 / C4).

The table has exactly one row (id=1, enforced by CHECK(id=1) and seeded in
migration 0032_budget_state). Workers read `get_state` once per claim
attempt to check the api_paused_at flag; if non-NULL no api-spending job
is claimed by any worker in the fleet.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, update
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


async def set_api_paused(session: AsyncSession, reason: str) -> None:
    """Set the fleet-level api pause with a timestamp and reason string.

    Idempotent: calling again while already paused just refreshes the timestamp
    and overwrites the reason.
    """
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(api_paused_at=func.now(), api_paused_reason=reason)
    )


async def clear_api_paused(session: AsyncSession) -> None:
    """Clear the fleet-level api pause (both columns set to NULL)."""
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(api_paused_at=None, api_paused_reason=None)
    )


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
