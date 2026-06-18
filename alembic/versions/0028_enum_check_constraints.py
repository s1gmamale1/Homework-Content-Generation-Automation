"""CHECK constraints for enum-like columns on homework_jobs and batches.

Adds DB-level guards for status / transport / extract_transport / judge_transport
so a bad value fails at INSERT/UPDATE rather than deep in the pipeline.

Small tables — a brief lock is acceptable; NOT VALID is not needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_enum_check_constraints"
down_revision: Union[str, Sequence[str], None] = "0027_per_role_provider_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # homework_jobs constraints
    op.create_check_constraint(
        "ck_homework_jobs_status",
        "homework_jobs",
        "status IN ('pending','running','done','failed','cancelling','cancelled')",
    )
    op.create_check_constraint(
        "ck_homework_jobs_transport",
        "homework_jobs",
        "transport IN ('cli','api')",
    )
    op.create_check_constraint(
        "ck_homework_jobs_extract_transport",
        "homework_jobs",
        "extract_transport IN ('cli','api','inherit')",
    )
    op.create_check_constraint(
        "ck_homework_jobs_judge_transport",
        "homework_jobs",
        "judge_transport IN ('cli','api','inherit')",
    )

    # batches constraints (no status column on batches)
    op.create_check_constraint(
        "ck_batches_transport",
        "batches",
        "transport IN ('cli','api')",
    )
    op.create_check_constraint(
        "ck_batches_extract_transport",
        "batches",
        "extract_transport IN ('cli','api','inherit')",
    )
    op.create_check_constraint(
        "ck_batches_judge_transport",
        "batches",
        "judge_transport IN ('cli','api','inherit')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_homework_jobs_status", "homework_jobs", type_="check")
    op.drop_constraint("ck_homework_jobs_transport", "homework_jobs", type_="check")
    op.drop_constraint("ck_homework_jobs_extract_transport", "homework_jobs", type_="check")
    op.drop_constraint("ck_homework_jobs_judge_transport", "homework_jobs", type_="check")

    op.drop_constraint("ck_batches_transport", "batches", type_="check")
    op.drop_constraint("ck_batches_extract_transport", "batches", type_="check")
    op.drop_constraint("ck_batches_judge_transport", "batches", type_="check")
