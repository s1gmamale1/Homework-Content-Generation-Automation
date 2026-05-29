from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Integer

from app.models import AgentUsage


async def create(
    session: AsyncSession,
    *,
    operation: str,
    model_name: Optional[str] = None,
    book_id: Optional[UUID] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    provider: str = "gemini",
    # Provider-neutral token counts (preferred names).
    prompt_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    total_tokens: int = 0,
    raw_envelope: Optional[dict[str, Any]] = None,
    # ─── Legacy kwargs (kept for the gemini service which is being
    # ─── retired in a later wave). Mapped onto the new columns; modality-
    # ─── specific counts (text/image/thoughts) are folded into raw_envelope
    # ─── because the new schema is provider-neutral.
    total_token_count: Optional[int] = None,
    prompt_token_count: Optional[int] = None,
    candidates_token_count: Optional[int] = None,
    cached_content_token_count: Optional[int] = None,
    input_text_token_count: Optional[int] = None,
    input_image_token_count: Optional[int] = None,
    thoughts_token_count: Optional[int] = None,
    usage_metadata: Optional[dict[str, Any]] = None,
    duration: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> AgentUsage:
    # Map legacy kwargs onto the provider-neutral columns. The new names win
    # if both are supplied — that's the common case for new callers.
    if prompt_token_count is not None and prompt_tokens == 0:
        prompt_tokens = prompt_token_count
    if candidates_token_count is not None and output_tokens == 0:
        output_tokens = candidates_token_count
    if cached_content_token_count is not None and cached_tokens == 0:
        cached_tokens = cached_content_token_count
    if total_token_count is not None and total_tokens == 0:
        total_tokens = total_token_count

    # Preserve the dropped modality-specific counts inside raw_envelope so the
    # data isn't lost while the gemini service is still alive.
    envelope = dict(raw_envelope) if raw_envelope else (dict(usage_metadata) if usage_metadata else None)
    if envelope is None and (
        input_text_token_count is not None
        or input_image_token_count is not None
        or thoughts_token_count is not None
    ):
        envelope = {}
    if envelope is not None:
        if input_text_token_count is not None:
            envelope.setdefault("input_text_token_count", input_text_token_count)
        if input_image_token_count is not None:
            envelope.setdefault("input_image_token_count", input_image_token_count)
        if thoughts_token_count is not None:
            envelope.setdefault("thoughts_token_count", thoughts_token_count)

    row = AgentUsage(
        operation=operation,
        model_name=model_name,
        provider=provider,
        book_id=book_id,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        raw_envelope=envelope,
        duration=duration,
        success=success,
        error_message=error_message,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(row)
    await session.flush()
    return row


def _parse_duration_seconds(value: Optional[str]) -> float:
    """Parse the stringified `duration` column into seconds.

    The column stores values like '1.23s' or '4500ms'. Anything else (or
    None) returns 0.0. Cheap pure-Python helper used by `stats_by_provider`
    when summing duration across rows where Postgres can't parse it directly.
    """
    if not value:
        return 0.0
    s = value.strip().lower()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return 0.0


async def stats_by_provider(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[dict]:
    """Aggregate agent_usages rows grouped by provider since a cutoff.

    Returns dicts with: provider, calls, duration_secs, prompt_tokens,
    output_tokens, cached_tokens, success_count.

    A single GROUP BY query covers all four token/count aggregates. The
    `duration` column is a free-form string ('1.23s' / '4500ms'), so we
    pull the per-row values back and sum them in Python — small N (capped
    at the rows in the window) makes this trivial.
    """
    success_int = cast(AgentUsage.success, Integer)
    stmt = (
        select(
            AgentUsage.provider.label("provider"),
            func.count().label("calls"),
            func.coalesce(func.sum(AgentUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AgentUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AgentUsage.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(
                func.sum(case((AgentUsage.success.is_(True), 1), else_=0)),
                0,
            ).label("success_count"),
        )
        .where(AgentUsage.started_at >= since)
        .group_by(AgentUsage.provider)
    )
    # Touch success_int so static analyzers don't whine; it isn't selected
    # because the case() expression above is more portable across dialects.
    _ = success_int

    rows = (await session.execute(stmt)).all()

    # Second tiny query: pull `duration` strings per provider for the same
    # window and sum them in Python (parser handles 's' / 'ms' suffixes).
    dur_stmt = (
        select(AgentUsage.provider, AgentUsage.duration)
        .where(AgentUsage.started_at >= since)
        .where(AgentUsage.duration.is_not(None))
    )
    dur_rows = (await session.execute(dur_stmt)).all()
    duration_by_provider: dict[str, float] = {}
    for provider, duration in dur_rows:
        duration_by_provider[provider] = (
            duration_by_provider.get(provider, 0.0)
            + _parse_duration_seconds(duration)
        )

    return [
        {
            "provider": r.provider,
            "calls": int(r.calls),
            "duration_secs": round(duration_by_provider.get(r.provider, 0.0), 1),
            "prompt_tokens": int(r.prompt_tokens),
            "output_tokens": int(r.output_tokens),
            "cached_tokens": int(r.cached_tokens),
            "success_count": int(r.success_count),
        }
        for r in rows
    ]
