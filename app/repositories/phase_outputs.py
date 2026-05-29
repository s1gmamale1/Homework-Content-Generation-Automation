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


async def create_or_reset(
    session: AsyncSession,
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    prompt_hash: str,
    model_name: str,
    status: str = "pending",
) -> PhaseOutput:
    """Create a new phase_outputs row, or hard-reset an existing one for
    (job_id, phase_name).

    Used when a job is reclaimed and retried after the worker died mid-phase:
    the orphan sweep in ``main.lifespan`` only marks pre-existing phase rows
    as ``failed``, leaving the unique constraint
    ``uq_phase_output_job_order`` (job_id, phase_order) intact. A naive
    ``create()`` on the retry would then crash with ``UniqueViolationError``.

    On reset, the audit trail is preserved (same row id, FK references
    survive) but all per-attempt fields are cleared so the phase looks
    identical to a fresh row in the ``pending`` state.
    """
    existing = await session.scalar(
        select(PhaseOutput).where(
            PhaseOutput.job_id == job_id,
            PhaseOutput.phase_name == phase_name,
        )
    )
    if existing is not None:
        existing.phase_order = phase_order
        existing.prompt_hash = prompt_hash
        existing.model_name = model_name
        existing.status = status
        existing.output_md = None
        existing.tokens_input = None
        existing.tokens_output = None
        existing.error_message = None
        existing.started_at = None
        existing.completed_at = None
        await session.flush()
        return existing
    return await create(
        session,
        job_id=job_id,
        phase_name=phase_name,
        phase_order=phase_order,
        prompt_hash=prompt_hash,
        model_name=model_name,
        status=status,
    )


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


async def find_latest_extract(
    session: AsyncSession,
    *,
    toc_entry_id: UUID,
    prompt_hash: str,
) -> Optional[PhaseOutput]:
    """Most-recent successful `extract` phase for this (section, prompt-hash).

    Used as a cross-job cache: if we've already extracted the lesson context
    for this section under the same builtin extract prompt, reuse the output
    instead of re-running Gemini.
    """
    from app.models import HomeworkJob

    stmt = (
        select(PhaseOutput)
        .join(HomeworkJob, HomeworkJob.id == PhaseOutput.job_id)
        .where(
            PhaseOutput.phase_name == "extract",
            PhaseOutput.status == "done",
            PhaseOutput.prompt_hash == prompt_hash,
            PhaseOutput.output_md.is_not(None),
            HomeworkJob.toc_entry_id == toc_entry_id,
        )
        .order_by(PhaseOutput.completed_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
