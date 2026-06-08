"""Add batches table (fleet batch automation) + homework_jobs.batch_id."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d5e9f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("notion_source", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", name="uq_batches_book_id"),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("batch_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_homework_jobs_batch_id", "homework_jobs", "batches", ["batch_id"], ["id"]
    )
    op.create_index("ix_homework_jobs_batch_id", "homework_jobs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_homework_jobs_batch_id", table_name="homework_jobs")
    op.drop_constraint("fk_homework_jobs_batch_id", "homework_jobs", type_="foreignkey")
    op.drop_column("homework_jobs", "batch_id")
    op.drop_table("batches")
