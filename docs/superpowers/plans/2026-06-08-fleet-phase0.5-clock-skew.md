# Fleet Phase 0.5 — Clock-Skew Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every worker-timing comparison reason on the single head-DB clock (`func.now()`) instead of each worker host's `datetime.now()`, so a multi-PC fleet with clock skew claims, leases, retries, and reports liveness correctly.

**Architecture:** The queue/lease/liveness code currently mixes a DB-set `scheduled_at` (`server_default NOW()`) with host-computed `datetime.now()` filters and cutoffs. On one box this is invisible; across ~10 PCs each host's clock drift skews claiming. The fix moves all timing comparisons server-side: filters use `scheduled_at <= func.now()`, stamps use `func.now()`, interval cutoffs use `func.now() - func.make_interval(0, 0, 0, 0, 0, 0, N)`, and worker liveness stamps with `func.now()` + evaluates against a DB-fetched `now`. Record-only `completed_at` writes (no comparison depends on them) are deliberately left as `datetime.now()` to keep the diff minimal. No schema change → no migration.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x (async, asyncpg), Postgres 16, pytest / pytest-asyncio. Integration tests guarded by `RUN_DB_INTEGRATION=1` + a throwaway Postgres.

---

## Implementation Decisions (locked before tasks)

1. **Dynamic intervals → positional `func.make_interval(0, 0, 0, 0, 0, 0, N)`** (N = seconds, the 7th positional arg), NOT the spec's `text("interval '…'")` and NOT the kwargs form `make_interval(secs=N)`. The cutoffs (`stale_after_seconds`) and backoff (`delay`) are dynamic Python ints; positional `make_interval` binds N as a real query parameter — renders `make_interval(0, 0, 0, 0, 0, 0, :p)` — which `text("interval '…'")` cannot do without string-interpolating an int into SQL. **Verified empirically:** the kwargs form `func.make_interval(secs=N)` raises `TypeError: Function.__init__() got an unexpected keyword argument 'secs'` at construction time (SQLAlchemy's `func.X(key=value)` does NOT emit PG's `name => value` named-arg syntax); the positional form compiles and runs on real Postgres. This supersedes the spec §6 caveat (which assumed a constant interval). The real-DB test (Task 1) proves the rendered SQL runs on Postgres.
2. **`is_online` stays a pure, unit-tested helper with an injected reference clock.** Signature gains a required keyword `now: datetime`; `list_with_liveness` fetches `db_now = SELECT now()` and passes it. Heartbeats are DB-stamped (`func.now()`), so both sides of the freshness comparison are on the head-DB clock, while the helper remains DB-free and unit-testable (the 4 existing unit cases are updated to inject `now`). Rejected alternative: a raw SQL `online` column expression — it would delete the pure helper and its focused unit tests for no correctness gain.
3. **Scope = the 7 verified timing-comparison spots only.** `completed_at` writes at `jobs.py:320` (terminal fail), `:371` (cancel_if_pending), `:405` (mark_cancelled), `:427` (reclaim_stale_cancelling) are record-only and stay `datetime.now(timezone.utc)` — EXCEPT `:427`'s sibling `claimed_at < cutoff` filter (spot 6), which IS converted. Leaving record-only writes alone keeps the diff focused on the fragility.

## File Structure

- Modify: `app/repositories/jobs.py` — convert 6 timing comparisons to `func.now()` / `make_interval` (claim filter+stamps, `touch_claim`, `reclaim_stuck_jobs`, `mark_failed_with_retry` retry-branch backoff, `queue_depth`, `reclaim_stale_cancelling`). Drop now-unused `timedelta` import.
- Modify: `app/repositories/workers.py` — `upsert_heartbeat` stamps `func.now()`; `is_online` takes injected `now`; `list_with_liveness` fetches + injects `db_now`. Drop dead `_utcnow` + `timezone` import.
- Modify: `tests/services/test_workers_liveness.py` — 4 unit cases inject `now=`.
- Create: `tests/integration/test_clock_skew.py` — real-DB proof that every converted query runs on Postgres and the canonical skew symptom is gone.
- Modify: `tests/integration/test_claim_contention.py` — remove the `0377b05` past-pinning crutch (the production fix now makes the un-pinned test deterministic; the reverted test becomes the regression guard).

