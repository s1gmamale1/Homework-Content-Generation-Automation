"""provider-agnostic usage: rename gemini_usages -> agent_usages, add provider/model on jobs

Revision ID: b71d3a4f6c20
Revises: a3f5e2d18c44
Create Date: 2026-05-06

Two related changes that prepare the schema for multi-provider LLM support:

1. `homework_jobs` learns which backend produced its output:
   - `provider` (NOT NULL, default 'gemini') — pinned at job creation so
     retries hit the same backend even after the global default flips.
   - `model` (nullable) — the specific model id within the provider; nullable
     because some providers default at the SDK level.

2. `gemini_usages` becomes `agent_usages` — provider-neutral. The
   modality-specific token columns (only Gemini ever populated them) are
   dropped, and the remaining counts are renamed to provider-neutral names:
       prompt_token_count          -> prompt_tokens
       candidates_token_count      -> output_tokens
       cached_content_token_count  -> cached_tokens
       total_token_count           -> total_tokens
       usage_metadata              -> raw_envelope
   Indexes are renamed in lock-step (`ix_gemini_usages_*` -> `ix_agent_usages_*`)
   and a new `ix_agent_usages_provider` index is added for billing breakdowns.

Existing rows in both tables backfill `provider = 'gemini'` (every row was
written by the Gemini service before this migration).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b71d3a4f6c20"
down_revision: Union[str, Sequence[str], None] = "a3f5e2d18c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── homework_jobs: provider + model ───────────────────────────────────
    op.add_column(
        "homework_jobs",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="gemini",
        ),
    )
    op.add_column(
        "homework_jobs",
        sa.Column("model", sa.String(length=128), nullable=True),
    )

    # ── gemini_usages -> agent_usages ────────────────────────────────────
    # Rename indexes BEFORE the table rename (Postgres carries indexes with
    # the table, but renaming the index name itself is a separate op).
    op.execute("ALTER INDEX ix_gemini_usages_book RENAME TO ix_agent_usages_book")
    op.execute("ALTER INDEX ix_gemini_usages_job RENAME TO ix_agent_usages_job")
    op.execute("ALTER INDEX ix_gemini_usages_phase RENAME TO ix_agent_usages_phase")
    op.execute("ALTER INDEX ix_gemini_usages_operation RENAME TO ix_agent_usages_operation")
    op.execute(
        "ALTER INDEX ix_gemini_usages_created_at_desc "
        "RENAME TO ix_agent_usages_created_at_desc"
    )

    op.rename_table("gemini_usages", "agent_usages")

    # Drop modality-specific columns (Gemini-only payload).
    op.drop_column("agent_usages", "input_text_token_count")
    op.drop_column("agent_usages", "input_image_token_count")
    op.drop_column("agent_usages", "thoughts_token_count")

    # Rename the remaining count columns to provider-neutral names.
    op.alter_column("agent_usages", "prompt_token_count", new_column_name="prompt_tokens")
    op.alter_column(
        "agent_usages", "candidates_token_count", new_column_name="output_tokens"
    )
    op.alter_column(
        "agent_usages", "cached_content_token_count", new_column_name="cached_tokens"
    )
    op.alter_column("agent_usages", "total_token_count", new_column_name="total_tokens")
    op.alter_column("agent_usages", "usage_metadata", new_column_name="raw_envelope")

    # Add `provider` column (backfills 'gemini' for existing rows via server_default).
    op.add_column(
        "agent_usages",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="gemini",
        ),
    )

    # New index on provider for billing breakdowns.
    op.create_index(
        "ix_agent_usages_provider", "agent_usages", ["provider"], unique=False
    )


def downgrade() -> None:
    # Reverse Change 2 first, then Change 1.

    op.drop_index("ix_agent_usages_provider", table_name="agent_usages")
    op.drop_column("agent_usages", "provider")

    # Reverse the column renames.
    op.alter_column("agent_usages", "raw_envelope", new_column_name="usage_metadata")
    op.alter_column("agent_usages", "total_tokens", new_column_name="total_token_count")
    op.alter_column(
        "agent_usages", "cached_tokens", new_column_name="cached_content_token_count"
    )
    op.alter_column(
        "agent_usages", "output_tokens", new_column_name="candidates_token_count"
    )
    op.alter_column("agent_usages", "prompt_tokens", new_column_name="prompt_token_count")

    # Re-add the modality-specific columns. Default 0 so the NOT NULL holds
    # for existing rows; clear the server_default afterwards so the ORM
    # owns the column going forward (matches 0004's pattern).
    op.add_column(
        "agent_usages",
        sa.Column(
            "input_text_token_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "agent_usages",
        sa.Column(
            "input_image_token_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "agent_usages",
        sa.Column(
            "thoughts_token_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column("agent_usages", "input_text_token_count", server_default=None)
    op.alter_column("agent_usages", "input_image_token_count", server_default=None)
    op.alter_column("agent_usages", "thoughts_token_count", server_default=None)

    op.rename_table("agent_usages", "gemini_usages")

    op.execute("ALTER INDEX ix_agent_usages_book RENAME TO ix_gemini_usages_book")
    op.execute("ALTER INDEX ix_agent_usages_job RENAME TO ix_gemini_usages_job")
    op.execute("ALTER INDEX ix_agent_usages_phase RENAME TO ix_gemini_usages_phase")
    op.execute("ALTER INDEX ix_agent_usages_operation RENAME TO ix_gemini_usages_operation")
    op.execute(
        "ALTER INDEX ix_agent_usages_created_at_desc "
        "RENAME TO ix_gemini_usages_created_at_desc"
    )

    # homework_jobs: drop provider + model.
    op.drop_column("homework_jobs", "model")
    op.drop_column("homework_jobs", "provider")
