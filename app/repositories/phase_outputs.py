from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PhaseOutput


async def create(
    session: AsyncSession,
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    prompt_hash: str,
    model_name: str,
    status: str = "pending",
) -> PhaseOutput:
    po = PhaseOutput(
        job_id=job_id,
        phase_name=phase_name,
        phase_order=phase_order,
        prompt_hash=prompt_hash,
        model_name=model_name,
        status=status,
    )
    session.add(po)
    await session.flush()
    return po


async def list_for_job(session: AsyncSession, job_id: UUID) -> list[PhaseOutput]:
    stmt = (
        select(PhaseOutput)
        .where(PhaseOutput.job_id == job_id)
        .order_by(PhaseOutput.phase_order)
    )
    return list((await session.execute(stmt)).scalars().all())


async def set_status(
    session: AsyncSession,
    phase_output_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    output_md: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    po = await session.get(PhaseOutput, phase_output_id)
    if po is None:
        return
    po.status = status
    if started_at is not None:
        po.started_at = started_at
    if completed_at is not None:
        po.completed_at = completed_at
    if output_md is not None:
        po.output_md = output_md
    if tokens_input is not None:
        po.tokens_input = tokens_input
    if tokens_output is not None:
        po.tokens_output = tokens_output
    if error_message is not None:
        po.error_message = error_message


async def list_running_for_sweep(session: AsyncSession) -> list[PhaseOutput]:
    stmt = select(PhaseOutput).where(PhaseOutput.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())
