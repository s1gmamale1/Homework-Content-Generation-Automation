"""Cross-process SSE bus proof (sse-multipod-1).

A publisher running in a SEPARATE PROCESS (like every fleet worker) must
reach a subscriber in THIS process. On the old in-process dict bus the
subprocess publishes into its own dict and this test fails on timeout —
that failure IS the frozen-"Queued"-chip bug. The ``getattr`` fallbacks for
``start_listener``/``stop_listener`` are kept so the test demonstrates the
gap when run against the pre-NOTIFY bus (RED provenance).

Needs RUN_DB_INTEGRATION=1 + a real DATABASE_URL (scratch DB, 127.0.0.1).
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Publishes one small event (must arrive inline), one oversized toc_ready
# (must arrive as a refetch marker), then close() — from its own interpreter,
# exactly like a fleet worker process.
PUBLISH_SCRIPT = """
import asyncio, sys

from app.services import events_bus

async def main():
    rid = sys.argv[1]
    await events_bus.publish(
        rid, "phase_started", {"phase_name": "flashcards", "phase_order": 2}
    )
    big = {"entries": [
        {"section_title": "L" * 80, "order_index": i} for i in range(120)
    ]}
    await events_bus.publish(rid, "toc_ready", big)
    await events_bus.close(rid)

asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_cross_process_publish_reaches_local_subscriber():
    from app.services import events_bus

    start = getattr(events_bus, "start_listener", None)
    stop = getattr(events_bus, "stop_listener", None)
    if start is not None:
        await start()
    rid = f"book:{uuid4()}"
    q = events_bus.subscribe(rid)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", PUBLISH_SCRIPT, rid,
            cwd=str(REPO_ROOT), env=dict(os.environ),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        assert proc.returncode == 0, f"publisher failed: {err.decode()[-2000:]}"

        first = await asyncio.wait_for(q.get(), timeout=10)
        assert first == {
            "event": "phase_started",
            "data": {"phase_name": "flashcards", "phase_order": 2},
        }

        second = await asyncio.wait_for(q.get(), timeout=10)
        assert second["event"] == "toc_ready"
        assert second["data"].get("__refetch__") is True   # oversized → marker
        assert "entries" not in second["data"]             # big field dropped

        sentinel = await asyncio.wait_for(q.get(), timeout=10)
        assert sentinel is None                            # close() crosses too
    finally:
        events_bus.unsubscribe(rid, q)
        if stop is not None:
            await stop()


@pytest.mark.real_events_bus
@pytest.mark.asyncio
async def test_publish_fires_despite_unrelated_open_transaction():
    """C1: pg_notify is transactional — fires on commit, dropped on rollback.
    publish() must run it on its own short-lived committed connection, never
    enlisted in an ambient caller transaction (which would delay delivery
    until that commit, or swallow it on rollback)."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services import events_bus

    start = getattr(events_bus, "start_listener", None)
    stop = getattr(events_bus, "stop_listener", None)
    if start is not None:
        await start()
    rid = f"job:{uuid4()}"
    q = events_bus.subscribe(rid)
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))   # ambient tx now OPEN, never committed
            await events_bus.publish(rid, "phase_started", {"phase_order": 1})
            # Delivered BEFORE the ambient tx resolves → publish did not enlist.
            got = await asyncio.wait_for(q.get(), timeout=10)
            assert got == {"event": "phase_started", "data": {"phase_order": 1}}
            await s.rollback()                  # and rollback can't retract it
    finally:
        events_bus.unsubscribe(rid, q)
        if stop is not None:
            await stop()
