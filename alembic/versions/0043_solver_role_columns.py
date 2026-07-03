"""solver role columns + phase_outputs.solver_status (CQ-C / R21.2)"""
from alembic import op
import sqlalchemy as sa

revision = "0043_solver_role_columns"
down_revision = "0042_books_toc_validation"  # re-verify current head at execution
branch_labels = None
depends_on = None

_TXN = ("cli", "api", "inherit")
_STATUS = ("ok", "mismatch_regen", "mismatch_shipped", "mismatch_regen_failed",
           "unavailable", "refused")


def upgrade() -> None:
    op.add_column("launch_defaults", sa.Column("solver_provider", sa.String(32), nullable=True))
    op.add_column("launch_defaults", sa.Column("solver_model", sa.String(128), nullable=True))
    op.add_column("launch_defaults", sa.Column("solver_transport", sa.String(16), nullable=True))
    for tbl in ("homework_jobs", "batches"):
        op.add_column(tbl, sa.Column("solver_transport", sa.String(16),
                                     nullable=False, server_default="inherit"))
        op.add_column(tbl, sa.Column("solver_provider", sa.String(32), nullable=True))
        op.add_column(tbl, sa.Column("solver_model", sa.String(128), nullable=True))
        op.create_check_constraint(
            f"ck_{tbl}_solver_transport", tbl,
            "solver_transport IN " + str(_TXN))
    op.add_column("phase_outputs", sa.Column("solver_status", sa.String(24), nullable=True))
    op.create_check_constraint(
        "ck_phase_outputs_solver_status", "phase_outputs",
        "solver_status IS NULL OR solver_status IN " + str(_STATUS))
    # R2: seed the singleton launch_defaults row so the fleet default is the
    # cheap, Vertex-native frontier solver (no ANTHROPIC key on the common path).
    # No-op if the row doesn't exist yet — the app's ensure-defaults path (Task 5)
    # supplies the same values on first create.
    op.execute(
        "UPDATE launch_defaults SET solver_provider='gemini', "
        "solver_model='gemini-3.1-pro-preview', solver_transport='inherit' "
        "WHERE solver_provider IS NULL")
    # Backfill EVERY pre-existing homework_jobs row with a NULL solver stamp
    # (no status filter) — mirrors 0037's judge/extract backfill. add_column
    # above left existing rows solver_provider=NULL + solver_transport='inherit'
    # (server_default); once the solver claim-gate (Task 8) goes live, such an
    # api job would resolve `_provider_api_ok(NULL)` → SQL NULL → excluded →
    # unclaimable by ANY worker. Retry/resume reuses the row WITHOUT re-stamping,
    # so a pre-0043 pending/failed api job would strand. COALESCE only writes
    # NULLs, so backfilling done rows is harmless.
    op.execute(
        """
        UPDATE homework_jobs
           SET solver_provider = COALESCE(solver_provider, 'gemini'),
               solver_model    = COALESCE(solver_model,    'gemini-3.1-pro-preview')
         WHERE solver_provider IS NULL OR solver_model IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.drop_column("phase_outputs", "solver_status")
    for tbl in ("homework_jobs", "batches"):
        op.drop_constraint(f"ck_{tbl}_solver_transport", tbl)
        op.drop_column(tbl, "solver_model")
        op.drop_column(tbl, "solver_provider")
        op.drop_column(tbl, "solver_transport")
    for col in ("solver_transport", "solver_model", "solver_provider"):
        op.drop_column("launch_defaults", col)