---

### Task 1: Convert `jobs.py` timing comparisons to the DB clock

**Files:**
- Modify: `app/repositories/jobs.py:1` (import), `:204-249` (`claim_next_job`), `:252-261` (`touch_claim`), `:264-292` (`reclaim_stuck_jobs`), `:329-343` (`mark_failed_with_retry` retry branch), `:346-355` (`queue_depth`), `:415-429` (`reclaim_stale_cancelling`)
- Test: `tests/integration/test_clock_skew.py` (new)

- [ ] **Step 1: Write the failing real-DB test**

Create `tests/integration/test_clock_skew.py`:

```python
"""Real-DB proof of Phase 0.5: every queue/lease comparison reasons on the
DB clock (func.now()), so a host-clock skew can't break claiming, and every
converted interval query actually compiles + runs on Postgres.

Skipped unless RUN_DB_INTEGRATION=1 + DATABASE_URL points at a throwaway PG.

Run:
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5436/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_clock_skew.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_section(s):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename="clock-skew.pdf",
        content_sha256="1" * 64,
        file_size_bytes=1,
        status="ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


@pytest.mark.asyncio
async def test_just_scheduled_job_is_immediately_claimable():
    """The canonical skew symptom: a job whose scheduled_at == DB now() must be
    claimable RIGHT AWAY. Under the old host-clock filter (`scheduled_at <= host
    now()`), a DB-set scheduled_at microseconds ahead of a drifting host clock
    made the job briefly 'not due' (the T1 flake). With `scheduled_at <= func.now()`
    the comparison is wholly on the DB clock and is deterministic. NO past-pinning."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        # scheduled_at defaults to server NOW() (we do NOT pin it to the past)
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        book_id = book.id
    try:
        async with SessionLocal() as s:
            claimed = await jobs_repo.claim_next_job(s, worker_id="W", max_attempts=3)
            assert claimed is not None, "a just-scheduled job was not immediately claimable"
            await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            from app.models.toc_entry import TOCEntry
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            from app.models.book import Book
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


@pytest.mark.asyncio
async def test_converted_interval_queries_run_on_postgres():
    """make_interval-based cutoffs + queue_depth filter must compile + execute
    on real Postgres (the unit suite is DB-free and can't catch a bad render)."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        # Each returns an int and does not raise -> the func.now()/make_interval SQL is valid.
        assert isinstance(await jobs_repo.reclaim_stuck_jobs(s, stale_after_seconds=120), int)
        assert isinstance(await jobs_repo.reclaim_stale_cancelling(s, 120), int)
        assert isinstance(await jobs_repo.queue_depth(s), int)
        await s.commit()


@pytest.mark.asyncio
async def test_retry_backoff_schedules_in_the_future_server_side():
    """mark_failed_with_retry must push scheduled_at into the future using the DB
    clock (func.now() + make_interval), leaving the job not-yet-claimable."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        book, toc = await _seed_section(s)
        job = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        job.attempts = 1  # one attempt already spent -> retry branch, not terminal
        await s.commit()
        job_id, book_id = job.id, book.id
    try:
        async with SessionLocal() as s:
            status = await jobs_repo.mark_failed_with_retry(
                s, job_id, error_message="boom", max_attempts=3, backoff_seconds=30
            )
            await s.commit()
            assert status == "pending"
        async with SessionLocal() as s:
            # Backoff is in the future on the DB clock -> not claimable right now.
            assert await jobs_repo.claim_next_job(s, worker_id="W", max_attempts=3) is None
            await s.commit()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            from app.models.toc_entry import TOCEntry
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            from app.models.book import Book
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 2: Run the test to verify it fails (or errors) on current code**

Start a throwaway Postgres (spare port 5436), migrate, run:

```bash
docker run -d --name fleet-pg05 -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
  -e POSTGRES_DB=edu_homework -p 5436:5432 postgres:16-alpine
