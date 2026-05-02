"""homework_jobs: queue columns + queue index

Revision ID: a3f5e2d18c44
Revises: e4a87cd16f02
Create Date: 2026-05-02

Turns `homework_jobs` into a Postgres-backed work queue using the
`SELECT ... FOR UPDATE SKIP LOCKED` pattern. Adds:

  - `priority` — higher first; user-triggered = 0, batch reruns = -10
  - `scheduled_at` — earliest time a worker may claim this job (for
                     delayed execution / exponential backoff)
  - `claimed_at` / `claimed_by` — provenance + stuck-job detection
  - `attempts` / `last_attempt_at` — retry bookkeeping
  - `last_error` — last failure message for telemetry; cleared on retry

Plus a partial index on (status='pending', scheduled_at, priority) so the
worker's claim query stays cheap regardless of how many `done` rows exist.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f5e2d18c44"
down_revision: Union[str, Sequence[str], None] = "e4a87cd16f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "homework_jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "homework_jobs",
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    # Partial index: only rows the worker actually scans. Most rows in
    # this table are `done` and shouldn't appear here. PostgreSQL
    # narrows the index to ~live-queue rows so the claim query is O(1)
    # regardless of total table size.
    op.create_index(
        "ix_homework_jobs_queue",
        "homework_jobs",
        ["scheduled_at", sa.text("priority DESC")],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Promote any `running` jobs left over from before the queue migration
    # back to `pending` so the new worker can reclaim them. Same for
    # legacy `pending` rows that were never claimed.
    op.execute(
        """
        UPDATE homework_jobs
           SET status = 'pending',
               claimed_at = NULL,
               claimed_by = NULL,
               started_at = NULL,
               current_phase = NULL
         WHERE status IN ('running', 'pending')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_homework_jobs_queue", table_name="homework_jobs")
    op.drop_column("homework_jobs", "last_error")
    op.drop_column("homework_jobs", "last_attempt_at")
    op.drop_column("homework_jobs", "attempts")
    op.drop_column("homework_jobs", "claimed_by")
    op.drop_column("homework_jobs", "claimed_at")
    op.drop_column("homework_jobs", "scheduled_at")
    op.drop_column("homework_jobs", "priority")
