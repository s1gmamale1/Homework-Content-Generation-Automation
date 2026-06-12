# Fleet Phase 1 — Workers Registry + Liveness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Build in the worktree `C:/Users/Recruiter/Desktop/homework-fleet-engine` (branch `feat/autonomous-fleet-engine`). No `.venv` in the worktree — use the main repo venv python `C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe` with cwd = worktree.

**Goal:** Give the head a live registry of which worker PCs are up — a `workers` table each worker heartbeats into, a liveness query (online = heartbeat fresh), and a read endpoint the dashboard (Phase 3) will consume.

**Architecture:** A new `workers` table keyed by the worker's `hostname:pid` id. Each worker upserts its `last_heartbeat` on startup and on a throttle in its main loop (reusing the existing loop, like the stuck-job sweep). A repo computes `online` from a staleness threshold. A `GET /api/v1/workers` endpoint exposes the list. **Reclaim/self-healing already shipped (worklog 0031) — NOT rebuilt here.**

**Tech Stack:** SQLAlchemy async + asyncpg, Alembic, FastAPI, pytest (+ guarded real-DB integration tests, same pattern as Phase 0).

---

## Reality note (verified against live code)
- Migrations: `alembic/versions/NNNN_desc.py`; latest is `0021_notion_skip_reason.py` with `revision="c4a7b2d3e6f0"`. New migration chains `down_revision="c4a7b2d3e6f0"`.
- Models register in `app/models/__init__.py` (must add the new model there for metadata/alembic).
- API endpoints: `app/api/v1/*.py`, aggregated in `app/api/v1/__init__.py`; non-health routers get `Depends(get_current_user)`. Auth is disabled in tests (`conftest.py` sets `AUTH_TOKEN=""`).
- Worker: `app/services/worker.py` `Worker.run()` loops; it already throttles `_sweep_stuck_jobs()` via `self._last_sweep_at`. The worker id is `f"{hostname}:{pid}"` (`_worker_id` at `:54-57`, assigned to `self.id` at `:73`). There is **no** workers-table registration yet — that's the gap.
- Base classes: `app/models/base.py` → `Base`, `UUIDPK`, `Timestamps`. The `workers` PK is a **string** (`pc_id`), so the model does NOT use `UUIDPK`.

---

## File Structure
- `app/models/worker.py` — new `WorkerNode` model (table `workers`). Named `WorkerNode` to avoid colliding with `app/services/worker.py:Worker`.
- `app/models/__init__.py` — register `WorkerNode`.
- `alembic/versions/0022_workers_registry.py` — create the `workers` table.
- `app/repositories/workers.py` — `is_online` (pure helper), `upsert_heartbeat`, `list_with_liveness`.
- `app/services/worker.py` — register on startup + heartbeat the `workers` row on a throttle in `run()`.
- `app/config.py` — `worker_registry_stale_seconds` setting.
- `app/api/v1/workers.py` + `app/api/v1/__init__.py` — `GET /api/v1/workers`.
- `tests/services/test_workers_liveness.py` — DB-free unit test of `is_online`.
- `tests/integration/test_workers_registry.py` — guarded real-DB test of upsert/heartbeat/liveness + the endpoint.

---

### Task 1: `WorkerNode` model + migration + registry

**Files:**
- Create: `app/models/worker.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/0022_workers_registry.py`

- [ ] **Step 1: Write the model**

```python
# app/models/worker.py
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkerNode(Base):
    """One row per worker process in the fleet. Keyed by the worker's
    `hostname:pid` id (same value used for `homework_jobs.claimed_by`). The
    worker upserts `last_heartbeat`; liveness ('online') is derived from how
    fresh that timestamp is, not stored."""

    __tablename__ = "workers"

    pc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="online"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Register the model** in `app/models/__init__.py`

Replace the file's contents with (adds `WorkerNode`):

```python
from app.models.agent_usage import AgentUsage
from app.models.base import Base
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.models.toc_entry import TOCEntry
from app.models.worker import WorkerNode

