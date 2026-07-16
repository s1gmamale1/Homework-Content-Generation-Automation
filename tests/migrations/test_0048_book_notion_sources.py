"""Real-DB: migration 0048 adds book_notion_sources (Notion page/block -> book
mapping) + books.toc_ready_at (worklog 0144 task 1, prepare-status-redo).

Recipe:
  createdb -h 127.0.0.1 -U macmini5 -O edu edu_scratch_prep
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_prep \
    uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/migrations/test_0048_book_notion_sources.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_DB_URL = os.getenv("DATABASE_URL", "")


def _run_alembic(cmd: list[str]) -> None:
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"
        )


async def _has_table(engine, name: str) -> bool:
    async with engine.begin() as conn:
        got = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": name})
    return got == name


async def _has_column(engine, table: str, column: str) -> bool:
    async with engine.begin() as conn:
        got = await conn.scalar(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
    return got == column


@pytest.mark.asyncio
async def test_0048_book_notion_sources_table_and_books_column():
    engine = create_async_engine(_DB_URL)
    try:
        # --- RED baseline: at 0047, neither exists ---
        _run_alembic(["downgrade", "0047_credential_slots"])
        assert not await _has_table(engine, "book_notion_sources")
        assert not await _has_column(engine, "books", "toc_ready_at")

        # --- GREEN: upgrade to head (0048) creates both ---
        _run_alembic(["upgrade", "head"])
        assert await _has_table(engine, "book_notion_sources")
        assert await _has_column(engine, "books", "toc_ready_at")

        # toc_ready_at is nullable
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name='books' AND column_name='toc_ready_at'"
                )
            )
            row = result.first()
        assert row.is_nullable == "YES"

        # --- downgrade removes both ---
        _run_alembic(["downgrade", "0047_credential_slots"])
        assert not await _has_table(engine, "book_notion_sources")
        assert not await _has_column(engine, "books", "toc_ready_at")
    finally:
        await engine.dispose()
        # Restore the shared scratch DB to head for any subsequent test in this run.
        _run_alembic(["upgrade", "head"])


@pytest.mark.asyncio
async def test_0048_unique_constraint_and_fk_cascade():
    engine = create_async_engine(_DB_URL)
    tag = uuid.uuid4().hex[:12]
    book_id = None
    try:
        _run_alembic(["upgrade", "head"])

        async with engine.begin() as conn:
            book_id = await conn.scalar(
                text(
                    "INSERT INTO books (id, subject, original_filename, content_sha256, "
                    "file_size_bytes, status, source_language, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'math', :fn, :sha, 100, 'toc_ready', 'uz', "
                    "now(), now()) RETURNING id"
                ),
                {"fn": f"book-{tag}.pdf", "sha": f"sha-{tag}"},
            )

        # First link inserts cleanly.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO book_notion_sources "
                    "(id, book_id, notion_page_id, notion_block_id, linked_at) "
                    "VALUES (gen_random_uuid(), :book_id, :page, :block, now())"
                ),
                {"book_id": book_id, "page": f"page-{tag}", "block": f"block-{tag}"},
            )

        # Same (page, block) pair a second time (even for a different book_id)
        # must violate the UNIQUE(notion_page_id, notion_block_id) constraint —
        # this is the raw-insert path; the repo's upsert_link uses ON CONFLICT
        # to update instead, tested separately at the repo layer.
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO book_notion_sources "
                        "(id, book_id, notion_page_id, notion_block_id, linked_at) "
                        "VALUES (gen_random_uuid(), :book_id, :page, :block, now())"
                    ),
                    {"book_id": book_id, "page": f"page-{tag}", "block": f"block-{tag}"},
                )

        # Deleting the book cascades to the source row (ondelete=CASCADE).
        async with engine.begin() as conn:
            remaining_before = await conn.scalar(
                text(
                    "SELECT count(*) FROM book_notion_sources WHERE notion_page_id=:page"
                ),
                {"page": f"page-{tag}"},
            )
        assert remaining_before == 1

        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM books WHERE id=:id"), {"id": book_id})

        async with engine.begin() as conn:
            remaining_after = await conn.scalar(
                text(
                    "SELECT count(*) FROM book_notion_sources WHERE notion_page_id=:page"
                ),
                {"page": f"page-{tag}"},
            )
        assert remaining_after == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM book_notion_sources WHERE notion_page_id=:page"),
                {"page": f"page-{tag}"},
            )
            await conn.execute(
                text("DELETE FROM books WHERE content_sha256=:sha"),
                {"sha": f"sha-{tag}"},
            )
        await engine.dispose()