# (give it ~3s to accept connections)
```

```powershell
$env:DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5436/edu_homework"
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m alembic upgrade head
$env:RUN_DB_INTEGRATION="1"
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/integration/test_clock_skew.py -v
```

Expected: `test_just_scheduled_job_is_immediately_claimable` is FLAKY/FAILS on current host-clock code (the symptom); the other two PASS on current code (they assert behavior, not skew). This establishes the regression guard. Record the result.

- [ ] **Step 3: Convert `claim_next_job` (spot 1)**

In `app/repositories/jobs.py`, `claim_next_job` (currently `:204-249`): delete the `now = datetime.now(timezone.utc)` line and replace both the filter and the stamps with `func.now()`:

```python
    pick_stmt = (
        select(HomeworkJob.id)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= func.now())
        .where(HomeworkJob.attempts < max_attempts)
        .order_by(HomeworkJob.priority.desc(), HomeworkJob.scheduled_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job_id = (await session.execute(pick_stmt)).scalar_one_or_none()
    if job_id is None:
        return None

    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="running",
            claimed_at=func.now(),
            claimed_by=worker_id,
            attempts=HomeworkJob.attempts + 1,
            last_attempt_at=func.now(),
            started_at=func.now(),
            error_message=None,  # clear stale message from prior attempt
        )
    )
    return await session.get(HomeworkJob, job_id)
```

(`func` is already imported at `jobs.py:5`. The trailing `session.get` re-SELECTs the row, so the returned ORM object carries the DB-computed timestamps — callers read `.id`, which is unaffected.)

- [ ] **Step 4: Convert `touch_claim` (spot 2)**

In `touch_claim` (currently `:252-261`), change the stamp:

```python
        .values(claimed_at=func.now())
```

- [ ] **Step 5: Convert `reclaim_stuck_jobs` (spot 3)**

In `reclaim_stuck_jobs` (currently `:264-292`), delete the `cutoff = datetime.now(...) - timedelta(...)` line and inline a DB-side interval:

```python
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "running")
        .where(
            (HomeworkJob.claimed_at.is_(None))
            | (HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        )
        .values(
            status="pending",
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
        )
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
```

- [ ] **Step 6: Convert `mark_failed_with_retry` retry backoff (spot 4) — leave terminal `completed_at` alone**

In `mark_failed_with_retry`, the retry branch (currently `:329-343`) only: replace `scheduled_at`. Leave the terminal branch's `completed_at=datetime.now(timezone.utc)` (`:320`) UNCHANGED (record-only per Decision 3).

```python
    # Retry: bump scheduled_at by exponential backoff (30s, 60s, 120s, ...).
    delay = backoff_seconds * (2 ** (job.attempts - 1))
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="pending",
            scheduled_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay),
            last_error=error_message,
            current_phase=None,
            claimed_at=None,
            claimed_by=None,
        )
    )
    return "pending"
```

- [ ] **Step 7: Convert `queue_depth` (spot 5)**

In `queue_depth` (currently `:346-355`):

```python
        .where(HomeworkJob.scheduled_at <= func.now())
```

- [ ] **Step 8: Convert `reclaim_stale_cancelling` cutoff (spot 6) — leave its `completed_at` alone**

In `reclaim_stale_cancelling` (currently `:415-429`), delete the `cutoff = ...` line and convert the filter only; leave `completed_at=datetime.now(timezone.utc)` (record-only):

```python
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.status == "cancelling")
        .where(HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        .values(status="cancelled", completed_at=datetime.now(timezone.utc))
    )
    return result.rowcount
