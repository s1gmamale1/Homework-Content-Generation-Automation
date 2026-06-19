"""add phase_outputs.judge_status

Revision ID: 0029_judge_status
Revises: 0028_enum_check_constraints
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_judge_status"
down_revision = "0028_enum_check_constraints"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("judge_status", sa.String(length=24), nullable=True))

def downgrade() -> None:
    op.drop_column("phase_outputs", "judge_status")
