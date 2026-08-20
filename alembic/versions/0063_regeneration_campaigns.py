"""versioned homework regeneration: campaigns, targets, revision jobs

Adds the schema for regenerating a complete homework snapshot with current
prompts and publishing it as an immutable `Homework V2/V3/...` Notion sibling,
never mutating V1.

Two new tables (`regeneration_campaigns`, `regeneration_targets`), two nullable
columns on `homework_jobs` that mark a job as a revision, and one on
`phase_outputs` that marks a phase copied from an earlier snapshot.

A target row is audit history — it records a publication version that is
consumed forever — so an implicit cascade from a TOC entry or a campaign would
silently free that version for reuse. Those keys are ON DELETE RESTRICT, as are
`homework_jobs.revision_of_job_id` / `regeneration_target_id` and
`phase_outputs.copied_from_phase_output_id`.

`regeneration_targets.source_job_id` is the single deliberate exception and is
ON DELETE SET NULL (spec §8.3). Protection against deleting a source out from
under a LIVE revision is `homework_jobs.revision_of_job_id`'s RESTRICT, which
fails cleanly. Once an explicitly ordered child-first purge removes the revision
child, the source may be deleted and the target survives as a reporting row with
a null historical source link. RESTRICT on both keys instead would block that
documented purge forever and leave the nullable column unreachable.

The one trigger in this repository lives here:
`trg_regeneration_targets_publication_gate` refuses any transition into
`publication_pending` / `publishing` / `published` unless the owning campaign
is approved and not rejected/cancelled. It reads the campaign row FOR KEY SHARE
so a target racing a concurrent approval WAITS for that transaction instead of
deciding from a stale snapshot. This is a database-level guarantee on purpose:
publication is irreversible (a public Notion page + a permanently consumed
version number), so it must not depend on every future caller remembering the
rule.

Revision ID: 0063_regeneration_campaigns
Revises: 0062_cap_pause_provenance

NOTE (parallel-worktree integration): three unmerged sibling branches carry a
DIFFERENT file also numbered 0063 (`0063_notion_archive_outbox.py`). The
revision IDs are distinct, so there is no Alembic collision — only a cosmetic
numeric-prefix overlap. If that other 0063 lands first, re-point this file's
`down_revision` at it before merging (same precedent as the note in
`0062_cap_pause_provenance.py`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063_regeneration_campaigns"
down_revision: Union[str, Sequence[str], None] = "0062_cap_pause_provenance"
branch_labels = None
depends_on = None

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

PUBLICATION_STATUSES = (
    "publication_pending",
    "publishing",
    "published",
    "publication_failed",
)

TERMINAL_STATUSES = ("published", "abandoned")

# Transitions INTO these require an approved campaign (the trigger below).
GATED_STATUSES = ("publication_pending", "publishing", "published")


def _sql_list(values: Sequence[str]) -> str:
    return ",".join(f"'{v}'" for v in values)


_TRIGGER_FUNCTION = f"""
CREATE OR REPLACE FUNCTION regeneration_target_publication_gate()
RETURNS trigger AS $$
DECLARE
    owner RECORD;
BEGIN
    IF NEW.status NOT IN ({_sql_list(GATED_STATUSES)}) THEN
        RETURN NEW;
    END IF;
    -- Only a TRANSITION into a publication state is gated; ordinary bookkeeping
    -- writes on a row already publishing (attempts, claim, last error) are not.
    IF TG_OP = 'UPDATE' AND OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;
    -- FOR KEY SHARE: if the operator's approval is committing right now, WAIT
    -- for it rather than reading this transaction's older snapshot and refusing
    -- a target that is in fact approved.
    SELECT c.approved_at, c.status
      INTO owner
      FROM regeneration_campaigns c
     WHERE c.id = NEW.campaign_id
       FOR KEY SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'regeneration target %: owning campaign % does not exist',
            NEW.id, NEW.campaign_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF owner.approved_at IS NULL OR owner.status IN ('rejected','cancelled') THEN
        RAISE EXCEPTION
            'regeneration target % cannot enter status %: campaign % is not approved (approved_at=%, status=%)',
            NEW.id, NEW.status, NEW.campaign_id, owner.approved_at, owner.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_regeneration_targets_publication_gate
