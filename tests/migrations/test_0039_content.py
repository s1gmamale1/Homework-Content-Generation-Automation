"""Real-DB: 0039 adds content_* columns + seeds gemini-2.5-pro (must differ from the judge default flash)."""
from __future__ import annotations
import os, subprocess, sys
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres")
_DB_URL = os.getenv("DATABASE_URL", "")
_SYNC_URL = _DB_URL.replace("postgresql+asyncpg://", "postgresql://")

def _run_alembic(cmd):
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    r = subprocess.run([sys.executable, "-m", "alembic"] + cmd, capture_output=True, text=True, env=env,
                       cwd=os.path.join(os.path.dirname(__file__), "..", ".."))
    if r.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")

async def test_0039_adds_content_columns_and_seeds():
    import asyncpg
    _run_alembic(["downgrade", "0038_output_language"])
    _run_alembic(["upgrade", "0039_launch_defaults_content"])
    conn = await asyncpg.connect(_SYNC_URL)
    try:
        row = await conn.fetchrow(
            "SELECT content_provider, content_model, content_transport FROM launch_defaults WHERE id=1")
        assert row["content_provider"] == "gemini"
        assert row["content_model"] == "gemini-2.5-pro"
        assert row["content_transport"] == "api"
    finally:
        await conn.close()
