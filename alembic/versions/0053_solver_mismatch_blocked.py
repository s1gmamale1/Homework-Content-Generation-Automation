"""Add fail-closed solver outcome for persistent answer-key mismatches."""

from alembic import op


revision = "0053_solver_mismatch_blocked"
down_revision = "0052_job_lease_fencing"
branch_labels = None
depends_on = None

_OLD_STATUS = (
    "ok",
    "mismatch_regen",
    "mismatch_shipped",
    "mismatch_regen_failed",
    "unavailable",
    "refused",
)
_STATUS = (*_OLD_STATUS, "mismatch_blocked")


def _constraint(values: tuple[str, ...]) -> str:
    return "solver_status IS NULL OR solver_status IN " + str(values)


def upgrade() -> None:
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.create_check_constraint(
        "ck_phase_outputs_solver_status",
        "phase_outputs",
        _constraint(_STATUS),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE phase_outputs SET solver_status='mismatch_shipped' "
        "WHERE solver_status='mismatch_blocked'"
    )
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.create_check_constraint(
        "ck_phase_outputs_solver_status",
        "phase_outputs",
        _constraint(_OLD_STATUS),
    )
