from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class HomeworkJob(Base, UUIDPK, Timestamps):
    __tablename__ = "homework_jobs"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    toc_entry_id: Mapped[UUID] = mapped_column(ForeignKey("toc_entries.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assembled_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Structured games extracted from the game-breaks phase output. Shape:
    # {"games": [{"type": "adaptive_quiz" | "tile_match" | "memory_match" |
    #             "sentence_fill", "title": str, ...type-specific fields}]}
    games_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Structured flashcards extracted from the flashcards phase. Shape:
    # {"cards": [{"front": str, "back": str, "hint"?: str, "cluster"?: str}]}
    flashcards_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    phase_outputs: Mapped[list["PhaseOutput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PhaseOutput.phase_order"
    )

    __table_args__ = (
        Index("ix_homework_jobs_book_toc", "book_id", "toc_entry_id"),
        Index("ix_homework_jobs_status", "status"),
    )


from app.models.phase_output import PhaseOutput  # noqa: E402
