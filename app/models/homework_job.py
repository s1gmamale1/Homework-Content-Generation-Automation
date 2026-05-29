from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
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
    # LLM provider used for this job (e.g. "gemini", "openai", "anthropic"). Set when the
    # job is created and never changes — pinned so retries hit the same backend.
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="gemini"
    )
    # Specific model id within the provider (e.g. "gemini-2.5-flash", "gpt-5-mini"). Optional
    # because some providers default at the SDK level.
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
    # Boss-fight-style HP quiz extracted from final-challenge.
    # {"title", "starting_hp", "questions": [{prompt, kind, options, correct_index, damage, hints, ...}]}
    final_challenge_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Quick recognition quiz extracted from memory-sprint.
    # {"items": [{prompt, kind, options, correct_index, explanation}]}
    memory_sprint_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # English-only reading passage with inline checkpoints.
    # {"passage_md", "checkpoints": [{after_paragraph, prompt, options, correct_index}], "cefr_level"}
    reading_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Canonical Real-Life Challenge (Practice Arc). Rich generator source-of-truth
    # (Infra pedagogy + 5 logical steps). Beta export down-maps this to the
    # platform `real_life_challenge` key; server-only answer fields are stripped
    # before the student-facing payload. Shape: app/schemas/real_life.py
    real_life_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Lesson skill registry: atomic concepts + can-do target skills derived from
    # the lesson. The source-of-truth every Practice Arc mission maps to.
    # {"concepts": [{concept_id, label, statement, section_ref}],
    #  "skills": [{skill_id, statement, bloom_level, pisa_level, concept_ids}]}
    # Shape: app/schemas/skills.py
    skills_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ─── queue bookkeeping ────────────────────────────────────────────────
    # Higher priority jobs claim first. User-triggered = 0 (default).
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Earliest time a worker may claim this job. Used for delayed retries.
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    # Worker provenance — set when a worker successfully claims this job.
    # Stuck-job detection: rows in `running` with stale `claimed_at` get
    # promoted back to `pending` for another worker to retry.
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Retry bookkeeping. Incremented on every claim. After
    # `settings.queue_max_attempts` the worker marks the job as failed
    # terminally instead of retrying.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    phase_outputs: Mapped[list["PhaseOutput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PhaseOutput.phase_order"
    )

    __table_args__ = (
        Index("ix_homework_jobs_book_toc", "book_id", "toc_entry_id"),
        Index("ix_homework_jobs_status", "status"),
        # Partial queue index: only rows a worker actually scans.
        Index(
            "ix_homework_jobs_queue",
            "scheduled_at",
            text("priority DESC"),
            postgresql_where=text("status = 'pending'"),
        ),
    )


from app.models.phase_output import PhaseOutput  # noqa: E402
