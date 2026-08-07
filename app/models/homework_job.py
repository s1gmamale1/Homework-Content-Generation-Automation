from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class HomeworkJob(Base, UUIDPK, Timestamps):
    __tablename__ = "homework_jobs"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    toc_entry_id: Mapped[UUID] = mapped_column(ForeignKey("toc_entries.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # LLM provider used for this job (e.g. "gemini", "openai", "anthropic"). Set when the
    # job is created and never changes — pinned so retries hit the same backend.
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="gemini"
    )
    # Specific model id within the provider (e.g. "gemini-2.5-flash", "gpt-5-mini"). Optional
    # because some providers default at the SDK level.
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Generation transport: "cli" (subprocess CLI, default) vs "api" (pay-per-token SDK).
    transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cli")
    # Output language for generated content: "uz" (default), "en", or "ru".
    output_language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="uz")
    # Per-role transport overrides: "cli" | "api" | "inherit" (follow the job's transport).
    extract_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    judge_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    # Per-phase custom prompt overrides {phase_name: markdown} for this job.
    # NULL/{} = all built-in. Never written to prompts/.
    custom_prompts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Ordered content-phase subset to run (dependency-closure-expanded at launch).
    # NULL = run the full subject flow. Named selected_phases (not `phases`) to
    # avoid colliding with JobOut.phases (the phase-outputs list).
    selected_phases: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Per-role provider/model overrides. NULL = fall back to the role default
    # (extract -> global default (launch_defaults); judge -> model_tiers auto).
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    solver_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    solver_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    solver_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
    # Per-claim fencing token: a fresh UUID minted on every successful claim.
    # A worker that was reclaimed (stale lease) presents an old token and gets
    # fenced out instead of mutating a job another worker now owns.
    claim_token: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
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
        CheckConstraint(
            "status IN ('pending','running','done','failed','cancelling','cancelled')",
            name="ck_homework_jobs_status",
        ),
        CheckConstraint(
            "transport IN ('cli','api')",
            name="ck_homework_jobs_transport",
        ),
        CheckConstraint(
            "extract_transport IN ('cli','api','inherit')",
            name="ck_homework_jobs_extract_transport",
        ),
        CheckConstraint(
            "judge_transport IN ('cli','api','inherit')",
            name="ck_homework_jobs_judge_transport",
        ),
        CheckConstraint(
            "solver_transport IN ('cli','api','inherit')",
            name="ck_homework_jobs_solver_transport",
        ),
        CheckConstraint(
            "output_language IN ('uz','en','ru')",
            name="ck_homework_jobs_output_language",
        ),
    )


from app.models.phase_output import PhaseOutput  # noqa: E402