__all__ = ["Base", "Book", "TOCEntry", "HomeworkJob", "PhaseOutput", "AgentUsage", "WorkerNode"]
```

- [ ] **Step 3: Write the migration**

```python
# alembic/versions/0022_workers_registry.py
"""Add workers registry table (fleet liveness)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c4a7b2d3e6f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("pc_id", sa.String(length=128), primary_key=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="online"),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("workers")
```

- [ ] **Step 4: Verify the migration applies against a throwaway DB**

```bash
docker run -d --name fleet-pg-p1 -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
  -e POSTGRES_DB=edu_homework -p 5436:5432 postgres:16-alpine
```
Wait ~4s, then (Bash, inline env, cwd = worktree):
`DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework "$PY" -m alembic upgrade head`
Expected: ends at `0022` (revision `d5e9f1a2b3c4`), no error. Keep this container for Task 2/3 (or recreate). Teardown later: `docker rm -f fleet-pg-p1`.

- [ ] **Step 5: Commit**

```bash
git add app/models/worker.py app/models/__init__.py alembic/versions/0022_workers_registry.py
git commit -m "feat(fleet): workers registry table + WorkerNode model (Phase 1)"
```

---

### Task 2: workers repo — `is_online` (unit-tested) + upsert/liveness

**Files:**
- Create: `app/repositories/workers.py`
- Create: `tests/services/test_workers_liveness.py`

- [ ] **Step 1: Write the failing unit test** (DB-free — pure logic)

```python
# tests/services/test_workers_liveness.py
from datetime import datetime, timedelta, timezone

from app.repositories.workers import is_online


def _t(seconds_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


def test_fresh_heartbeat_is_online():
    assert is_online(_t(10), stale_after_seconds=90) is True


def test_stale_heartbeat_is_offline():
    assert is_online(_t(120), stale_after_seconds=90) is False


def test_boundary_is_inclusive_online():
    # exactly at the threshold counts as online (>= cutoff)
    assert is_online(_t(89), stale_after_seconds=90) is True


def test_none_heartbeat_is_offline():
    assert is_online(None, stale_after_seconds=90) is False
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `"$PY" -m pytest tests/services/test_workers_liveness.py -v` (cwd = worktree)
Expected: FAIL (`ModuleNotFoundError`/`ImportError: cannot import name 'is_online'`).

- [ ] **Step 3: Write the repo**

```python
# app/repositories/workers.py
"""Fleet worker registry: register/heartbeat a worker row + derive liveness.

`is_online` is a pure helper (DB-free, unit-tested). `upsert_heartbeat` is the
register-or-beat (Postgres upsert). `list_with_liveness` is the head-side view.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker import WorkerNode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_online(last_heartbeat: Optional[datetime], *, stale_after_seconds: int) -> bool:
    """True if the heartbeat is fresh enough. None (never beat) -> offline."""
    if last_heartbeat is None:
        return False
    return last_heartbeat >= _utcnow() - timedelta(seconds=stale_after_seconds)


async def upsert_heartbeat(session: AsyncSession, pc_id: str, *, status: str = "online") -> None:
    """Register the worker (first call) or refresh its heartbeat (every call)."""
    now = _utcnow()
    stmt = pg_insert(WorkerNode).values(pc_id=pc_id, last_heartbeat=now, status=status)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_={"last_heartbeat": now, "status": status},
    )
    await session.execute(stmt)


async def list_with_liveness(session: AsyncSession, *, stale_after_seconds: int) -> list[dict]:
    """Every worker row + a derived `online` flag, ordered by pc_id."""
    rows = (await session.execute(select(WorkerNode).order_by(WorkerNode.pc_id))).scalars().all()
    return [
        {
            "pc_id": w.pc_id,
            "last_heartbeat": w.last_heartbeat,
            "status": w.status,
            "notes": w.notes,
            "online": is_online(w.last_heartbeat, stale_after_seconds=stale_after_seconds),
        }
        for w in rows
    ]
