import uuid
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_lease_event import JobLeaseEvent


async def append_event(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    claim_token: Optional[uuid.UUID],
    event_type: str,
    owner: Optional[str] = None,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    stmt = pg_insert(JobLeaseEvent).values(
        job_id=job_id,
        claim_token=claim_token,
        event_type=event_type,
        owner=owner,
        actor=actor,
        reason=reason,
    ).on_conflict_do_nothing(constraint="uq_job_lease_events_job_token_event")
    await session.execute(stmt)
