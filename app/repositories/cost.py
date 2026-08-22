"""Cost ledger — read-only queries over agent_usages.

Every function SELECTs api-mode usage rows and sums ``pricing.cost_usd``
in Python (not SQL). The per-provider cached-token semantics that ``cost_usd``
encodes are non-trivial (gemini prompt INCLUDES cached; claude is disjoint),
so the pricing logic must live in Python, not SQL aggregation.

**Ordinary Fleet cost and regeneration cost are separate ledgers.** A revision
job (``revision_of_job_id IS NOT NULL``) re-runs a lesson the operator already
paid for, deliberately, under an approved campaign budget. So:

* :func:`section_prior_api_cost` — the never-pay-twice / rebill warning — must
  exclude revisions. Quoting a regeneration's spend as "this section already
  cost $X" would either wave through or block an ordinary re-launch on a number
  that has nothing to do with ordinary generation;
* :func:`campaign_actual_api_cost_usd` is the opposite read: a campaign's real
  spend is exactly the api usage of its own revision jobs;
* :func:`fleet_api_cost_usd` deliberately counts BOTH. It backs the fleet-wide
  daily $ cap, and a revision spends real money on the same credential;
* :func:`batch_api_cost_usd` needs no filter at all — a revision may not belong
  to a batch (``ck_homework_jobs_revision_no_batch``), so batch scoping already
  excludes it, in the database rather than by convention.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_usage import AgentUsage
from app.models.homework_job import HomeworkJob
from app.models.regeneration_target import RegenerationTarget
from app.services import pricing


def _row_usage(row: AgentUsage) -> dict:
    """Extract the token fields from an ORM row into the dict pricing.cost_usd expects."""
    return {
        "prompt_tokens": int(row.prompt_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cached_tokens": int(row.cached_tokens or 0),
        "cache_creation_tokens": int(row.cache_creation_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
    }


async def batch_api_cost_usd(session: AsyncSession, batch_id: UUID) -> float:
    """Total API spend (USD) for every usage row belonging to a batch.

    Joins agent_usages → homework_jobs on homework_job_id, filters to
    rows whose job is in the given batch AND whose auth_mode is 'api'.
    CLI rows (auth_mode='cli') are always $0 per their pricing entry and
    are excluded to keep the query tight.
    """
    stmt = (
        select(AgentUsage)
        .join(HomeworkJob, AgentUsage.homework_job_id == HomeworkJob.id)
        .where(HomeworkJob.batch_id == batch_id)
        .where(AgentUsage.auth_mode == "api")
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return sum(
        pricing.cost_usd(row.provider, row.model_name, _row_usage(row))
        for row in rows
    )


async def fleet_api_cost_usd(session: AsyncSession, since: datetime) -> float:
    """Total API spend (USD) across ALL jobs since the given cutoff.

    Mirrors the ``started_at >= since`` window used by stats_by_provider,
    filtered to api-mode rows only (cli rows are always $0 and excluded).
    """
    stmt = (
        select(AgentUsage)
        .where(AgentUsage.auth_mode == "api")
        .where(AgentUsage.started_at >= since)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return sum(
        pricing.cost_usd(row.provider, row.model_name, _row_usage(row))
        for row in rows
    )


async def section_prior_api_cost(
    session: AsyncSession,
    book_id: UUID,
    toc_entry_id: UUID,
    transport: str,
) -> tuple[float, bool]:
    """API cost already spent on the latest done api job for this section.

    Returns ``(cost_usd, had_done_job)`` where:
    - ``had_done_job`` is True iff a done api job exists for (book, section, transport).
    - ``cost_usd`` is the sum of api usage across all usages linked to that job.

    Consumed by the never-pay-twice budget gate (Task 7): if a section already
    has a done job on this transport, the budget check can apply the prior cost
    rather than estimating from scratch.

    REVISION jobs are excluded (``revision_of_job_id IS NULL``): they are not
    ordinary Fleet generation. A regenerated lesson is very often the NEWEST
    done job for its section, so without this the warning would quote the
    campaign's spend instead of the original launch's — and a section whose
    only done job is a revision would falsely report ``had_done=True``, i.e.
    "you already generated this", for a lesson Fleet never generated.
    """
    # Find the most recent done ORDINARY api job for this (book, section, transport).
    job_stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.book_id == book_id)
        .where(HomeworkJob.toc_entry_id == toc_entry_id)
        .where(HomeworkJob.transport == transport)
        .where(HomeworkJob.status == "done")
        .where(HomeworkJob.revision_of_job_id.is_(None))
        .order_by(HomeworkJob.created_at.desc())
        .limit(1)
    )
    job = (await session.execute(job_stmt)).scalar_one_or_none()
    if job is None:
        return 0.0, False

    # Sum up api usage rows for that job.
    usage_stmt = (
        select(AgentUsage)
        .where(AgentUsage.homework_job_id == job.id)
        .where(AgentUsage.auth_mode == "api")
    )
    rows = list((await session.execute(usage_stmt)).scalars().all())
    cost = sum(
        pricing.cost_usd(row.provider, row.model_name, _row_usage(row))
        for row in rows
    )
    return cost, True


async def campaign_actual_api_cost_usd(session: AsyncSession, campaign_id: UUID) -> float:
    """Total API spend (USD) actually incurred by one regeneration campaign.

    Joins agent_usages → homework_jobs → regeneration_targets and keeps only
    api rows of the campaign's own REVISION jobs — the mirror image of
    ``section_prior_api_cost``'s exclusion. This is what the canary screen
    compares against the estimate before bulk approval.

    Two things it does NOT need to special-case, because the writers already
    guarantee them: copied phases clone no usage rows (only row-level
    provenance), and the copied-extract marker
    (``agent.record_cached_lesson_extract``) is recorded with ``auth_mode='cli'``
    and zero tokens, so the api filter drops it. Neither can inflate the
    campaign's real cost. A Notion publication retry writes no usage row at all.
    """
    stmt = (
        select(AgentUsage)
        .join(HomeworkJob, AgentUsage.homework_job_id == HomeworkJob.id)
        .join(
            RegenerationTarget,
            HomeworkJob.regeneration_target_id == RegenerationTarget.id,
        )
        .where(RegenerationTarget.campaign_id == campaign_id)
        .where(HomeworkJob.revision_of_job_id.is_not(None))
        .where(AgentUsage.auth_mode == "api")
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return sum(
        pricing.cost_usd(row.provider, row.model_name, _row_usage(row))
        for row in rows
    )
