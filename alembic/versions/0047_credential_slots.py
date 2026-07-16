"""credential_slots: fleet-wide per-credential api concurrency limiter (BE-16 task 1).

Revision ID: 0047_credential_slots
Revises: 0046_worker_version_floor
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_credential_slots"
down_revision: Union[str, Sequence[str], None] = "0046_worker_version_floor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # credential_slots has NO SQLAlchemy model (deliberate — schema-only task).
    # The limiter (Task 3) accesses this table exclusively via raw SQL text()
    # calls, so `id` generation must happen server-side rather than via an
    # ORM-level Python default; gen_random_uuid() is built into PostgreSQL
    # core since v13 (no pgcrypto extension needed on this repo's PG 16).
    op.create_table(
        "credential_slots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("credential", sa.Text(), nullable=False),
        sa.Column("pc_id", sa.Text(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_credential_slots_credential", "credential_slots", ["credential"]
    )
    op.add_column(
        "sa_keys", sa.Column("max_concurrent_calls", sa.Integer(), nullable=True)
    )
    # Task 4 amendment: reject 0/negative overrides at the DB layer too (not
    # just pydantic `Field(ge=1)` on the settings default — this column has
    # no pydantic gate of its own since it's written via raw upload/edit
    # paths). NULL (no override) stays valid; 0 would otherwise cause
    # `acquire()` to silently interpret the row as "no cap" (its BYPASS
    # sentinel treats <=0 as unbounded) rather than "block everything",
    # which is never the intent of an admin-set override.
    op.create_check_constraint(
        "ck_sa_keys_max_concurrent_calls_min",
        "sa_keys",
        "max_concurrent_calls IS NULL OR max_concurrent_calls >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_sa_keys_max_concurrent_calls_min", "sa_keys", type_="check"
    )
    op.drop_column("sa_keys", "max_concurrent_calls")
    op.drop_index("ix_credential_slots_credential", table_name="credential_slots")
    op.drop_table("credential_slots")
