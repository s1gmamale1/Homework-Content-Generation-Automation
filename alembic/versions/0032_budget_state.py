"""fleet-daily global pause gate — budget_state singleton table

Revision ID: 0032_budget_state
Revises: 0031_batch_pause_columns
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_budget_state"
down_revision = "0031_batch_pause_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_paused_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("api_paused_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_budget_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the mandatory singleton row.
    op.execute("INSERT INTO budget_state (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("budget_state")
