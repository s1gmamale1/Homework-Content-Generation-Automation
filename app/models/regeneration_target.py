from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK

# Generation and publication states live in ONE column but stay conceptually
# separate: a generated revision is never regenerated because Notion delivery
# failed — `publication_failed` retries delivery only.
TARGET_STATUSES = (
    "planned",
    "generating",
    "awaiting_canary_approval",
    "publication_pending",
    "publishing",
    "published",
    "generation_failed",
    "publication_failed",
    "abandoned",
)

# The states that mean "a publication version has been reserved and released".
PUBLICATION_STATUSES = (
    "publication_pending",
    "publishing",
    "published",
    "publication_failed",
)

# Terminal states (spec §8.2): these — and only these — stamp `terminal_at`,
# which is what frees the lesson's active lineage for a later campaign.
TERMINAL_STATUSES = ("published", "abandoned")


def _sql_list(values: tuple[str, ...]) -> str:
    return ",".join(f"'{v}'" for v in values)


class RegenerationTarget(Base, UUIDPK, Timestamps):
    """One lesson (TOC entry + output language) inside one campaign.

    There is deliberately NO `revision_job_id` column here. The authoritative
    one-to-one link is the unique `homework_jobs.regeneration_target_id`, read
    back through the `revision_job` relationship — a second, mutable foreign key
    pointing the other way could disagree with it, and nothing in the database
    could say which side was right.

    A target row is audit history: it records a consumed publication version,
    so the campaign and TOC foreign keys are RESTRICT — letting either delete
    cascade the row away would silently free that version for reuse.

    `source_job_id` is the deliberate exception and is SET NULL; see the column
    comment. Nulling a pointer is not audit destruction — the row, its version
    and its Notion page ID all survive.
    """

    __tablename__ = "regeneration_targets"

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "regeneration_campaigns.id",
            ondelete="RESTRICT",
            name="fk_regeneration_targets_campaign_id",
        ),
        nullable=False,
    )
    toc_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "toc_entries.id",
            ondelete="RESTRICT",
            name="fk_regeneration_targets_toc_entry_id",
        ),
        nullable=False,
    )
    output_language: Mapped[str] = mapped_column(String(8), nullable=False)
    # The V1 (or latest) job whose snapshot this revision is derived from.
    #
    # SET NULL, not RESTRICT (spec §8.3). Source-deletion protection lives on
    # the OTHER key: homework_jobs.revision_of_job_id is RESTRICT, so deleting a
    # source out from under a LIVE revision fails cleanly. Once an explicitly
    # ordered child-first purge has removed that revision, the source may go and
    # this reporting row survives with a null historical source link.
    #
    # RESTRICT here instead would make that documented purge impossible: the
    # target would reference the source forever, so the source could never be
    # deleted and this nullable column could never actually reach null. It also
    # masked the revision-child guard — the target's key fired first, so nothing
    # ever proved revision_of_job_id was doing any work.
    #
    # use_alter: this FK and homework_jobs.regeneration_target_id point at each
    # other, so the metadata table sort has a genuine cycle. Marking the weaker
    # (nullable) side breaks it — SQLAlchemy would otherwise warn that it cannot
    # sort the tables, and a future release turns that warning into an error.
    # No DDL impact here: the migration creates both keys explicitly.
    source_job_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey(
            "homework_jobs.id",
            ondelete="SET NULL",
            name="fk_regeneration_targets_source_job_id",
            use_alter=True,
        ),
        nullable=True,
    )
    is_canary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Frozen per-target plan: which phases are regenerated and which are copied
    # from the source snapshot. Written at planning time, never mutated.
    #
    # The value is `RegenerationPhasePlan.to_json()` — a JSON OBJECT, not a bare
    # phase-name list, and it is read back ONLY through
    # `RegenerationPhasePlan.from_json`. A flat list cannot express what the
    # later lanes need: the copied/regenerated split, the auto-included and
    # acknowledged-excluded sets, the broken dependency edges and
    # `refresh_extraction`. `app.services.regeneration_planner` owns the only
    # serializer; nothing here or in the repositories hand-rolls that JSON.
    phase_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="planned")

    # ─── publication ──────────────────────────────────────────────────────
    # Stamped when publication is RELEASED (the version is reserved at that
    # moment and is never reused, even if delivery later fails).
    publication_released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Logical V1 is the existing `Homework` page and has no row here, so the
    # first allocated number is 2.
    publication_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notion_page_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # ─── durable publisher claim (survives a publisher restart) ───────────
    publication_claim_token: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    publication_claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publication_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    publication_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publication_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ─── terminality ──────────────────────────────────────────────────────
    terminal_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Cancellation convergence: a running revision must finish cancelling before
    # the target may become terminal, so the REQUEST is recorded separately from
    # the terminal stamp.
    abandon_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    abandon_requested_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    campaign: Mapped["RegenerationCampaign"] = relationship(back_populates="targets")
    # One-to-one, owned by the job side's unique column.
    revision_job: Mapped[Optional["HomeworkJob"]] = relationship(
        "HomeworkJob",
        back_populates="regeneration_target",
        foreign_keys="HomeworkJob.regeneration_target_id",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "toc_entry_id",
            "output_language",
            name="uq_regeneration_targets_campaign_toc_language",
        ),
        # At most ONE non-terminal target per (lesson, language) ACROSS
        # campaigns. A generation failure therefore blocks a competing campaign
        # until the operator retries or explicitly abandons it.
        Index(
            "uq_regeneration_targets_active_lineage",
            "toc_entry_id",
            "output_language",
            unique=True,
            postgresql_where=text("terminal_at IS NULL"),
        ),
        # A version number is consumed forever, per lesson AND language — UZ V2
        # and RU V2 are independent publications.
        Index(
            "uq_regeneration_targets_publication_version",
            "toc_entry_id",
            "output_language",
            "publication_version",
            unique=True,
            postgresql_where=text("publication_version IS NOT NULL"),
        ),
        Index("ix_regeneration_targets_campaign_id", "campaign_id"),
        Index("ix_regeneration_targets_source_job_id", "source_job_id"),
        CheckConstraint(
            f"status IN ({_sql_list(TARGET_STATUSES)})",
            name="ck_regeneration_targets_status",
        ),
        CheckConstraint(
            "output_language IN ('uz','en','ru')",
            name="ck_regeneration_targets_output_language",
        ),
        # Terminality is not a convention the services agree to honour: the
        # partial lineage index above is only correct if `terminal_at` and
        # `status` can never disagree.
        CheckConstraint(
            f"(status IN ({_sql_list(TERMINAL_STATUSES)})) = (terminal_at IS NOT NULL)",
            name="ck_regeneration_targets_terminal_at",
        ),
        CheckConstraint(
            "status <> 'published' OR ("
            "publication_version IS NOT NULL AND notion_page_id IS NOT NULL "
            "AND publication_released_at IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_regeneration_targets_published_complete",
        ),
        CheckConstraint(
            f"status NOT IN ({_sql_list(PUBLICATION_STATUSES)}) "
            "OR publication_released_at IS NOT NULL",
            name="ck_regeneration_targets_publication_released",
        ),
        CheckConstraint(
            "publication_attempts >= 0",
            name="ck_regeneration_targets_publication_attempts",
        ),
    )