BEFORE INSERT OR UPDATE ON regeneration_targets
FOR EACH ROW EXECUTE FUNCTION regeneration_target_publication_gate();
"""


def upgrade() -> None:
    op.create_table(
        "regeneration_campaigns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.String(length=48), nullable=False, server_default="draft"),
        # Immutable specification — written once at draft time.
        sa.Column("selection_spec", postgresql.JSONB(), nullable=False),
        sa.Column("requested_phases", postgresql.JSONB(), nullable=False),
        sa.Column("excluded_phases", postgresql.JSONB(), nullable=False),
        sa.Column("launch_contract", postgresql.JSONB(), nullable=False),
        sa.Column(
            "refresh_extraction", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "exclusion_acknowledged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("canary_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("estimated_cost_low_usd", sa.Double(), nullable=True),
        sa.Column("estimated_cost_high_usd", sa.Double(), nullable=True),
        sa.Column("app_git_revision", sa.String(length=64), nullable=True),
        # Audit trail.
        sa.Column("canary_launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("cancel_requested_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"status IN ({_sql_list(CAMPAIGN_STATUSES)})",
            name="ck_regeneration_campaigns_status",
        ),
        sa.CheckConstraint(
            "canary_size >= 0", name="ck_regeneration_campaigns_canary_size"
        ),
    )
    op.create_index(
        "ix_regeneration_campaigns_status", "regeneration_campaigns", ["status"]
    )

    op.create_table(
        "regeneration_targets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("toc_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_language", sa.String(length=8), nullable=False),
        sa.Column("source_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_canary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("phase_plan", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planned"),
        sa.Column("publication_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_version", sa.Integer(), nullable=True),
        sa.Column("notion_page_id", sa.String(length=128), nullable=True),
        sa.Column("publication_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("publication_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publication_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("publication_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_last_error", sa.Text(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("abandon_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandon_requested_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["regeneration_campaigns.id"],
            name="fk_regeneration_targets_campaign_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["toc_entry_id"],
            ["toc_entries.id"],
            name="fk_regeneration_targets_toc_entry_id",
            ondelete="RESTRICT",
        ),
        # SET NULL, not RESTRICT — see the module docstring. The restrictive
        # half of the source-deletion rule is fk_homework_jobs_revision_of_job_id.
        sa.ForeignKeyConstraint(
            ["source_job_id"],
            ["homework_jobs.id"],
            name="fk_regeneration_targets_source_job_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "toc_entry_id",
            "output_language",
            name="uq_regeneration_targets_campaign_toc_language",
        ),
        sa.CheckConstraint(
            f"status IN ({_sql_list(TARGET_STATUSES)})",
            name="ck_regeneration_targets_status",
        ),
        sa.CheckConstraint(
            "output_language IN ('uz','en','ru')",
            name="ck_regeneration_targets_output_language",
        ),
        # `terminal_at` and `status` can never disagree — the partial lineage
        # index below is only correct because of this.
        sa.CheckConstraint(
            f"(status IN ({_sql_list(TERMINAL_STATUSES)})) = (terminal_at IS NOT NULL)",
            name="ck_regeneration_targets_terminal_at",
        ),
        sa.CheckConstraint(
            "status <> 'published' OR ("
            "publication_version IS NOT NULL AND notion_page_id IS NOT NULL "
            "AND publication_released_at IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_regeneration_targets_published_complete",
        ),
        sa.CheckConstraint(
            f"status NOT IN ({_sql_list(PUBLICATION_STATUSES)}) "
            "OR publication_released_at IS NOT NULL",
            name="ck_regeneration_targets_publication_released",
        ),
        sa.CheckConstraint(
            "publication_attempts >= 0",
            name="ck_regeneration_targets_publication_attempts",
        ),
    )
    # At most ONE non-terminal target per (lesson, language) across ALL
    # campaigns: a competing campaign is blocked until the operator retries or
    # explicitly abandons the existing one.
    op.create_index(
        "uq_regeneration_targets_active_lineage",
        "regeneration_targets",
        ["toc_entry_id", "output_language"],
        unique=True,
        postgresql_where=sa.text("terminal_at IS NULL"),
    )
    # A version number is consumed forever, per lesson AND language — UZ V2 and
    # RU V2 are independent publications.
    op.create_index(
        "uq_regeneration_targets_publication_version",
        "regeneration_targets",
        ["toc_entry_id", "output_language", "publication_version"],
        unique=True,
        postgresql_where=sa.text("publication_version IS NOT NULL"),
    )
    op.create_index(
        "ix_regeneration_targets_campaign_id", "regeneration_targets", ["campaign_id"]
    )
    op.create_index(
        "ix_regeneration_targets_source_job_id", "regeneration_targets", ["source_job_id"]
    )

    # ─── revision jobs ────────────────────────────────────────────────────
    op.add_column(
        "homework_jobs",
        sa.Column("revision_of_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("regeneration_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_homework_jobs_revision_of_job_id",
        "homework_jobs",
        "homework_jobs",
        ["revision_of_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_homework_jobs_regeneration_target_id",
        "homework_jobs",
        "regeneration_targets",
        ["regeneration_target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_homework_jobs_regeneration_target_id",
        "homework_jobs",
        ["regeneration_target_id"],
    )
    op.create_index(
        "ix_homework_jobs_revision_of_job_id", "homework_jobs", ["revision_of_job_id"]
    )
    op.create_check_constraint(
        "ck_homework_jobs_revision_pair",
        "homework_jobs",
        "(revision_of_job_id IS NULL) = (regeneration_target_id IS NULL)",
    )
    # A revision is never a Fleet batch member, so batch rollups, adoption,
    # resume and dedup queries stay untouched by regeneration.
    op.create_check_constraint(
        "ck_homework_jobs_revision_no_batch",
        "homework_jobs",
        "revision_of_job_id IS NULL OR batch_id IS NULL",
    )

    # ─── copied (not regenerated) phase provenance ────────────────────────
    op.add_column(
        "phase_outputs",
        sa.Column(
            "copied_from_phase_output_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_phase_outputs_copied_from_phase_output_id",
        "phase_outputs",
        "phase_outputs",
        ["copied_from_phase_output_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_phase_outputs_copied_from", "phase_outputs", ["copied_from_phase_output_id"]
    )

    op.execute(_TRIGGER_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_regeneration_targets_publication_gate "
        "ON regeneration_targets"
    )
    op.execute("DROP FUNCTION IF EXISTS regeneration_target_publication_gate()")

    op.drop_index("ix_phase_outputs_copied_from", table_name="phase_outputs")
    op.drop_constraint(
        "fk_phase_outputs_copied_from_phase_output_id",
        "phase_outputs",
        type_="foreignkey",
    )
    op.drop_column("phase_outputs", "copied_from_phase_output_id")

    op.drop_constraint("ck_homework_jobs_revision_no_batch", "homework_jobs", type_="check")
    op.drop_constraint("ck_homework_jobs_revision_pair", "homework_jobs", type_="check")
    op.drop_index("ix_homework_jobs_revision_of_job_id", table_name="homework_jobs")
    op.drop_constraint(
        "uq_homework_jobs_regeneration_target_id", "homework_jobs", type_="unique"
    )
    op.drop_constraint(
        "fk_homework_jobs_regeneration_target_id", "homework_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_homework_jobs_revision_of_job_id", "homework_jobs", type_="foreignkey"
    )
    op.drop_column("homework_jobs", "regeneration_target_id")
    op.drop_column("homework_jobs", "revision_of_job_id")

    op.drop_index(
        "ix_regeneration_targets_source_job_id", table_name="regeneration_targets"
    )
    op.drop_index(
        "ix_regeneration_targets_campaign_id", table_name="regeneration_targets"
    )
    op.drop_index(
        "uq_regeneration_targets_publication_version", table_name="regeneration_targets"
    )
    op.drop_index(
        "uq_regeneration_targets_active_lineage", table_name="regeneration_targets"
    )
    op.drop_table("regeneration_targets")
    op.drop_index(
        "ix_regeneration_campaigns_status", table_name="regeneration_campaigns"
    )
    op.drop_table("regeneration_campaigns")
