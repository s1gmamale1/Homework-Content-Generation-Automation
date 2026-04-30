from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GeminiUsage


async def create(
    session: AsyncSession,
    *,
    operation: str,
    model_name: Optional[str] = None,
    book_id: Optional[UUID] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    total_token_count: int = 0,
    input_text_token_count: int = 0,
    input_audio_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
    usage_metadata: Optional[dict[str, Any]] = None,
    duration: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> GeminiUsage:
    row = GeminiUsage(
        operation=operation,
        model_name=model_name,
        book_id=book_id,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        total_token_count=total_token_count,
        input_text_token_count=input_text_token_count,
        input_audio_token_count=input_audio_token_count,
        candidates_token_count=candidates_token_count,
        thoughts_token_count=thoughts_token_count,
        usage_metadata=usage_metadata,
        duration=duration,
        success=success,
        error_message=error_message,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(row)
    await session.flush()
    return row
