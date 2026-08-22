"""Create the immutable revision job for one regeneration target.

One function matters here — :func:`create_revision_job`. It turns a planned
target into a complete, runnable homework job whose *unchanged* phases are
already present as ``done`` rows copied verbatim from the source snapshot, so
the ordinary pipeline resumes into it and re-runs only what the campaign asked
for.

Three rules drive every line below.

**Provenance is the immediate source's.** ``book_id``, ``toc_entry_id``,
``subject`` and ``output_language`` are copied from the job this revision is
derived from — never from a campaign-wide value. A lineage is scoped by
``(toc_entry_id, output_language)`` and one campaign may legitimately hold a UZ
*and* an RU target for the same lesson, so there is no campaign-wide language to
read; reading one would publish an RU revision of a UZ lesson.

**The contract is copied, never re-resolved.** Every provider/model/transport
and the session-limit strategy come from the stored
:class:`~app.schemas.regeneration_contract.ResolvedLaunchContract`, read back
through ``ensure_resolved`` — which *verifies* and refuses, and deliberately
cannot resolve. ``launch_canary`` and ``approve_canary`` call this function at
two wall-clock moments separated by a human gate, so a second read of the
mutable ``launch_defaults`` row or of the fleet-wide
``settings.session_limit_strategy`` would give one immutable campaign two
meanings: the canary evidence would stop describing the bulk run. This module
therefore never imports ``resolve_session_limit_strategy`` or ``launch_defaults``.

**A copy is free.** Copied phase rows carry a ``copied_from_phase_output_id``
provenance link and no ``agent_usages`` row of their own; the single exception
is the existing zero-cost ``<cache>`` ``lesson.extract`` marker, written through
``agent.record_cached_lesson_extract`` exactly as the pipeline's cross-job
extract reuse does. No paid usage row is ever cloned — doing so would double
every historical dollar in ``/agent/stats`` and in the campaign's own
before/after comparison.

Completeness is judged by ``regeneration_planner.validate_complete_snapshot``
and by nothing else, and the phase plan is read back through
``RegenerationPhasePlan.from_json`` and by nothing else.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.homework_job import HomeworkJob
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import regeneration_targets as targets_repo
from app.schemas.regeneration_contract import ResolvedLaunchContract, ensure_resolved
from app.services import agent
from app.services.regeneration_planner import (
    EXTRACT_PHASE,
    RegenerationPhasePlan,
    validate_complete_snapshot,
)

__all__ = [
    "IncompleteSnapshot",
    "MissingRevisionSource",
    "RevisionSnapshotError",
    "TargetNotEligible",
    "TargetNotFound",
    "create_revision_job",
    "ensure_resolved",
]


class RevisionSnapshotError(RuntimeError):
    """Base for every refusal below. Callers may catch this one class."""


class TargetNotFound(RevisionSnapshotError):
    """No such target row."""


class MissingRevisionSource(RevisionSnapshotError):
    """The target has no ``source_job_id`` — there is nothing to copy from.

    Refused on that exact predicate, not on a missing job row: an explicitly
    ordered child-first purge NULLs the link (``fk_regeneration_targets_
    source_job_id`` is ``SET NULL``) while the reporting row, its consumed
    version and its Notion page id survive. The target therefore still exists
    and still looks live — but its snapshot is gone.
    """


class TargetNotEligible(RevisionSnapshotError):
    """The target is already terminal (``published``/``abandoned``).

    A terminal target has released its lineage; giving it a new revision job
    would resurrect a campaign the operator already closed.
    """


class IncompleteSnapshot(RevisionSnapshotError):
    """The source job is not a complete, usable homework snapshot.

    Carries ``validate_complete_snapshot``'s own reasons verbatim — including
    the phase-order drift reason, which is why this is a refusal rather than a
    silent renumber: copying a row to a *different* ``phase_order`` than the one
    it was generated at would hand the pipeline a snapshot whose order no longer
    matches the deployed flow.
    """

    def __init__(self, job_id: UUID, reasons: tuple[str, ...]):
        self.job_id = job_id
        self.reasons = reasons
        super().__init__(
            f"source job {job_id} is not a complete snapshot: {'; '.join(reasons)}"
        )


# The exact `phase_outputs` columns a copied row carries forward. Listed here
# (and consumed by `phase_repo.copy_for_revision`) so the set is stated ONCE.
# Deliberately absent: `id` and `job_id` (the copy is a new row on a new job),
# `claim_token` (the SOURCE run's fencing token — copying it would let a
# long-dead worker's token look current on a live revision) and
# `copied_from_phase_output_id` (set to the source row, not copied from it).
COPIED_PHASE_COLUMNS = phase_repo.REVISION_COPIED_COLUMNS


async def create_revision_job(
    session: AsyncSession,
    *,
    target_id: UUID,
    launch_contract,
    start_offset_seconds: int = 0,
) -> HomeworkJob:
    """Create (or return) the one revision job for ``target_id``.

    Idempotent by construction: the target is locked ``FOR UPDATE`` and, if a
    revision job is already linked, it is returned untouched — in particular its
    ``scheduled_at`` is NOT re-staggered, so a retried wave cannot push an
    already-queued revision further into the future.

    ``launch_contract`` is the campaign's STORED contract (the raw JSONB dict is
    accepted). It goes through ``ensure_resolved``, which refuses anything still
    carrying ``'inherit'``, a null role provider/model or a null content model
    rather than repairing it.

    Commits before returning. The zero-cost ``<cache>`` extract marker is written
    by ``agent.record_cached_lesson_extract`` through its OWN session and would
    hit a foreign-key violation against an uncommitted phase row, so the unit of
    work has to close first. That also makes a wave crash-safe: every revision
    created so far is complete and the idempotent path resumes the rest.
    """
    contract: ResolvedLaunchContract = ensure_resolved(launch_contract)

    target = await targets_repo.get_target_for_update(session, target_id)
    if target is None:
        raise TargetNotFound(f"regeneration target {target_id} not found")

    existing = await targets_repo.revision_job_for_target(session, target_id=target_id)
    if existing is not None:
        return existing

    if target.terminal_at is not None:
        raise TargetNotEligible(
            f"regeneration target {target_id} is terminal "
            f"(status={target.status!r}) — it has no live revision to create"
        )
    if target.source_job_id is None:
        raise MissingRevisionSource(
            f"regeneration target {target_id} has no source_job_id — its source "
            "job was purged; there is no snapshot to copy from"
        )

    source = await jobs_repo.get(session, target.source_job_id)
    if source is None:
        # Belt and braces: the FK makes this unreachable while the id is set.
        raise MissingRevisionSource(
            f"regeneration target {target_id} references missing source job "
            f"{target.source_job_id}"
        )

    plan = RegenerationPhasePlan.from_json(target.phase_plan)
    source_rows = await phase_repo.list_for_job(session, source.id)
    validation = validate_complete_snapshot(subject=source.subject, rows=source_rows)
    if not validation.usable:
        raise IncompleteSnapshot(source.id, validation.reasons)

    by_phase = {row.phase_name: row for row in source_rows}

    job = await jobs_repo.create(
        session,
        # ── provenance: the IMMEDIATE source's, never a campaign-wide value ──
        book_id=source.book_id,
        toc_entry_id=source.toc_entry_id,
        subject=source.subject,
        output_language=source.output_language,
        # ── the approved contract, copied verbatim ──
        provider=contract.provider,
        model=contract.model,
        transport=contract.transport,
        extract_transport=contract.extract_transport,
        judge_transport=contract.judge_transport,
        solver_transport=contract.solver_transport,
        extract_provider=contract.extract_provider,
        extract_model=contract.extract_model,
        judge_provider=contract.judge_provider,
        judge_model=contract.judge_model,
        solver_provider=contract.solver_provider,
        solver_model=contract.solver_model,
        session_limit_strategy=contract.session_limit_strategy,
        # ── what makes it a revision ──
        revision_of_job_id=source.id,
        regeneration_target_id=target.id,
        # A revision is never a Fleet batch member and never runs a job-level
        # phase subset: the phase plan lives on the target, and the phases it
        # does not regenerate are seeded below as `done` rows instead.
        batch_id=None,
        selected_phases=None,
        kind="homework",
        start_offset_seconds=start_offset_seconds,
    )

    copied_extract: Optional[tuple[UUID, UUID]] = None
    for phase_name in plan.copied_phases:
        src = by_phase.get(phase_name)
        if src is None:
            # Unreachable: validate_complete_snapshot already required a usable
            # row for every canonical phase, and copied_phases ⊆ canonical.
            raise IncompleteSnapshot(
                source.id, (f"missing phase row: {phase_name}",)
            )
        row = await phase_repo.copy_for_revision(
            session,
            job_id=job.id,
            source=src,
            # The VERIFIED canonical order from the validation above — never a
            # re-derived or renumbered one.
            phase_order=validation.canonical_order[phase_name],
        )
        if phase_name == EXTRACT_PHASE:
            copied_extract = (row.id, src.id)

    await session.commit()

    if copied_extract is not None:
        # The existing zero-cost path, unchanged (`agent.py` is not this task's
        # file): a $0 `lesson.extract` row marking the reuse, with the source
        # job/phase ids in its envelope. No PAID usage row is ever cloned.
        new_phase_id, source_phase_id = copied_extract
        try:
            await agent.record_cached_lesson_extract(
                homework_job_id=job.id,
                phase_output_id=new_phase_id,
                source_job_id=source.id,
                source_phase_output_id=source_phase_id,
            )
        except Exception:  # noqa: BLE001 — accounting marker, never fatal
            logger.warning(
                f"revision {job.id}: cached-extract marker not recorded "
                "(accounting only)", exc_info=True,
            )
    return job