```

- [ ] **Step 4: Run the unit test to confirm it passes**

Run: `"$PY" -m pytest tests/services/test_workers_liveness.py -v`
Expected: `4 passed`. Then run the full DB-free suite to confirm nothing broke: `"$PY" -m pytest tests/ -q` → expect the same baseline (no new failures from this change).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/workers.py tests/services/test_workers_liveness.py
git commit -m "feat(fleet): workers repo (is_online unit-tested + upsert/liveness) (Phase 1)"
```

---

### Task 3: worker registers + heartbeats the registry row

**Files:**
- Modify: `app/config.py` (add `worker_registry_stale_seconds`)
- Modify: `app/services/worker.py` (register on startup + throttled heartbeat in `run()`)

- [ ] **Step 1: Add the staleness setting** in `app/config.py`

Add this line in the `# ─── Resilience` block, right AFTER the existing `reclaim_stale_seconds: int = 120` line:

```python
    # Fleet registry: a worker upserts its `workers.last_heartbeat` every
    # `heartbeat_seconds` (30s); the head treats it offline if older than this.
    # 90s = 3 missed beats — tolerant of a slow loop without lying about death.
    worker_registry_stale_seconds: int = 90
```

- [ ] **Step 2: Import the workers repo** in `app/services/worker.py`

Change the repo import block (currently `from app.repositories import jobs as jobs_repo`) to also import the workers repo:

```python
from app.repositories import jobs as jobs_repo
from app.repositories import workers as workers_repo
```

- [ ] **Step 3: Add the registry heartbeat as a DEDICATED background task**

> ⚠ **Why a dedicated task, NOT the main loop (verified against `worker.py:96-130`):** the loop blocks at `await self._wait_for_slot_or_stop()` (`:107`) whenever all concurrency slots are busy. Fleet lessons run for MINUTES, so a fully-busy worker sits parked there far longer than `worker_registry_stale_seconds` (90s) → it would be falsely marked **offline** exactly when it's hardest at work. The per-job `_heartbeat` avoids this by running on its own `asyncio.create_task` (`:203`); the registry beat must too. (Putting the beat at the loop top would starve it — the original bug in this plan.)

Add these two methods to the `Worker` class (next to `_sweep_stuck_jobs`):

```python
    async def _registry_heartbeat(self) -> None:
        """Register this worker / refresh its heartbeat in the fleet `workers`
        table so the head-side liveness view knows this PC is alive.
        Best-effort: a failed beat is logged, never fatal."""
        try:
            async with SessionLocal() as session:
                await workers_repo.upsert_heartbeat(session, self.id)
                await session.commit()
        except Exception:
            logger.warning(f"worker {self.id} registry heartbeat failed")

    async def _registry_heartbeat_loop(self) -> None:
        """Beat on its OWN task — NOT the main loop — so a busy worker (all
        slots full with long jobs, main loop blocked in _wait_for_slot_or_stop)
        still reports alive. Mirrors the per-job _heartbeat; shutdown-aware via
        stop_event so it exits promptly."""
        await self._registry_heartbeat()  # register immediately on startup
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=settings.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                await self._registry_heartbeat()
```

In `run()`, spawn the loop as a task right AFTER the startup `await self._sweep_stuck_jobs()` call (line ~94, BEFORE the `try:`):

```python
        # Registry heartbeat on its OWN task so a busy worker (all slots full)
        # still reports alive — the main loop blocks while slots are occupied.
        registry_hb = asyncio.create_task(self._registry_heartbeat_loop())
```

Then cancel it in the existing `finally:` block (`:129-131`) — change it from:

```python
        finally:
            await self._drain()
            logger.info(f"worker {self.id} stopped")
```

to:

