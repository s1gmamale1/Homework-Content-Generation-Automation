"""launch_defaults singleton — UI-managed judge/extract/TOC defaults; backfill jobs

Revision ID: 0037_launch_defaults
Revises: 0036_session_limit_strategy
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_launch_defaults"
down_revision = "0036_session_limit_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "launch_defaults",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("judge_provider", sa.String(length=32), nullable=True),
        sa.Column("judge_model", sa.String(length=128), nullable=True),
        sa.Column("judge_transport", sa.String(length=16), nullable=True),
        sa.Column("extract_provider", sa.String(length=32), nullable=True),
        sa.Column("extract_model", sa.String(length=128), nullable=True),
        sa.Column("extract_transport", sa.String(length=16), nullable=True),
        sa.Column("toc_transport", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_launch_defaults_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the singleton with literal default values (no .env/settings read).
    op.execute(
        """
        INSERT INTO launch_defaults
            (id, judge_provider, judge_model, judge_transport,
             extract_provider, extract_model, extract_transport,
             toc_transport, updated_at)
        VALUES
            (1, 'gemini', 'gemini-2.5-flash', 'inherit',
             'gemini', 'gemini-2.5-flash', 'inherit',
             'cli', now())
        """
    )
    # Backfill EVERY pre-existing job with NULL judge/extract columns (no status
    # filter): the claim gate's settings hint is dropped in this release, and the
    # user actively retries/resumes failed+cancelled jobs — retry reuses the row
    # WITHOUT re-stamping, so a failed/cancelled Auto job retried post-deploy would
    # strand unclaimable. COALESCE only writes NULL columns, so backfilling done
    # rows is harmless. This fully delivers the locked "nothing strands" goal.
    op.execute(
        """
        UPDATE homework_jobs
           SET judge_provider   = COALESCE(judge_provider,   'gemini'),
               judge_model      = COALESCE(judge_model,      'gemini-2.5-flash'),
               extract_provider = COALESCE(extract_provider, 'gemini'),
               extract_model    = COALESCE(extract_model,    'gemini-2.5-flash')
         WHERE judge_provider IS NULL OR judge_model IS NULL
            OR extract_provider IS NULL OR extract_model IS NULL
        """
    )


def downgrade() -> None:
    op.drop_table("launch_defaults")
