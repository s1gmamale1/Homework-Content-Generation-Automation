from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Double, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK

# Lifecycle (spec §8.1). `attention_required` is NOT terminal — it is the state
# a bulk run parks in while any target is retryable, and it is the reason a
# campaign's completion is derived from its TARGETS, never from job counts.
CAMPAIGN_STATUSES = (
    "draft",
    "canary_running",
    "awaiting_canary_approval",
    "approved",
    "bulk_running",
    "attention_required",
    "completed",
    "completed_with_abandonments",
    "rejected",
    "cancelled",
)


class RegenerationCampaign(Base, UUIDPK, Timestamps):
    """One operator-approved regeneration run over a set of lessons.

    Everything that decides WHAT gets generated is frozen here at draft time —
    the selection, the requested/excluded phases and the full launch contract —
    so a campaign that is approved days later still regenerates exactly what the
    operator saw and priced. The JSON columns are written once at creation and
    never mutated; only the lifecycle status and the audit timestamps/reasons
    move afterwards.
    """

    __tablename__ = "regeneration_campaigns"

    status: Mapped[str] = mapped_column(String(48), nullable=False, server_default="draft")

    # ─── immutable specification (written once, at draft time) ────────────
    # How the lessons were chosen (book/subject/grade/language + explicit ids).
    selection_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The phases the operator asked for, and the dependency-closure result the
    # operator was shown and acknowledged.
    requested_phases: Mapped[list] = mapped_column(JSONB, nullable=False)
    excluded_phases: Mapped[list] = mapped_column(JSONB, nullable=False)
    # A serialized `app.schemas.regeneration_contract.ResolvedLaunchContract`
    # (resolved once, at draft time, before it is stored) — the one owner of
    # the provider/model/transport selection for every revision job this
    # campaign creates. NOT the language: a revision's `output_language` is per
    # target (`regeneration_targets.output_language`, chosen by
    # `selection_spec`) and is copied from the immediate source job, because
    # one campaign may hold a UZ and an RU target for the same lesson.
    launch_contract: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Re-run the extract phase (pay for a fresh source read) instead of reusing
    # the source job's cached extract.
    refresh_extraction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # The operator confirmed the excluded-phase list (phases that will be COPIED
    # from the source snapshot rather than regenerated).
    exclusion_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    canary_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Estimate shown at approval time; kept for the after-the-fact comparison
    # against real `agent_usages` spend. Nullable: a draft may predate pricing.
    estimated_cost_low_usd: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    estimated_cost_high_usd: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    # The app revision that produced this campaign's prompts — regeneration's
    # whole point is "current prompts", so which "current" must be recorded.
    app_git_revision: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # The Notion version number this whole campaign publishes ("Homework V2").
    # NOT the same column as `regeneration_targets.publication_version`, which
    # is the per-lesson allocation guarded by
    # `uq_regeneration_targets_publication_version`; this one is the campaign's
    # single declared version.
    #
    # Nullable, because logical V1 is the pre-existing `Homework` page that no
    # campaign produced, and every campaign drafted before the guided wizard has
    # no version to claim. Task 4 is what makes it mandatory on a new
    # service-created campaign; retro-assigning one here would invent a
    # publication that never happened.
    publication_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ─── audit trail ──────────────────────────────────────────────────────
    canary_launched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancel_requested_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    targets: Mapped[list["RegenerationTarget"]] = relationship(
        back_populates="campaign", order_by="RegenerationTarget.created_at"
    )

    __table_args__ = (
        Index("ix_regeneration_campaigns_status", "status"),
        CheckConstraint(
            "status IN ("
            + ",".join(f"'{s}'" for s in CAMPAIGN_STATUSES)
            + ")",
            name="ck_regeneration_campaigns_status",
        ),
        CheckConstraint(
            "canary_size >= 0",
            name="ck_regeneration_campaigns_canary_size",
        ),
        # Already total: `NULL IS NULL` is TRUE, so the NULL case is decided by
        # the first disjunct and never leaves the predicate UNKNOWN.
        CheckConstraint(
            "publication_version IS NULL OR publication_version >= 2",
            name="ck_regeneration_campaigns_publication_version",
        ),
    )


from app.models.regeneration_target import RegenerationTarget  # noqa: E402
