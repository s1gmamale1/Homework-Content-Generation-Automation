from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPK

AUTHORING_MODES = (
    "structured",
    "markdown_fallback",
    "markdown_builtin",
    "markdown_custom",
    "markdown_legacy",
)


class PhaseOutput(Base, UUIDPK):
    __tablename__ = "phase_outputs"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("homework_jobs.id", ondelete="CASCADE"), nullable=False
    )
    phase_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # 128 (not 64): a custom-prompt hash is "custom:sha256:<64 hex>" = 78 chars.
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
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
    # LLM-judge verdict for this phase: ok | major_shipped | major_regen_failed |
    # unavailable | refused | None (not judged).
    judge_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    # LLM-solver verdict for this phase's answer key (CQ-C):
    # ok | mismatch_regen | mismatch_shipped | mismatch_regen_failed |
    # mismatch_blocked | unavailable | refused | None (not solved).
    solver_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Structured generation (content_json lane). All nullable — pre-migration
    # rows read as markdown_legacy.
    content_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    authoring_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    content_schema_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    renderer_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Fencing token of the claim that produced/is producing this phase row —
    # mirrors HomeworkJob.claim_token so a stale worker's write can be
    # rejected even at phase-row granularity.
    claim_token: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # Versioned regeneration: this row was COPIED verbatim from an earlier
    # phase output (a phase the campaign deliberately did not regenerate)
    # rather than generated. NULL on every generated row. RESTRICT so the
    # snapshot a live revision was built from cannot be deleted underneath it.
    copied_from_phase_output_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey(
            "phase_outputs.id",
            ondelete="RESTRICT",
            name="fk_phase_outputs_copied_from_phase_output_id",
        ),
        nullable=True,
    )

    job: Mapped["HomeworkJob"] = relationship(back_populates="phase_outputs")

    __table_args__ = (
        UniqueConstraint("job_id", "phase_order", name="uq_phase_output_job_order"),
        Index("ix_phase_outputs_copied_from", "copied_from_phase_output_id"),
    )


from app.models.homework_job import HomeworkJob  # noqa: E402