```

- [ ] **Step 9: Drop the now-unused `timedelta` import**

`timedelta` is no longer referenced anywhere in `jobs.py` (both reclaim cutoffs and the retry backoff dropped it). `datetime` and `timezone` remain (record-only `completed_at`/`set_status` paths). Change `jobs.py:1`:

```python
from datetime import datetime, timezone
```

Verify with a search that `timedelta` has zero remaining references in the file before committing.

- [ ] **Step 10: Run the real-DB test — verify it passes**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/integration/test_clock_skew.py -v
```

Expected: all 3 PASS (run `test_just_scheduled_job_is_immediately_claimable` ~10× to confirm it's now deterministic, not flaky).

- [ ] **Step 11: Run the DB-free unit suite — verify no regression**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/ -q
```

Expected: same baseline as before this task — `5 failed (pre-existing Notion, no .env) / 329 passed / N skipped`. No NEW failures.

- [ ] **Step 12: Commit**

```bash
git add app/repositories/jobs.py tests/integration/test_clock_skew.py
git commit -m "feat(fleet): jobs queue/lease timing on DB clock (func.now) — Phase 0.5 spots 1-6"
```

---

### Task 2: Convert worker liveness (`workers.py`) to the DB clock (spot 7)

**Files:**
- Modify: `app/repositories/workers.py:8` (imports), `:18-19` (`_utcnow`), `:22-26` (`is_online`), `:29-37` (`upsert_heartbeat`), `:40-52` (`list_with_liveness`)
- Test: `tests/services/test_workers_liveness.py` (4 unit cases gain `now=`), `tests/integration/test_clock_skew.py` (add 1 case)

- [ ] **Step 1: Update the unit tests to inject `now` (write the failing test)**

Replace `tests/services/test_workers_liveness.py` with the injected-clock form:

```python
from datetime import datetime, timedelta, timezone

from app.repositories.workers import is_online


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _t(seconds_ago: float) -> datetime:
    return _now() - timedelta(seconds=seconds_ago)


def test_fresh_heartbeat_is_online():
    assert is_online(_t(10), now=_now(), stale_after_seconds=90) is True


def test_stale_heartbeat_is_offline():
    assert is_online(_t(120), now=_now(), stale_after_seconds=90) is False


def test_boundary_is_inclusive_online():
    ref = _now()
    assert is_online(ref - timedelta(seconds=89), now=ref, stale_after_seconds=90) is True


def test_none_heartbeat_is_offline():
    assert is_online(None, now=_now(), stale_after_seconds=90) is False
```

- [ ] **Step 2: Run the unit test to verify it fails**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/services/test_workers_liveness.py -v
```

Expected: FAIL — `is_online() got an unexpected keyword argument 'now'`.

- [ ] **Step 3: Convert `is_online` to take an injected clock**

In `app/repositories/workers.py`, replace `is_online` (`:22-26`):

```python
def is_online(
    last_heartbeat: Optional[datetime],
    *,
    now: datetime,
    stale_after_seconds: int,
) -> bool:
    """True if the heartbeat is fresh enough, measured against `now`. None
    (never beat) -> offline. `now` is injected (the DB clock on the head-side
    path) so liveness never mixes a DB-stamped heartbeat with a host clock."""
    if last_heartbeat is None:
        return False
    return last_heartbeat >= now - timedelta(seconds=stale_after_seconds)
```

- [ ] **Step 4: DB-stamp `upsert_heartbeat` + fetch DB-now in `list_with_liveness`**

Replace `upsert_heartbeat` (`:29-37`) and `list_with_liveness` (`:40-52`):

```python
async def upsert_heartbeat(session: AsyncSession, pc_id: str, *, status: str = "online") -> None:
    """Register the worker (first call) or refresh its heartbeat (every call).
    Stamps `last_heartbeat` with the DB clock (func.now()) so every worker's
    beat is on the single head-DB clock regardless of its host clock."""
    stmt = pg_insert(WorkerNode).values(pc_id=pc_id, last_heartbeat=func.now(), status=status)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pc_id"],
        set_={"last_heartbeat": func.now(), "status": status},
    )
    await session.execute(stmt)


async def list_with_liveness(session: AsyncSession, *, stale_after_seconds: int) -> list[dict]:
    """Every worker row + a derived `online` flag, ordered by pc_id. Liveness is
    evaluated against the DB clock (db_now) so it matches the DB-stamped beats."""
    db_now = await session.scalar(select(func.now()))
    rows = (await session.execute(select(WorkerNode).order_by(WorkerNode.pc_id))).scalars().all()
    return [
        {
            "pc_id": w.pc_id,
            "last_heartbeat": w.last_heartbeat,
            "status": w.status,
            "notes": w.notes,
            "online": is_online(
                w.last_heartbeat, now=db_now, stale_after_seconds=stale_after_seconds
            ),
        }
        for w in rows
    ]
```

- [ ] **Step 5: Fix imports — add `func`, drop dead `_utcnow` + `timezone`**

`_utcnow` is now unused (both callers converted). `timezone` was only used by `_utcnow`. `datetime` (type hint) and `timedelta` (`is_online`) remain. Update the import block (`:8`, `:11`) and delete `_utcnow` (`:18-19`):

```python
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker import WorkerNode
```

(Delete the `_utcnow()` function entirely.) Verify `_utcnow` and `timezone` have zero remaining references in the file before committing.

- [ ] **Step 6: Run the unit test — verify it passes**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/services/test_workers_liveness.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Add a real-DB liveness proof to `test_clock_skew.py`**

Append to `tests/integration/test_clock_skew.py`:

```python
@pytest.mark.asyncio
async def test_heartbeat_is_db_stamped_and_reports_online():
    """upsert_heartbeat stamps the DB clock; list_with_liveness evaluates against
    the DB clock -> a just-beaten worker is online, and its stored last_heartbeat
    matches the DB now() (not the host clock) within a small window."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.worker import WorkerNode
    from app.repositories import workers as workers_repo

    pc = "skew-host:5555"
    try:
        async with SessionLocal() as s:
            await workers_repo.upsert_heartbeat(s, pc)
            await s.commit()
        async with SessionLocal() as s:
            db_now = await s.scalar(select(func.now()))
            row = await s.scalar(select(WorkerNode).where(WorkerNode.pc_id == pc))
            # DB-stamped: within 5s of the DB's own now(), independent of host clock.
            assert abs((db_now - row.last_heartbeat).total_seconds()) < 5
            rows = await workers_repo.list_with_liveness(s, stale_after_seconds=90)
        mine = [r for r in rows if r["pc_id"] == pc]
        assert mine and mine[0]["online"] is True
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(WorkerNode).where(WorkerNode.pc_id == pc))
            await s.commit()
```

- [ ] **Step 8: Run the full real-DB integration set + the registry set (no regression)**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/integration/test_clock_skew.py tests/integration/test_workers_registry.py -v
```

Expected: `test_clock_skew.py` 4 PASS; `test_workers_registry.py` 3 PASS (the existing registry test still passes — it never called `is_online` directly, it goes through `list_with_liveness`, which now injects `db_now` internally).

- [ ] **Step 9: Run the DB-free unit suite (no regression)**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/ -q
```

Expected: baseline unchanged (`5 failed pre-existing / 329 passed / N skipped`).

- [ ] **Step 10: Commit**

```bash
git add app/repositories/workers.py tests/services/test_workers_liveness.py tests/integration/test_clock_skew.py
git commit -m "feat(fleet): worker liveness on DB clock (func.now stamp + injected now) — Phase 0.5 spot 7"
```

---

### Task 3: Remove the T1 past-pinning crutch + acceptance gate

**Files:**
- Modify: `tests/integration/test_claim_contention.py:18` (import), `:53-61` (drop past-pinning)

- [ ] **Step 1: Remove the past-pinning workaround**

The `0377b05` crutch pinned `scheduled_at` to the past to dodge the host-vs-DB skew. Phase 0.5 fixes that at the source, so the un-pinned test is now deterministic AND serves as a regression guard. In `tests/integration/test_claim_contention.py`, delete the past-pinning block (`:53-61` — the comment + `past = ...` + the two `j*.scheduled_at = past` lines) so the two jobs keep their server-default `scheduled_at = NOW()`:

```python
        j1 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        j2 = await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        book_id = book.id
```

Then simplify the import at `:18` (only `timezone`/`timedelta`/`datetime` were used for the crutch; confirm none remain used elsewhere in the file and reduce the import accordingly — if nothing else in the file uses them, delete the `from datetime import ...` line entirely).

- [ ] **Step 2: Run T1 repeatedly — verify deterministic pass**

```powershell
1..10 | ForEach-Object {
  & "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/integration/test_claim_contention.py -q
}
```

Expected: 10/10 green (deterministic — the skew that used to flake it is fixed at the source).

- [ ] **Step 3: Full real-DB integration sweep**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/integration/ -v
```

Expected: all integration tests PASS (clock-skew, contention, workers-registry).

- [ ] **Step 4: Full DB-free suite (final)**

```powershell
& "C:/Users/Recruiter/Desktop/Homework-Content-Generation-Automation/.venv/Scripts/python.exe" -m pytest tests/ -q
```

Expected: baseline `5 failed pre-existing / 329 passed / N skipped`. No new red.

- [ ] **Step 5: Container acceptance smoke (real fleet, two workers, one DB)**

Rebuild the fleet image and re-run the Phase 0 two-worker container smoke against the throwaway DB to prove the converted timing code works end-to-end in a real worker process (not just in-process tests). Use the existing Phase 0 acceptance pattern (isolated docker network + throwaway pg, migrate one-shot, API with `WORKER_CONCURRENCY=0` + 2 worker containers `python -m app.services.worker`; assert both pull jobs, `attempts>0`, 0 orphans). Record the result in the worklog.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_claim_contention.py
git commit -m "test(fleet): drop T1 past-pinning crutch — DB-clock fix makes it deterministic (Phase 0.5)"
```

- [ ] **Step 7: Worklog + index (worklog 0049)**

Add a worklog entry to `docs/memory/MASTER_MEMORY.md` (Phase 0.5 — clock-skew hardening: 7 spots → DB clock, make_interval rationale, injected-now liveness, real-DB gate + container smoke result) and a row in `docs/memory/INDEX.md`. Commit:

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs(memory): worklog 0049 — fleet Phase 0.5 (clock-skew hardening, DB-clock timing)"
```

---

## Self-Review

**Spec coverage:** All 7 verified spots from spec §6 Phase 0.5 are covered — Task 1 spots 1–6 (`jobs.py`), Task 2 spot 7 (`workers.py`). Record-only `completed_at` writes explicitly left (Decision 3). Real-DB test gate (`test_clock_skew.py`) + container smoke (Task 3 Step 5) satisfy the spec's "MUST add a real-DB test + run the full suite + a smoke" gate.

**Placeholder scan:** No TBD/TODO. Every code step shows the exact replacement. The container smoke (Task 3 Step 5) references the existing Phase 0 acceptance pattern rather than repeating it — acceptable because it's an already-built, documented procedure (worklog 0047), not new code.

**Type consistency:** `is_online` gains `now: datetime` (keyword-only) in Task 2 Step 3; every caller updated in the same task — unit tests (Step 1) and `list_with_liveness` (Step 4). `func.make_interval(0, 0, 0, 0, 0, 0, N)` (positional, N = seconds) used identically in all three interval spots. `func.now()` returns `timestamptz` → asyncpg tz-aware datetime, comparable with the tz-aware `last_heartbeat` and `scheduled_at` columns.

**Caveat carried from spec §6:** after `.values(claimed_at=func.now())`, the returned ORM object's stamp is DB-computed; `claim_next_job` re-SELECTs via `session.get`, callers read `.id` — safe.
