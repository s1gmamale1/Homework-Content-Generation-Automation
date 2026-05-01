from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPK


class GeminiUsage(Base, UUIDPK, Timestamps):
    """One row per Gemini API call. Records what the call was for, how long it
    took, what tokens it consumed, and whether it succeeded.

    The three nullable FKs are alternatives — exactly one is typically set:
    - `book_id` for file uploads and TOC extraction
    - `homework_job_id` + `phase_output_id` for pipeline phases
    """

    __tablename__ = "gemini_usages"

    # Optional links — SET NULL on delete so usage history survives cleanup
    book_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    homework_job_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("homework_jobs.id", ondelete="SET NULL"), nullable=True
    )
    phase_output_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("phase_outputs.id", ondelete="SET NULL"), nullable=True
    )

    # What kind of call it was: 'files.upload' | 'toc.extract' | 'lesson.extract' | 'phase.run'
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Token counts — broken down by what this project actually consumes:
    # text + image (PDFs render as images), thoughts, output (candidates), and
    # the SDK's own roll-ups for total prompt and cached portion.
    total_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_text_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_image_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidates_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    thoughts_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_content_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Raw usage_metadata as JSON — for fields the SDK adds later that we don't surface yet
    usage_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Duration as a stringified value (e.g., '1.23s' or '4500ms') — easy to read in DB browsers
    duration: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_gemini_usages_book", "book_id"),
        Index("ix_gemini_usages_job", "homework_job_id"),
        Index("ix_gemini_usages_phase", "phase_output_id"),
        Index("ix_gemini_usages_operation", "operation"),
        Index("ix_gemini_usages_created_at_desc", "created_at"),
    )