```python
        finally:
            registry_hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await registry_hb   # let the cancellation settle (matches _execute_job:257-259)
            await self._drain()
            logger.info(f"worker {self.id} stopped")
```

(`contextlib` is already imported in `worker.py:32`. Do NOT add `self._last_registry_hb_at`, and do NOT touch the loop body — the beat is fully decoupled from the main loop.)

- [ ] **Step 4: Write the failing integration test** (guarded real-DB)

```python
# tests/integration/test_workers_registry.py
"""Real-DB proof: a worker registers + heartbeats, and the liveness view +
endpoint report it online. Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL.

Run (throwaway pg on :5436):
  docker run -d --name fleet-pg-p1 -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
    -e POSTGRES_DB=edu_homework -p 5436:5432 postgres:16-alpine
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework \
    .venv/Scripts/python.exe -m alembic upgrade head
  RUN_DB_INTEGRATION=1 DATABASE_URL=...:5436/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_workers_registry.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_register_then_liveness_reports_online():
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "test-host:99999"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        # second beat = update path (no duplicate row)
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == pc]
        assert len(mine) == 1, f"expected exactly one row for {pc}, got {len(mine)}"
        assert mine[0]["online"] is True
        assert mine[0]["status"] == "online"
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()


@pytest.mark.asyncio
async def test_busy_worker_still_heartbeats(monkeypatch):
    """Regression (the loop-top-beat bug): a worker whose slots are ALL busy —
    main loop blocked in _wait_for_slot_or_stop — must STILL report online,
    because the registry beat runs on its own task. Hold the only slot, run the
    worker, confirm it's online."""
    import asyncio

    from app.config import settings as cfg
    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from app.services.worker import Worker

    monkeypatch.setattr(cfg, "heartbeat_seconds", 0.2)  # fast beat for the test
    w = Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)
    await w._slots.acquire()  # occupy the only slot -> main loop blocks at :107
    run_task = asyncio.create_task(w.run())
    try:
        await asyncio.sleep(0.8)  # ~4 beat intervals while the main loop is blocked
        async with SessionLocal() as s:
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == w.id]
        assert mine and mine[0]["online"] is True, "busy worker was not reported online"
    finally:
        w.stop()
        await asyncio.wait_for(run_task, timeout=5)
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == w.id))
            await s.commit()


@pytest.mark.asyncio
async def test_workers_endpoint_returns_liveness():
    import httpx

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo
    from main import app

    pc = "test-host:88888"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.get("/api/v1/workers")
        assert resp.status_code == 200
        body = resp.json()
        assert any(w["pc_id"] == pc and w["online"] is True for w in body["workers"])
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()
```

