"""cap-pause provenance: which host paused, and at what cap

Both pause gates are FLEET-WIDE flags (`batches.paused_at`,
`budget_state.api_paused_at`) but were decided against each worker's OWN env
cap. During the 38-host operation with an uneven cap rollout (7 hosts still on
COST_CAP_BATCH_USD=50, 31 on 2000) a stale worker paused a batch and a patched
worker unpaused it on its next tick, forever — and nothing recorded who had
paused it or under which cap.

These columns make the decision self-describing: the effective cap that tripped
the pause and the worker id (`hostname:pid@sha`) that decided it. The budget
monitor refuses to lift a cap pause recorded under a STRICTER cap than its own
(see `worker._may_lift_cap_pause`), which is what stops the oscillation.

NULL = no provenance: a manual/operator pause, or a cap pause written before
this migration. Those keep the historical permissive reconcile so they drain.

Revision ID: 0062_cap_pause_provenance
Revises: 0059_toc_teacher_deck_notion

NOTE (parallel-worktree integration): the head in this worktree is 0059. If
migrations 0060/0061 land from another branch, re-point `down_revision` at
0061 before merging — the file is numbered 0062 so it sorts after them.
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_cap_pause_provenance"
down_revision = "0061_credential_slot_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batches",
        sa.Column("paused_cap_usd", sa.Double(), nullable=True),
    )
    op.add_column(
        "batches",
        sa.Column("paused_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "budget_state",
        sa.Column("api_paused_cap_usd", sa.Double(), nullable=True),
    )
    op.add_column(
        "budget_state",
        sa.Column("api_paused_by", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_state", "api_paused_by")
    op.drop_column("budget_state", "api_paused_cap_usd")
    op.drop_column("batches", "paused_by")
    op.drop_column("batches", "paused_cap_usd")
