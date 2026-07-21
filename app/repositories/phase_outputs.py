from datetime import datetime
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import select, update
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
        existing.validation_warnings = None
        existing.judge_status = None
        existing.solver_status = None
        existing.provider = None
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
    validation_warnings: Optional[list] = None,
    provider: Optional[str] = None,
    judge_status: Optional[str] = None,
    solver_status: Optional[str] = None,
    guard: bool = True,
) -> bool:
    """Set a phase row's status. With ``guard`` (default), a ``done`` phase is
    frozen — protects the resumable set (``_done_phase_md``) from a
    cancel-race clobber. Returns True iff a row was updated."""
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if output_md is not None:
        values["output_md"] = output_md
    if tokens_input is not None:
        values["tokens_input"] = tokens_input
    if tokens_output is not None:
        values["tokens_output"] = tokens_output
    if error_message is not None:
        values["error_message"] = error_message
    if validation_warnings is not None:
        values["validation_warnings"] = validation_warnings
    if provider is not None:
        values["provider"] = provider
    if judge_status is not None:
        values["judge_status"] = judge_status
    if solver_status is not None:
        values["solver_status"] = solver_status
    stmt = update(PhaseOutput).where(PhaseOutput.id == phase_output_id)
    if guard:
        stmt = stmt.where(PhaseOutput.status != "done")
    result = await session.execute(stmt.values(**values))
    return result.rowcount > 0


async def list_running_for_sweep(session: AsyncSession) -> list[PhaseOutput]:
    stmt = select(PhaseOutput).where(PhaseOutput.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())


# The synthetic error main.lifespan's boot sweep stamps on every
# pending/running phase row before the startup reclaim runs. The
# reconciliation predicate matches THIS exact prose — single source,
# never duplicate the string (orphan-phase-reconciliation-1).
ORPHANED_RESTART_MESSAGE = "orphaned: worker restarted"


async def reset_abandoned_phases(
    session: AsyncSession,
    job_ids: Sequence[UUID],
    *,
    phase_names: Optional[list[str]] = None,
    status: str,
    error_message: Optional[str] = None,
    source_statuses: Sequence[str] = ("pending", "running"),
    include_orphan_failed: bool = False,
) -> int:
    """Reset a batch of jobs' abandoned phase rows (queue-correctness-1 +
    orphan-phase-reconciliation-1). 'done' rows are always untouched;
    'failed' rows are untouched unless they carry the synthetic
    ORPHANED_RESTART_MESSAGE and include_orphan_failed=True — genuine
    failure evidence is never rewritten.

    status='pending' (job requeued/parked — rows are WAITING, error cleared)
    or status='failed' (job terminal — error_message recorded).
    phase_names=None means every phase of the job; [] is a no-op (the #109
    scheduler contract). Empty job_ids is a no-op before any session use.
    source_statuses may only narrow within {'pending', 'running'} — 'done' is
    always frozen and 'failed' rows are reachable ONLY via
    include_orphan_failed's marker equality, never wholesale."""
    # Real raises, not asserts — python -O strips asserts, and these guards
    # ARE the preservation contract (PR #110 round-3; closes
    # reset-abandoned-status-assert-1).
    if status not in ("pending", "failed"):
        raise ValueError(f"status must be 'pending' or 'failed', got {status!r}")
    if not set(source_statuses) <= {"pending", "running"}:
        raise ValueError(
            f"source_statuses may only narrow within pending/running "
            f"(got {tuple(source_statuses)!r}) — 'done' is always frozen and "
            f"'failed' rows are reachable ONLY via include_orphan_failed's "
            f"marker equality, never wholesale"
        )
    if not job_ids:
        return 0
    if phase_names is not None and not phase_names:
        return 0
    from sqlalchemy import func as sa_func, or_
    values: dict = {"status": status}
    if status == "failed":
        values["error_message"] = error_message
        values["completed_at"] = sa_func.now()
    else:
        values["error_message"] = None
        values["completed_at"] = None
    eligible = PhaseOutput.status.in_(tuple(source_statuses))
    if include_orphan_failed:
        eligible = or_(
            eligible,
            (PhaseOutput.status == "failed")
            & (PhaseOutput.error_message == ORPHANED_RESTART_MESSAGE),
        )
    stmt = (
        update(PhaseOutput)
        .where(PhaseOutput.job_id.in_(list(job_ids)), eligible)
        .values(**values)
    )
    if phase_names is not None:
        stmt = stmt.where(PhaseOutput.phase_name.in_(phase_names))
    result = await session.execute(stmt)
    return result.rowcount


async def find_latest_extract(
    session: AsyncSession,
    *,
    toc_entry_id: UUID,
    prompt_hash: str,
    provider: str,
    model: str,
) -> Optional[PhaseOutput]:
    """Most-recent successful `extract` phase for this section.

    Used as a cross-job cache: if we've already extracted the lesson context
    for this section under the same builtin extract prompt AND the same
    producing ``(provider, model)``, reuse the output instead of re-running
    the agent. Reuse now requires same ``(toc_entry_id, prompt_hash, provider,
    model)`` because the extract role is per-job — a gemini-produced extract
    must not be served to a job requesting a claude extract. A legacy row with
    ``provider IS NULL`` never matches (safe miss → forces a fresh extract).
    Transport is deliberately NOT part of the key: the auth mode does not
    change the extract output.
    """
    from app.models import HomeworkJob

    stmt = (
        select(PhaseOutput)
        .join(HomeworkJob, HomeworkJob.id == PhaseOutput.job_id)
        .where(
            PhaseOutput.phase_name == "extract",
            PhaseOutput.status == "done",
            PhaseOutput.prompt_hash == prompt_hash,
            PhaseOutput.provider == provider,
            PhaseOutput.model_name == model,
            PhaseOutput.output_md.is_not(None),
            HomeworkJob.toc_entry_id == toc_entry_id,
        )
        .order_by(PhaseOutput.completed_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
