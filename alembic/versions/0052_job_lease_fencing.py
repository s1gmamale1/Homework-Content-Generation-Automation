"""job lease fencing: claim_token columns + job_lease_events ledger"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0052_job_lease_fencing"
down_revision = "0051_launch_defaults_3x"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("homework_jobs", sa.Column("claim_token", UUID(as_uuid=True), nullable=True))
    op.add_column("phase_outputs", sa.Column("claim_token", UUID(as_uuid=True), nullable=True))
    op.create_table(
        "job_lease_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),  # NO FK: ledger survives job deletion
        sa.Column("claim_token", UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # NB: every event this system writes carries a NON-NULL token (claimed→new
    # token, reclaimed_*→the OLD rotated token, lease_lost/released_*→the presented
    # token), so PG's default NULLS-DISTINCT never defeats this idempotency key.
    op.create_unique_constraint(
        "uq_job_lease_events_job_token_event", "job_lease_events",
        ["job_id", "claim_token", "event_type"])
    op.create_index("ix_job_lease_events_job_id", "job_lease_events", ["job_id"])
    op.create_index("ix_job_lease_events_created_at", "job_lease_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_lease_events_created_at", table_name="job_lease_events")
    op.drop_index("ix_job_lease_events_job_id", table_name="job_lease_events")
    op.drop_constraint("uq_job_lease_events_job_token_event", "job_lease_events", type_="unique")
    op.drop_table("job_lease_events")
    op.drop_column("phase_outputs", "claim_token")
    op.drop_column("homework_jobs", "claim_token")
