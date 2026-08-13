"""Real-DB: migration 0060 adds credential_slots.slot_index + the per-slot
UNIQUE(credential, slot_index) that replaced the fleet-wide advisory lock.

That unique index is the whole enforcement mechanism now — the ceiling is a
property of the schema, not of a critical section the acquirer has to hold —
so it gets its own bites-proof.

Recipe: same as test_0047_credential_slots.py.
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
async def test_0060_slot_index_column_unique_index_and_backfill():
    engine = create_async_engine(_DB_URL)
    cred = f"test-cred-0060-{uuid.uuid4().hex[:12]}"
    try:
        # --- RED baseline: at 0059 there is no slot_index ---
        _run_alembic(["downgrade", "0059_toc_teacher_deck_notion"])
        assert not await _has_column(engine, "credential_slots", "slot_index")

        # Seed rows the OLD way (no slot_index) so the backfill has work to
        # do — three live slots for one credential, as a running fleet would
        # have mid-deploy.
        async with engine.begin() as conn:
            for pc in ("host-a:1", "host-b:2", "host-c:3"):
                await conn.execute(
                    text(
                        "INSERT INTO credential_slots (credential, pc_id) "
                        "VALUES (:cred, :pc)"
                    ),
                    {"cred": cred, "pc": pc},
                )

        # --- GREEN: 0060 adds the column, backfills, and makes it NOT NULL ---
        _run_alembic(["upgrade", "head"])
        assert await _has_column(engine, "credential_slots", "slot_index")

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT slot_index FROM credential_slots "
                        "WHERE credential = :cred ORDER BY slot_index"
                    ),
                    {"cred": cred},
                )
            ).scalars().all()
        # In-flight slots SURVIVE the deploy (deleting them would under-count
        # real concurrency) and are packed densely from 0.
        assert rows == [0, 1, 2]

        async with engine.begin() as conn:
            nullable = await conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name='credential_slots' AND column_name='slot_index'"
                )
            )
            idx = await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname=:i"),
                {"i": "uq_credential_slots_credential_slot_index"},
            )
        assert nullable == "NO"
        assert idx is not None and "UNIQUE" in idx

        # The bite: one credential can never hold the same slot index twice.
        # This is what bounds the ceiling now that no advisory lock does.
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO credential_slots "
                        "(credential, slot_index, pc_id) VALUES (:cred, 0, 'dupe')"
                    ),
                    {"cred": cred},
                )

        # A DIFFERENT credential reuses the same index freely — the index is
        # per credential, not global.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO credential_slots "
                    "(credential, slot_index, pc_id) VALUES (:cred, 0, 'other')"
                ),
                {"cred": cred + "-other"},
            )

        # --- downgrade removes both ---
        _run_alembic(["downgrade", "0059_toc_teacher_deck_notion"])
        assert not await _has_column(engine, "credential_slots", "slot_index")
        async with engine.begin() as conn:
            idx = await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname=:i"),
                {"i": "uq_credential_slots_credential_slot_index"},
            )
        assert idx is None
    finally:
        _run_alembic(["upgrade", "head"])
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM credential_slots WHERE credential LIKE :pat"),
                {"pat": f"{cred}%"},
            )
        await engine.dispose()
