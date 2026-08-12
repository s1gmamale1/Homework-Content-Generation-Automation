"""Real-DB: output_language columns exist on all three tables with NOT NULL +
server_default 'uz', and the batches unique constraint has been renamed to
include output_language. Skipped unless RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_output_language_columns_exist_with_default_uz():
    from app.db import SessionLocal

    async with SessionLocal() as s:
        for tbl in ("homework_jobs", "batches", "launch_defaults"):
            row = (await s.execute(
                text(
                    "SELECT column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'output_language'"
                ),
                {"t": tbl},
            )).first()
            assert row is not None, f"{tbl}.output_language column is missing"
            assert "uz" in (row[0] or ""), (
                f"{tbl}.output_language server_default is not 'uz' (got {row[0]!r})"
            )
            assert row[1] == "NO", (
                f"{tbl}.output_language should be NOT NULL (is_nullable={row[1]!r})"
            )


@pytest.mark.asyncio
async def test_batches_unique_constraint_swapped():
    from app.db import SessionLocal

    async with SessionLocal() as s:
        names = [
            r[0]
            for r in (
                await s.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'batches'::regclass AND contype = 'u'"
                    )
                )
            ).all()
        ]
    assert "uq_batches_book_id_transport_output_language_kind" in names, (
        f"new unique constraint missing; found: {names}"
    )
    assert "uq_batches_book_id_transport" not in names, (
        f"old unique constraint still present; found: {names}"
    )
    assert "uq_batches_book_id_transport_output_language" not in names, (
        f"pre-0054 3-column unique constraint still present (should have been "
        f"widened by migration 0054); found: {names}"
    )
