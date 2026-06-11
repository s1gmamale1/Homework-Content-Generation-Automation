"""Add transport (homework_jobs, batches) + auth_mode (agent_usages) + fold
transport into the batches unique key (Phase 4 transport toggle)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7e6d5c4b3a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default="cli" backfills existing rows; no separate backfill needed.
    op.add_column(
        "homework_jobs",
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="cli"),
    )
    op.add_column(
        "batches",
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="cli"),
    )
    op.add_column(
        "agent_usages",
        sa.Column("auth_mode", sa.String(length=8), nullable=False, server_default="cli"),
    )
    # Fold transport into the batches unique key (add-column above must run first
    # so batches.transport exists).
    op.drop_constraint("uq_batches_book_id", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport", "batches", ["book_id", "transport"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_batches_book_id_transport", "batches", type_="unique")
    # CAVEAT: recreating UNIQUE(book_id) here will fail if by now any book has
    # both a cli and an api batch. Acceptable for a dev migration — documented.
    op.create_unique_constraint("uq_batches_book_id", "batches", ["book_id"])
    op.drop_column("agent_usages", "auth_mode")
    op.drop_column("batches", "transport")
    op.drop_column("homework_jobs", "transport")
