"""Add sa_keys + sa_key_assignments (web SA-key upload + worker auto-distribution)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_sa_keys"
down_revision: Union[str, Sequence[str], None] = "0040_books_source_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sa_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("client_email", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_sa_keys_sha256"),
    )
    op.create_table(
        "sa_key_assignments",
        sa.Column("hostname", sa.Text(), primary_key=True),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scrub_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["key_id"], ["sa_keys.id"], name="fk_sa_key_assignments_key_id",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("sa_key_assignments")
    op.drop_table("sa_keys")
