"""launch_defaults.solver_boss_arena_enabled — operator toggle for boss-arena solving.

Revision ID: 0044_solver_boss_toggle
Revises: 0043_solver_role_columns
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_solver_boss_toggle"
down_revision = "0043_solver_role_columns"  # re-verify current head at execution
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL + server_default true backfills the seeded singleton row.
    op.add_column(
        "launch_defaults",
        sa.Column("solver_boss_arena_enabled", sa.Boolean(),
                  nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("launch_defaults", "solver_boss_arena_enabled")
