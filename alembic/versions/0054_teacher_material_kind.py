"""jobs/batches: kind discriminator for teacher material, widen batch key

Revision ID: 0054_teacher_material_kind
Revises: 0053_solver_mismatch_blocked
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_teacher_material_kind"
down_revision = "0053_solver_mismatch_blocked"
branch_labels = None
depends_on = None

_CK = "kind IN ('homework','teacher_material')"


def upgrade() -> None:
    op.add_column("homework_jobs", sa.Column(
        "kind", sa.String(32), nullable=False, server_default="homework"))
    op.create_check_constraint("ck_homework_jobs_kind", "homework_jobs", _CK)

    op.add_column("batches", sa.Column(
        "kind", sa.String(32), nullable=False, server_default="homework"))

    op.drop_constraint(
        "uq_batches_book_id_transport_output_language", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport_output_language_kind", "batches",
        ["book_id", "transport", "output_language", "kind"])


def downgrade() -> None:
    op.drop_constraint(
        "uq_batches_book_id_transport_output_language_kind", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport_output_language", "batches",
        ["book_id", "transport", "output_language"])
    op.drop_column("batches", "kind")

    op.drop_constraint("ck_homework_jobs_kind", "homework_jobs", type_="check")
    op.drop_column("homework_jobs", "kind")
