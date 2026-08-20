"""Reconcile a revision JOB's terminal truth onto its campaign TARGET.

A revision job's status is written by the pipeline/worker/sweeps; a target's
status is campaign truth. They live in different transactions on purpose (a job
must be able to finish even if the regeneration tables are unavailable), so
something has to carry the first onto the second — and it has to survive every
way a job can end, including the ones with no worker involved at all
(``reclaim_stale_cancelling``, ``fail_exhausted_pending_jobs``, a process kill
between the job's terminal commit and this write).

Hence exactly two entry points:

* :func:`reconcile_revision_job` — idempotent, single job, called by the worker
  as the LAST thing in ``_execute_job``'s ``finally`` and by the API when a
  cancel finalizes a pending revision that no worker ever claimed;
* :func:`reconcile_terminal_revision_jobs` — the crash-repair sweep, run as its
  OWN named step (own session, own transaction, own guard) by worker
  maintenance and by startup, and later by campaign actions and each publisher
  pass.

Two rules that are easy to get wrong:

**Never move a target backwards.** ``regeneration_states.can_transition_target``
is the authority. A late reconcile of an already-``publishing`` target must not
reset it to ``publication_pending`` and hand the same irreversible delivery to a
second publisher; a terminal target must not be resurrected at all.

**Never publish an incomplete packet.** ``done`` alone is not enough: the
revision's own phase rows go through ``validate_complete_snapshot`` — the same
single predicate the copy path uses — and a ``done`` job that does not pass it
is a generation failure, not a publication.

Lock order is parent → child (campaign, then target), matching the direction the
campaign-level actions take, so a bulk wave finishing while an operator approves
cannot deadlock.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import _utcnow
from app.models.homework_job import HomeworkJob
from app.models.regeneration_target import RegenerationTarget
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import regeneration_campaigns as campaigns_repo
from app.repositories import regeneration_targets as targets_repo
from app.services.regeneration_planner import validate_complete_snapshot
from app.services.regeneration_states import can_transition_target

__all__ = [
    "TERMINAL_JOB_STATUSES",
    "desired_target_status",
    "reconcile_revision_job",
    "reconcile_terminal_revision_jobs",
]

# Job statuses that mean "this revision will never do any more work".
TERMINAL_JOB_STATUSES = ("done", "failed", "cancelled")
# Target statuses the crash-repair sweep may still be the first to move. Once a
# target has reached a publication state it belongs to the publisher, and once
# it is terminal it belongs to nobody.
_REPAIRABLE_TARGET_STATUSES = ("planned", "generating")

_ABANDON_REASON = "abandon requested before the revision reached a usable snapshot"


def desired_target_status(
    *,
    job_status: str,
    snapshot_usable: bool,
    campaign_approved: bool,
    abandon_requested: bool,
) -> str:
    """Map current job truth onto the target status it implies. Pure.

    ``done`` WITHOUT a usable snapshot deliberately lands in the same bucket as
    ``failed``/``cancelled``: the job finished, so leaving the target
    ``generating`` would strand the campaign forever, and publishing a packet
    with a missing or empty phase is the one outcome regeneration exists to
    prevent.
    """
    if job_status in ("pending", "running", "cancelling"):
        return "generating"
    if job_status == "done" and snapshot_usable:
        return "publication_pending" if campaign_approved else "awaiting_canary_approval"
    return "abandoned" if abandon_requested else "generation_failed"


def _campaign_is_approved(campaign) -> bool:
    """The SAME predicate `trg_regeneration_targets_publication_gate` enforces —
    stated once here so the service and the database cannot disagree about which
    campaigns may release a publication."""
    return (
        campaign is not None
        and campaign.approved_at is not None
        and campaign.status not in ("rejected", "cancelled")
    )


async def _reconcile_one(session: AsyncSession, job_id: UUID) -> bool:
    """Reconcile one job. Returns True when the target actually moved.

    Does NOT commit — the caller owns the transaction boundary (the worker
    commits its own session; the sweep commits per target).
    """
    job = await jobs_repo.get(session, job_id)
    if job is None or job.revision_of_job_id is None:
        return False  # ordinary job (or gone): nothing to reconcile, no session work
    target_id = job.regeneration_target_id
    if target_id is None:
        # Unreachable while `ck_homework_jobs_revision_pair` holds.
        return False

    # Parent → child lock order. The campaign id is read WITHOUT a lock first so
    # the campaign row can be locked before the target, matching the direction
    # campaign-level actions (approve/reject/cancel) take.
    campaign_id = await session.scalar(
        select(RegenerationTarget.campaign_id).where(RegenerationTarget.id == target_id)
    )
    if campaign_id is None:
        return False
    campaign = await campaigns_repo.get_campaign_for_update(session, campaign_id)
    target = await targets_repo.get_target_for_update(session, target_id)
    if target is None or target.terminal_at is not None:
        return False

    snapshot_usable = False
    if job.status == "done":
        rows = await phase_repo.list_for_job(session, job_id)
        snapshot_usable = validate_complete_snapshot(
            subject=job.subject, rows=rows
        ).usable

    desired = desired_target_status(
        job_status=job.status,
        snapshot_usable=snapshot_usable,
        campaign_approved=_campaign_is_approved(campaign),
        abandon_requested=target.abandon_requested_at is not None,
    )
    if desired == target.status:
        return False
    if not can_transition_target(target.status, desired):
        # e.g. a late reconcile of a `done` job whose target the publisher has
        # already claimed. The publisher's state wins; say so and leave it.
        logger.debug(
            f"revision {job_id}: target {target_id} is {target.status!r}; "
            f"job status {job.status!r} implies {desired!r} — leaving it alone"
        )
        return False

    terminal_at = _utcnow() if desired == "abandoned" else None
    released_at: Optional[object] = None
    if desired == "publication_pending" and target.publication_released_at is None:
        # The release stamp is what `ck_regeneration_targets_publication_released`
        # demands, and it is written ONCE: a re-release would restart the
        # publication clock on a version that is already reserved.
        released_at = _utcnow()

    moved = await targets_repo.set_target_status(
        session,
        target_id=target_id,
        new_status=desired,
        expected_statuses=[target.status],
        terminal_at=terminal_at,
        terminal_reason=(
            (target.abandon_requested_reason or _ABANDON_REASON)
            if desired == "abandoned" else None
        ),
        publication_released_at=released_at,
    )
    if moved:
        logger.info(
            f"revision {job_id} ({job.status}) → target {target_id}: "
            f"{target.status} → {desired}"
        )
    return moved


async def reconcile_revision_job(session: AsyncSession, job_id: UUID) -> None:
    """Carry one revision job's current status onto its target. Idempotent.

    A no-op for an ordinary job, a missing job, a target already at (or past)
    the implied status, and a terminal target. The caller commits.
    """
    await _reconcile_one(session, job_id)


async def reconcile_terminal_revision_jobs(session: AsyncSession) -> int:
    """Repair every terminal revision whose target never got the memo.

    Covers the crash between a job's terminal commit and its target update, and
    the two sweeps that write terminal job statuses with no worker involved
    (``reclaim_stale_cancelling``, ``fail_exhausted_pending_jobs``).

    Commits per target so a failure late in a long sweep cannot discard the
    repairs already made. Returns how many targets moved.
    """
    rows = await session.execute(
        select(HomeworkJob.id)
        .join(
            RegenerationTarget,
            RegenerationTarget.id == HomeworkJob.regeneration_target_id,
        )
        .where(
            HomeworkJob.revision_of_job_id.is_not(None),
            HomeworkJob.status.in_(TERMINAL_JOB_STATUSES),
            RegenerationTarget.terminal_at.is_(None),
            RegenerationTarget.status.in_(_REPAIRABLE_TARGET_STATUSES),
        )
        # Deterministic, so a repeated sweep does the same work in the same
        # order and a lock conflict cannot ping-pong between two processes.
        .order_by(HomeworkJob.id)
    )
    moved = 0
    for (job_id,) in rows.all():
        if await _reconcile_one(session, job_id):
            moved += 1
        await session.commit()
    return moved
