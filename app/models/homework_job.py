from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
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
    batch_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("batches.id"), nullable=True
    )
    current_phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notion_archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why a `done` job was NOT pushed to Notion (resolvable causes only:
    # no subject-page mapping, no completed phases, missing book/section).
    # NULL = archived, not-yet-attempted, or archiving disabled. Cleared on a
    # successful archive. Makes the silent-skip failure mode visible.
    notion_skip_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
        Index("ix_homework_jobs_batch_id", "batch_id"),
        # Partial queue index: only rows a worker actually scans.
        Index(
            "ix_homework_jobs_queue",
            "scheduled_at",
            text("priority DESC"),
            postgresql_where=text("status = 'pending'"),
        ),
    )


from app.models.phase_output import PhaseOutput  # noqa: E402
