"""Real-DB: migration 0049 flips launch_defaults (id=1) to the 3.x-flash
target tuple, from EITHER of the two starting states seen in the fleet —
a fresh-0048-seeded row and the current PROD row — and downgrade restores
the exact PROD tuple regardless of starting state (task 6, 2026-07-24
model-config-3x-flash plan).

Recipe:
  createdb -O edu edu_scratch_mig0049
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_mig0049 \
    RUN_DB_INTEGRATION=1 uv run alembic upgrade head
  DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run python -m pytest \
    tests/repositories/test_launch_defaults_migration.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_DB_URL = os.getenv("DATABASE_URL", "")

_COLUMNS = (
    "content_provider", "content_model", "content_transport",
    "extract_provider", "extract_model", "extract_transport",
    "judge_provider", "judge_model", "judge_transport",
    "solver_provider", "solver_model", "solver_transport",
    "toc_transport",
)

_TARGET_TUPLE = {
    "content_provider": "gemini", "content_model": "gemini-3.6-flash", "content_transport": "api",
    "extract_provider": "gemini", "extract_model": "gemini-3.5-flash-lite", "extract_transport": "api",
    "judge_provider": "gemini", "judge_model": "gemini-3.5-flash", "judge_transport": "api",
    "solver_provider": "gemini", "solver_model": "gemini-3.1-pro-preview", "solver_transport": "api",
    "toc_transport": "api",
}

_PROD_TUPLE = {
    "content_provider": "gemini", "content_model": "gemini-3-flash-preview", "content_transport": "api",
    "extract_provider": "gemini", "extract_model": "gemini-2.5-flash", "extract_transport": "api",
    "judge_provider": "gemini", "judge_model": "gemini-2.5-flash", "judge_transport": "api",
    "solver_provider": "gemini", "solver_model": "gemini-3.1-pro-preview", "solver_transport": "api",
    "toc_transport": "api",
}

_FRESH_0048_TUPLE = {
    "content_provider": "gemini", "content_model": "gemini-2.5-pro", "content_transport": "api",
    "extract_provider": "gemini", "extract_model": "gemini-2.5-flash", "extract_transport": "inherit",
    "judge_provider": "gemini", "judge_model": "gemini-2.5-flash", "judge_transport": "inherit",
    "solver_provider": "gemini", "solver_model": "gemini-3.1-pro-preview", "solver_transport": "inherit",
    "toc_transport": "cli",
}


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


async def _seed(engine, tuple_: dict) -> None:
    cols = ", ".join(f"{c} = :{c}" for c in tuple_)
    async with engine.begin() as conn:
        await conn.execute(text(f"UPDATE launch_defaults SET {cols} WHERE id = 1"), tuple_)


async def _fetch(engine) -> dict:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"SELECT {', '.join(_COLUMNS)} FROM launch_defaults WHERE id = 1")
        )
        row = result.mappings().first()
    return dict(row)


@pytest.mark.asyncio
async def test_0049_upgrade_from_fresh_0048_tuple_lands_on_target():
    engine = create_async_engine(_DB_URL)
    try:
        _run_alembic(["downgrade", "0048_book_notion_sources"])
        await _seed(engine, _FRESH_0048_TUPLE)
        assert await _fetch(engine) == _FRESH_0048_TUPLE

        _run_alembic(["upgrade", "0049_launch_defaults_3x"])
        assert await _fetch(engine) == _TARGET_TUPLE

        _run_alembic(["downgrade", "0048_book_notion_sources"])
        assert await _fetch(engine) == _PROD_TUPLE
    finally:
        await engine.dispose()
        _run_alembic(["upgrade", "head"])


@pytest.mark.asyncio
async def test_0049_upgrade_from_prod_tuple_lands_on_target():
    engine = create_async_engine(_DB_URL)
    try:
        _run_alembic(["downgrade", "0048_book_notion_sources"])
        await _seed(engine, _PROD_TUPLE)
        assert await _fetch(engine) == _PROD_TUPLE

        _run_alembic(["upgrade", "0049_launch_defaults_3x"])
        assert await _fetch(engine) == _TARGET_TUPLE

        _run_alembic(["downgrade", "0048_book_notion_sources"])
        assert await _fetch(engine) == _PROD_TUPLE
    finally:
        await engine.dispose()
        _run_alembic(["upgrade", "head"])
