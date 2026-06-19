"""Cost ledger — read-only queries over agent_usages.

All three functions SELECT api-mode usage rows and sum ``pricing.cost_usd``
in Python (not SQL). The per-provider cached-token semantics that ``cost_usd``
encodes are non-trivial (gemini prompt INCLUDES cached; claude is disjoint),
so the pricing logic must live in Python, not SQL aggregation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_usage import AgentUsage
from app.models.homework_job import HomeworkJob
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
    """
    # Find the most recent done api job for this (book, section, transport).
    job_stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.book_id == book_id)
        .where(HomeworkJob.toc_entry_id == toc_entry_id)
        .where(HomeworkJob.transport == transport)
        .where(HomeworkJob.status == "done")
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
