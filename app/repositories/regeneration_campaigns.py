"""Campaign primitives shared by every regeneration lane.

Module-level async functions taking the session first, like every other
repository here. Only the common primitives live in this task; Tasks 7-8 extend
this module sequentially with their own reads.
"""
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regeneration_campaign import RegenerationCampaign


async def create_campaign(
    session: AsyncSession,
    *,
    selection_spec: dict,
    requested_phases: list,
    excluded_phases: list,
    launch_contract: dict,
    status: str = "draft",
    refresh_extraction: bool = False,
    exclusion_acknowledged: bool = False,
    canary_size: int = 1,
    estimated_cost_low_usd: Optional[float] = None,
    estimated_cost_high_usd: Optional[float] = None,
    app_git_revision: Optional[str] = None,
) -> RegenerationCampaign:
    """Insert a draft campaign. The JSON columns are the campaign's frozen
    specification — write them here and never mutate them again."""
    campaign = RegenerationCampaign(
        status=status,
        selection_spec=selection_spec,
        requested_phases=requested_phases,
        excluded_phases=excluded_phases,
        launch_contract=launch_contract,
        refresh_extraction=refresh_extraction,
        exclusion_acknowledged=exclusion_acknowledged,
        canary_size=canary_size,
        estimated_cost_low_usd=estimated_cost_low_usd,
        estimated_cost_high_usd=estimated_cost_high_usd,
        app_git_revision=app_git_revision,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def get_campaign(
    session: AsyncSession, campaign_id: UUID
) -> Optional[RegenerationCampaign]:
    """Plain read, no lock — reports and read-only API responses.

    ``populate_existing`` for the same reason the locked read below has it:
    ``SessionLocal`` is ``expire_on_commit=False``, so a session that already
    holds this campaign would otherwise be handed its own stale copy.
    """
    return await session.scalar(
        select(RegenerationCampaign)
        .where(RegenerationCampaign.id == campaign_id)
        .execution_options(populate_existing=True)
    )


async def get_campaign_for_update(
    session: AsyncSession, campaign_id: UUID
) -> Optional[RegenerationCampaign]:
    """Row-locked read (``FOR UPDATE``). Every decision that reads the campaign
    status and then writes it — approve, reject, cancel, completion rollup —
    must go through this, or two operators can both act on the same snapshot.

    ``populate_existing=True`` is load-bearing, not tidiness. ``SessionLocal``
    is built with ``expire_on_commit=False``, so a session that already loaded
    this campaign keeps that Python object across commits and the identity map
    hands it straight back. The lock would then be taken while the caller reads
    a status that has since moved — approving a campaign an operator cancelled
    a moment ago — and the compare-and-set that follows would silently do
    nothing. The refresh makes the locked read mean what it says: the row as it
    is NOW, under our lock.
    """
    return await session.scalar(
        select(RegenerationCampaign)
        .where(RegenerationCampaign.id == campaign_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def set_campaign_status(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    new_status: str,
    expected_statuses: Sequence[str],
    canary_launched_at=None,
    approved_at=None,
    rejected_at=None,
    cancel_requested_at=None,
    completed_at=None,
    rejected_reason: Optional[str] = None,
    cancel_requested_reason: Optional[str] = None,
) -> bool:
    """Fenced compare-and-set: writes only if the row is still in one of
    ``expected_statuses``. Returns True when it moved, False when someone else
    got there first (a stale UI, a duplicate click, a racing worker).

    Only the audit fields passed non-None are written, so a caller can never
    blank an earlier decision's timestamp by omitting it.
    """
    values: dict = {"status": new_status, "updated_at": func.now()}
    for column, value in (
        ("canary_launched_at", canary_launched_at),
        ("approved_at", approved_at),
        ("rejected_at", rejected_at),
        ("cancel_requested_at", cancel_requested_at),
        ("completed_at", completed_at),
        ("rejected_reason", rejected_reason),
        ("cancel_requested_reason", cancel_requested_reason),
    ):
        if value is not None:
            values[column] = value

    result = await session.execute(
        update(RegenerationCampaign)
        .where(
            RegenerationCampaign.id == campaign_id,
            RegenerationCampaign.status.in_(list(expected_statuses)),
        )
        .values(**values)
    )
    return result.rowcount == 1
