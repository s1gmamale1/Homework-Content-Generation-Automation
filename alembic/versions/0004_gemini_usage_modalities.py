"""gemini_usages: drop input_audio, add image/prompt/cached columns

Revision ID: 92e8c4d10aa1
Revises: f42a38524dfd
Create Date: 2026-05-01

This project never has audio input but does have image input (PDF tokens
render under modality=IMAGE). Drop the dead audio column, add image, and
surface the SDK's prompt_token_count + cached_content_token_count as
queryable columns instead of leaving them buried in the usage_metadata jsonb.

Backfills the new columns from usage_metadata for existing rows so the
historical data stays useful.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "92e8c4d10aa1"
down_revision: Union[str, Sequence[str], None] = "f42a38524dfd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "gemini_usages",
        sa.Column("prompt_token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gemini_usages",
        sa.Column("input_image_token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gemini_usages",
        sa.Column(
            "cached_content_token_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )

    # Backfill prompt_token_count + cached_content_token_count from usage_metadata.
    op.execute(
        """
        UPDATE gemini_usages
        SET prompt_token_count = COALESCE((usage_metadata->>'prompt_token_count')::int, 0),
            cached_content_token_count =
                COALESCE((usage_metadata->>'cached_content_token_count')::int, 0)
        WHERE usage_metadata IS NOT NULL
        """
    )

    # Backfill input_image_token_count by summing IMAGE-modality entries in
    # the prompt_tokens_details array.
    op.execute(
        """
        UPDATE gemini_usages
        SET input_image_token_count = COALESCE((
            SELECT SUM((d->>'token_count')::int)
            FROM jsonb_array_elements(usage_metadata->'prompt_tokens_details') AS d
            WHERE d->>'modality' = 'IMAGE'
        ), 0)
        WHERE usage_metadata IS NOT NULL
          AND jsonb_typeof(usage_metadata->'prompt_tokens_details') = 'array'
        """
    )

    # Drop server defaults — columns are now ORM-managed.
    op.alter_column("gemini_usages", "prompt_token_count", server_default=None)
    op.alter_column("gemini_usages", "input_image_token_count", server_default=None)
    op.alter_column("gemini_usages", "cached_content_token_count", server_default=None)

    # Drop the dead audio column.
    op.drop_column("gemini_usages", "input_audio_token_count")


def downgrade() -> None:
    op.add_column(
        "gemini_usages",
        sa.Column(
            "input_audio_token_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column("gemini_usages", "input_audio_token_count", server_default=None)
    op.drop_column("gemini_usages", "cached_content_token_count")
    op.drop_column("gemini_usages", "input_image_token_count")
    op.drop_column("gemini_usages", "prompt_token_count")
