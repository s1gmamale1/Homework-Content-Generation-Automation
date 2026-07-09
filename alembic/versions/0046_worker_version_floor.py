"""budget_state: fleet worker version floor (fleet-worker-version-gate-1).

Revision ID: 0046_worker_version_floor
Revises: 0045_notion_archived_job
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_worker_version_floor"
down_revision = "0045_notion_archived_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_state", sa.Column("min_worker_version", sa.Integer(), nullable=True))
    op.add_column("budget_state", sa.Column("min_worker_version_stamped_by", sa.String(128), nullable=True))
    op.add_column(
        "budget_state",
        sa.Column("min_worker_version_stamped_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_state", "min_worker_version_stamped_at")
    op.drop_column("budget_state", "min_worker_version_stamped_by")
    op.drop_column("budget_state", "min_worker_version")