(Note: `test_workers_endpoint_returns_liveness` depends on Task 4's endpoint — it will fail until Task 4 lands. Run the first test now; run the endpoint test after Task 4.)

- [ ] **Step 5: Run the two Task-3 integration tests** (against the :5436 pg from Task 1; the endpoint test is Task 4)

Run (cwd = worktree, env inline): `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework "$PY" -m pytest tests/integration/test_workers_registry.py -k "register or busy" -v`
Expected: `2 passed` (`test_register_then_liveness_reports_online` + `test_busy_worker_still_heartbeats` — the latter proves a fully-busy worker still reports online). Also re-run the DB-free suite (`"$PY" -m pytest tests/ -q`) to confirm the worker.py edits didn't break import/collection.

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/services/worker.py tests/integration/test_workers_registry.py
git commit -m "feat(fleet): worker registers + heartbeats the workers registry (Phase 1)"
```

---

### Task 4: `GET /api/v1/workers` endpoint

**Files:**
- Create: `app/api/v1/workers.py`
- Modify: `app/api/v1/__init__.py`

- [ ] **Step 1: Write the endpoint**

```python
# app/api/v1/workers.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import workers as workers_repo

router = APIRouter()


@router.get("/workers")
async def list_workers(session: AsyncSession = Depends(get_session)) -> dict:
    """Head-side fleet liveness view: every worker + a derived `online` flag."""
    rows = await workers_repo.list_with_liveness(
        session, stale_after_seconds=settings.worker_registry_stale_seconds
    )
    online = sum(1 for r in rows if r["online"])
    return {
        "workers": rows,
        "total": len(rows),
        "online": online,
        "stale_after_seconds": settings.worker_registry_stale_seconds,
    }
```

- [ ] **Step 2: Wire it into the router** — modify `app/api/v1/__init__.py`:

Change the import line `from app.api.v1 import books, health, jobs, notion` to:
```python
from app.api.v1 import books, health, jobs, notion, workers
```
And add, after the `notion` include line:
```python
api_v1_router.include_router(workers.router, dependencies=[Depends(get_current_user)])
```

- [ ] **Step 3: Run the endpoint integration test** (now it can pass)

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework "$PY" -m pytest tests/integration/test_workers_registry.py -v`
Expected: `2 passed`. Also re-run the DB-free suite (`"$PY" -m pytest tests/ -q`) → no new failures.

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/workers.py app/api/v1/__init__.py
git commit -m "feat(fleet): GET /api/v1/workers liveness endpoint (Phase 1)"
```

---

### Task 5: Worklog

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md` (append `## [0048] …`)
- Modify: `docs/memory/INDEX.md` (append a `| 0048 | … |` row, matching the existing 4-column format)

- [ ] **Step 1: Append a `[0048]` worklog** to `docs/memory/MASTER_MEMORY.md` summarizing: workers registry table + WorkerNode model + migration 0022; `is_online` (unit) + upsert/liveness repo; worker registers + heartbeats on a throttle; `GET /api/v1/workers`; reclaim already shipped (0031) so not rebuilt. Dated 2026-06-08, branch `feat/autonomous-fleet-engine`. Reference the task commit hashes.

- [ ] **Step 2: Append the matching INDEX row.**

- [ ] **Step 3: Commit**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs(memory): worklog 0048 — fleet Phase 1 (workers registry + liveness)"
```

---

## Acceptance (controller, after all tasks)
Container-level, building on Phase 0's setup: start a throwaway pg + 2 worker containers (`class-homework-builder:fleet`), then `GET /api/v1/workers` (via a one-shot API container or the host) → **2 workers, both `online: true`**. Kill one container, wait > `worker_registry_stale_seconds` (90s), query again → that worker `online: false` (still listed). This proves the head sees fleet liveness in real time.

The **busy-worker-stays-online** case (the starvation bug) is covered by `test_busy_worker_still_heartbeats` — a worker holding all slots still reports online — so the container acceptance does NOT need to simulate multi-minute jobs.

---

## Self-Review
**1. Spec coverage (Phase 1 = "workers registry table + per-worker heartbeat + head-side liveness view"):** table+model+migration → Task 1; per-worker heartbeat → Task 3; liveness view (repo + endpoint) → Tasks 2+4. Reclaim explicitly out of scope (0031). ✓
**2. Placeholders:** none — full code in every code step. The `[0048]` worklog body is summarized (depends on commit hashes only known at execution), with explicit contents listed.
**3. Type/identifier consistency:** `WorkerNode` (model, table `workers`), `workers_repo.{is_online, upsert_heartbeat, list_with_liveness}`, `settings.worker_registry_stale_seconds`, endpoint returns `{workers, total, online, stale_after_seconds}` and the test reads `body["workers"]` + `r["online"]`/`r["pc_id"]` — all consistent. Migration `revision="d5e9f1a2b3c4"`, `down_revision="c4a7b2d3e6f0"` (chains off 0021). `WorkerNode` registered in `app/models/__init__.py` so alembic/metadata see it.
**4. Honesty:** Tasks 1/3 verification needs Docker + a throwaway pg (real-DB, like Phase 0). The `is_online` unit test (Task 2) is the DB-free piece. The cross-task note (endpoint test needs Task 4) is called out.
