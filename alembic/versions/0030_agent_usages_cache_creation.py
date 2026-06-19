"""add agent_usages.cache_creation_tokens

Revision ID: 0030_agent_usages_cache_creation
Revises: 0029_judge_status
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_agent_usages_cache_creation"
down_revision = "0029_judge_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_usages",
        sa.Column(
            "cache_creation_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Backfill rows that already have the raw claude cache-write count
    op.execute(
        "UPDATE agent_usages "
        "SET cache_creation_tokens = COALESCE((raw_envelope->>'cache_creation_input_tokens')::int, 0) "
        "WHERE raw_envelope ? 'cache_creation_input_tokens'"
    )


def downgrade() -> None:
    op.drop_column("agent_usages", "cache_creation_tokens")
