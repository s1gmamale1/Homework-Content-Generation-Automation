"""add output_language to jobs, batches, launch_defaults

Revision ID: 0038_output_language
Revises: 0037_launch_defaults
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_output_language"
down_revision = "0037_launch_defaults"
branch_labels = None
depends_on = None

_CK = "output_language IN ('uz','en','ru')"


def upgrade() -> None:
    for tbl in ("homework_jobs", "batches", "launch_defaults"):
        op.add_column(tbl, sa.Column(
            "output_language", sa.String(), nullable=False, server_default="uz"))
        op.create_check_constraint(f"ck_{tbl}_output_language", tbl, _CK)
    op.drop_constraint("uq_batches_book_id_transport", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport_output_language", "batches",
        ["book_id", "transport", "output_language"])


def downgrade() -> None:
    op.drop_constraint(
        "uq_batches_book_id_transport_output_language", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport", "batches", ["book_id", "transport"])
    for tbl in ("homework_jobs", "batches", "launch_defaults"):
        op.drop_constraint(f"ck_{tbl}_output_language", tbl, type_="check")
        op.drop_column(tbl, "output_language")
