"""Add homework_jobs.notion_skip_reason (silent-skip visibility)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7b2d3e6f0"
down_revision: Union[str, Sequence[str], None] = "b3f6a1c2d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("notion_skip_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("homework_jobs", "notion_skip_reason")
