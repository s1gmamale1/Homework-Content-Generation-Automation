"""add batches.paused_at + paused_reason (batch-pause primitive)

Revision ID: 0031_batch_pause_columns
Revises: 0030_agent_usages_cache_creation
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_batch_pause_columns"
down_revision = "0030_agent_usages_cache_creation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batches",
        sa.Column("paused_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "batches",
        sa.Column("paused_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batches", "paused_reason")
    op.drop_column("batches", "paused_at")
