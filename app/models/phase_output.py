from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPK


class PhaseOutput(Base, UUIDPK):
    __tablename__ = "phase_outputs"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("homework_jobs.id", ondelete="CASCADE"), nullable=False
    )
    phase_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # The provider that ACTUALLY produced this phase (may differ from the job's
    # requested provider after failover). Nullable; job badge = requested.
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    output_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Deterministic validator output for this phase's markdown (list[str]).
    # Warn-only — never blocks generation. Surfaced per-phase in the console.
    validation_warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # LLM-judge verdict for this phase: "ok", "major", "minor", or None (not yet judged).
    judge_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["HomeworkJob"] = relationship(back_populates="phase_outputs")

    __table_args__ = (UniqueConstraint("job_id", "phase_order", name="uq_phase_output_job_order"),)


from app.models.homework_job import HomeworkJob  # noqa: E402
