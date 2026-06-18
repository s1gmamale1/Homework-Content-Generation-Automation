# Cluster 2 — Cancel / Resume Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Commit prefix **`c2:`**; worklog ID **0078** (reserved); PR title prefix **`[cluster-2]`**. Branch `cluster-2-cancel-resume` (worktree `../HCGA-c2-cancel-resume`), cut off `origin/Nggaev-v2`.

**Goal:** Make batch cancel reliable, make resume a first-class batch action, and stop the silent data-loss/re-bill when relaunching a batch over failed/cancelled lessons that have saved work.

**Architecture:** A status-write guard (`cancel-race-1`) is the keystone — once a job is `cancelling`/terminal, no per-phase `running` write may clobber it. On top of the reliable cancel: a batch **cancel-all** and batch **resume** endpoint reuse the existing per-job primitives (`cancel_if_pending`/`request_cancel`/`reset_for_retry`), and `launch_batch` becomes **resume-aware** so a relaunch reuses saved phases instead of discarding them.

**Tech Stack:** FastAPI, SQLAlchemy async (guarded `UPDATE`s), Alembic (no schema change here), React + TypeScript (no FE test runner → `tsc` + `npm run build` is the proof).

---

## Approach & key decisions

- **Keystone first — guard `set_status` (`cancel-race-1`).** `jobs.set_status` (`jobs.py:134`) and `phase_repo.set_status` (`phase_outputs.py:98`) currently assign status unconditionally; the pipeline rewrites `running` every phase (`pipeline.py:141,676`) and the worker only samples `cancelling` every `heartbeat_seconds` (`worker.py:316`), so a `running→cancelling` flip gets clobbered back to `running`. Fix = a guarded `UPDATE`: **terminal `{done,failed,cancelled}` is frozen; a `cancelling` job may only advance to `cancelled`.** The rule is also a pure predicate (`status_write_allowed`) so the body is unit-testable without a DB; the atomic guarantee comes from the `WHERE`. Verified the guard does NOT break legitimate transitions (claim's `pending→running` and retry's `cancelled→pending` go through their OWN updates — `claim_next_job` / `reset_for_retry` — not `set_status`).
- **Reuse the per-job primitives for batch ops.** Cancel-all = `pending→cancelled` (`cancel_if_pending` semantics) + `running→cancelling` (`request_cancel`) over the batch + local `RUNNING_JOBS` task-cancel (instant kill, mirrors the per-job cancel endpoint). Resume = loop `reset_for_retry` over `failed`+`cancelled` jobs (resume already reuses `done` phase rows — `pipeline.py:148-152`, `_done_phase_md`).
- **Relaunch steers to resume (supersedes the brief).** The brief framed `fleet-relaunch-dataloss-1` as a "2-step confirm then discard" *interim* because resume was assumed to land later. Resume ships in THIS cluster, so the better fix is: `launch_batch` resumes a `failed`/`cancelled` section (via `reset_for_retry`, reusing its saved `done` phases) instead of `jobs_repo.create` (which discarded them + re-billed). **Decisions locked with the user (2026-06-18):** (1) **Relaunch guard = steer-to-resume.** "Launch remaining" resumes saved + launches new + recreates empty in ONE click; the FE intercepts with a dialog **only when ≥1 remaining section has saved work** (`"X of N have saved work"`), primary **Resume saved + launch new**, secondary **Discard & regenerate** behind a second confirm that states the count re-bills. (2) **"Re-run all" → kebab (⋯) overflow**, away from Launch, confirm states it re-bills all N. — This changes `fleet-relaunch-dataloss-1`'s deliverable from "confirm-before-discard" to "steer-to-resume, discard is the confirmed escape hatch" — **call this out in the worklog/WISHLIST closeout** (deliberate improvement, not the stale brief line).
- **Test strategy (per brief's real-body rule):** new repo functions with SQL semantics (the guard, `cancel_all_in_batch`, `resume_batch`, the relaunch disposition) get **real-DB integration tests** in `tests/integration/` (`RUN_DB_INTEGRATION=1`, the existing `_seed_book`/`_cleanup` pattern) — these run the real bodies. New endpoints also get mocked-repo unit tests (existing `tests/api/` culture). Pure predicates get plain unit tests.
- **Acceptance (generation-affecting → real run):** launch a small real batch, cancel-all mid-flight (assert CLIs die + `done` phases preserved + jobs `cancelled`), then resume (assert only unfinished phases re-run). Closes `cancel-1`(a) + `fleet-test-4`.
- **Order (sequential, deps):** guard → phase-guard → cancel-all → resume → resume-aware relaunch → FE client → FE card. **Merge before Cluster 4** (its kill-switch builds on this guard).

---

### Task 1: Guard `jobs.set_status` against clobbering a cancelling/terminal status

**Files:**
- Modify: `app/repositories/jobs.py` (`set_status` ~134-156; add `status_write_allowed` + `_TERMINAL_STATUSES`)
- Test: `tests/test_status_guard.py` (create — pure predicate, no DB)
- Test: `tests/integration/test_set_status_guard.py` (create — real DB)

- [ ] **Step 1: Write the failing pure-predicate test**

Create `tests/test_status_guard.py`:

```python
import pytest

from app.repositories.jobs import status_write_allowed


@pytest.mark.parametrize("current,target,allowed", [
    ("pending", "running", True),       # claim/start
    ("running", "running", True),       # per-phase re-write
    ("running", "done", True),          # success
    ("running", "failed", True),        # crash
    ("running", "cancelling", True),    # request_cancel path
    ("cancelling", "cancelled", True),  # finalize
    ("cancelling", "running", False),   # THE RACE — must be blocked
    ("cancelling", "pending", False),   # no resurrection
    ("cancelling", "done", False),      # cancel wins over a late success
    ("cancelling", "failed", False),    # cancel wins over a late failure
    ("done", "running", False),         # terminal frozen
    ("failed", "running", False),
    ("cancelled", "running", False),
    ("cancelled", "pending", False),
])
def test_status_write_allowed(current, target, allowed):
    assert status_write_allowed(current, target) is allowed
```

- [ ] **Step 2: Run it — expect ImportError**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/test_status_guard.py -q`
Expected: FAIL — `cannot import name 'status_write_allowed'`.

- [ ] **Step 3: Implement the predicate + guarded UPDATE**

In `app/repositories/jobs.py`, add near the top (after imports) the predicate:

```python
_TERMINAL_STATUSES = ("done", "failed", "cancelled")


def status_write_allowed(current: str, target: str) -> bool:
    """Guard for job status writes (cancel-race-1). A terminal status is frozen;
    a `cancelling` job may only advance to `cancelled` (never resurrected to
    running/pending, nor flipped to done/failed). Every other transition is
    allowed. Mirror this rule in the set_status guarded UPDATE WHERE clause."""
    if current in _TERMINAL_STATUSES:
        return False
    if current == "cancelling" and target != "cancelled":
        return False
    return True
```

Replace the body of `set_status` with a guarded `UPDATE` (keep the same signature; add `guard: bool = True`; return `bool`):

```python
async def set_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    current_phase: Optional[str] = None,
    guard: bool = True,
) -> bool:
    """Set a job's status. With ``guard`` (default), a guarded UPDATE refuses to
    overwrite a terminal status or resurrect a `cancelling` job (cancel-race-1):
    terminal {done,failed,cancelled} is frozen; from `cancelling` only
    `cancelled` is allowed. Mirrors ``status_write_allowed``. Returns True iff a
    row was updated. claim (pending->running) and reset_for_retry
    (cancelled->pending) use their OWN updates and are unaffected."""
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if error_message is not None:
        values["error_message"] = error_message
    if current_phase is not None:
        values["current_phase"] = current_phase
    stmt = update(HomeworkJob).where(HomeworkJob.id == job_id)
    if guard:
        stmt = stmt.where(HomeworkJob.status.not_in(_TERMINAL_STATUSES))
        if status != "cancelled":
            stmt = stmt.where(HomeworkJob.status != "cancelling")
    result = await session.execute(stmt.values(**values))
    return result.rowcount > 0
```

Confirm `update` and `Optional` are already imported in `jobs.py` (they are — `cancel_if_pending` uses `update`). If `UUID`/`datetime` typing imports are missing for the signature, they already exist (the old signature used them).

- [ ] **Step 3b: ORM-staleness caller sweep (required — gatekeeper change #2)**

`set_status` changed from ORM-mutation (`session.get` + assign) to a raw guarded `UPDATE`, so the in-session ORM object is **no longer mutated**. Grep every caller and confirm none reads `job.status` / `job.current_phase` / `job.completed_at` / `job.started_at` off a loaded ORM object **in the same session after** calling `set_status`:

Run: `grep -n "set_status" app/services/pipeline.py app/services/worker.py app/api/v1/*.py`

For each call site, read ±15 lines: if the code later reads those fields off the same loaded `job` instance in the same session, fix it — either use the returned bool, re-fetch via `jobs_repo.get`/`get_status`, or `session.expire(job)`. The known callers (`pipeline.py:141,269,296,387,676`) set-and-move-on (next read is a fresh session), but **verify, don't assume** — quote each call site's following lines in the commit message. This is a classic ORM→UPDATE regression; the controller re-checks it on diff review.

- [ ] **Step 4: Run the predicate test — expect PASS**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/test_status_guard.py -q`
Expected: PASS (14 cases).

- [ ] **Step 5: Write the real-DB integration test**

Create `tests/integration/test_set_status_guard.py`:

```python
"""Real-DB: set_status guard refuses to clobber a cancelling/terminal job
(cancel-race-1). RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_job(status: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="d" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(toc); await s.flush()
        job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                          subject="math-algebra", provider="claude", status=status)
        s.add(job); await s.commit()
        return book.id, job.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_running_write_cannot_clobber_cancelling():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("cancelling")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "running",
                                                 current_phase="flashcards")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "cancelling"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_cancelling_can_finalize_to_cancelled():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("cancelling")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "cancelled")
            await s.commit()
        assert changed is True
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "cancelled"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_terminal_done_is_frozen():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("done")
    try:
        async with SessionLocal() as s:
            changed = await jobs_repo.set_status(s, job_id, "running")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "done"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_normal_pending_to_running_still_works():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, job_id = await _seed_job("running")
    try:
        async with SessionLocal() as s:
            assert await jobs_repo.set_status(s, job_id, "done") is True
            await s.commit()
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, job_id) == "done"
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 6: Run the integration test (real PG)**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_set_status_guard.py -q`
Expected: PASS (4 cases). If `HomeworkJob` requires more non-null columns at insert, add them in `_seed_job` to match the model.

- [ ] **Step 7: Full suite stays green (the guard touches a hot path — run all)**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/ -q`
Expected: no NEW failures vs base (the 5 notion-503 env tests fail only without `NOTION_API_KEY` — ignore those; everything else green).

- [ ] **Step 8: Commit**

```bash
git add app/repositories/jobs.py tests/test_status_guard.py tests/integration/test_set_status_guard.py
git commit -m "c2: guard jobs.set_status against clobbering cancelling/terminal (cancel-race-1)"
```

---

### Task 2: Guard `phase_repo.set_status` so a `done` phase is frozen (protects resume)

**Files:**
- Modify: `app/repositories/phase_outputs.py` (`set_status` ~98-131)
- Test: `tests/integration/test_phase_status_guard.py` (create)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_phase_status_guard.py`:

```python
"""Real-DB: phase_repo.set_status freezes a `done` phase so a late cancel-race
write can't corrupt the resumable set. RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_phase(status: str, output_md: str):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="e" * 64, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(toc); await s.flush()
        job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                          subject="math-algebra", provider="claude", status="running")
        s.add(job); await s.flush()
        po = PhaseOutput(job_id=job.id, phase_name="flashcards", phase_order=1,
                         status=status, output_md=output_md)
        s.add(po); await s.commit()
        return book.id, po.id


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    async with SessionLocal() as s:
        await s.execute(delete(PhaseOutput).where(
            PhaseOutput.job_id.in_(
                __import__("sqlalchemy").select(HomeworkJob.id).where(
                    HomeworkJob.book_id == book_id))))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_done_phase_is_frozen():
    from app.db import SessionLocal
    from app.repositories import phase_outputs as phase_repo
    book_id, po_id = await _seed_phase("done", "REAL OUTPUT")
    try:
        async with SessionLocal() as s:
            changed = await phase_repo.set_status(s, po_id, "running")
            await s.commit()
        assert changed is False
        async with SessionLocal() as s:
            rows = await phase_repo.list_for_job(
                s, (await s.get(__import__("app.models.phase_output",
                    fromlist=["PhaseOutput"]).PhaseOutput, po_id)).job_id)
        assert [r for r in rows if r.id == po_id][0].status == "done"
        assert [r for r in rows if r.id == po_id][0].output_md == "REAL OUTPUT"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_running_phase_can_advance_to_done():
    from app.db import SessionLocal
    from app.repositories import phase_outputs as phase_repo
    book_id, po_id = await _seed_phase("running", "")
    try:
        async with SessionLocal() as s:
            changed = await phase_repo.set_status(s, po_id, "done", output_md="X")
            await s.commit()
        assert changed is True
    finally:
        await _cleanup(book_id)
```

(If `PhaseOutput`'s column for ordering is named differently than `phase_order`, match the model — check `app/models/phase_output.py` first and adjust the seed.)

- [ ] **Step 2: Run it — expect FAIL** (guard not present; `set_status` returns None / clobbers)

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_phase_status_guard.py -q`
Expected: FAIL on `test_done_phase_is_frozen` (status flips to running).

- [ ] **Step 3: Implement the phase guard**

In `app/repositories/phase_outputs.py`, convert `set_status` to a guarded `UPDATE` returning `bool`, freezing `done`:

```python
async def set_status(
    session: AsyncSession,
    phase_output_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    output_md: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    error_message: Optional[str] = None,
    validation_warnings: Optional[list] = None,
    provider: Optional[str] = None,
    guard: bool = True,
) -> bool:
    """Set a phase row's status. With ``guard`` (default), a `done` phase is
    frozen — protects the resumable set (`_done_phase_md`) from a cancel-race
    clobber. Returns True iff a row was updated."""
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if output_md is not None:
        values["output_md"] = output_md
    if tokens_input is not None:
        values["tokens_input"] = tokens_input
    if tokens_output is not None:
        values["tokens_output"] = tokens_output
    if error_message is not None:
        values["error_message"] = error_message
    if validation_warnings is not None:
        values["validation_warnings"] = validation_warnings
    if provider is not None:
        values["provider"] = provider
    stmt = update(PhaseOutput).where(PhaseOutput.id == phase_output_id)
    if guard:
        stmt = stmt.where(PhaseOutput.status != "done")
    result = await session.execute(stmt.values(**values))
    return result.rowcount > 0
```

Add `from sqlalchemy import update` to `phase_outputs.py` if not already imported.

- [ ] **Step 4: Run it — expect PASS**

Run: same command as Step 2. Expected: PASS (2 cases).

- [ ] **Step 5: Full suite green**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add app/repositories/phase_outputs.py tests/integration/test_phase_status_guard.py
git commit -m "c2: freeze done phase rows in phase_repo.set_status (protects resume)"
```

---

### Task 3: Batch Cancel-all — repo + `POST /jobs/batch/{id}/cancel`

**Files:**
- Modify: `app/repositories/jobs.py` (add `cancel_all_in_batch`)
- Modify: `app/api/v1/batch.py` (add the route + local task-cancel)
- Test: `tests/integration/test_batch_cancel.py` (create — real DB, mixed-state batch)
- Test: `tests/api/test_batch_cancel_endpoint.py` (create — mocked, mirrors `test_cancel_endpoint.py`)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_batch_cancel.py` (reuse the `_seed_book`/`_cleanup` shape from `tests/integration/test_batches.py`; seed a batch with jobs in `pending`, `running`, `done`, `failed`):

```python
"""Real-DB: cancel_all_in_batch flips pending->cancelled and running->cancelling,
leaving done/failed untouched. RUN_DB_INTEGRATION=1."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


async def _seed_batch_with_statuses(statuses):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256="a1" * 32, file_size_bytes=1, status="toc_ready")
        s.add(book); await s.flush()
        batch = Batch(book_id=book.id, subject="math-algebra", grade="9",
                      provider="claude", transport="cli")
        s.add(batch); await s.flush()
        job_ids = {}
        for i, st in enumerate(statuses):
            toc = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(toc); await s.flush()
            job = HomeworkJob(book_id=book.id, toc_entry_id=toc.id,
                              subject="math-algebra", provider="claude",
                              status=st, batch_id=batch.id)
            s.add(job); await s.flush()
            job_ids[st] = job.id
        await s.commit()
        return book.id, batch.id, job_ids


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.models.batch import Batch
    from app.models.homework_job import HomeworkJob
    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


@pytest.mark.asyncio
async def test_cancel_all_in_batch_mixed_states():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, batch_id, ids = await _seed_batch_with_statuses(
        ["pending", "running", "done", "failed"])
    try:
        async with SessionLocal() as s:
            counts = await jobs_repo.cancel_all_in_batch(s, batch_id)
            await s.commit()
        assert counts == {"cancelled": 1, "cancelling": 1}
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, ids["pending"]) == "cancelled"
            assert await jobs_repo.get_status(s, ids["running"]) == "cancelling"
            assert await jobs_repo.get_status(s, ids["done"]) == "done"
            assert await jobs_repo.get_status(s, ids["failed"]) == "failed"
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run it — expect FAIL** (`cancel_all_in_batch` missing).

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_batch_cancel.py -q`

- [ ] **Step 3: Implement `cancel_all_in_batch`**

In `app/repositories/jobs.py`:

```python
async def cancel_all_in_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Cancel every non-terminal job in a batch in one transaction: pending ->
    cancelled (never claimed), running -> cancelling (the worker/heartbeat then
    kills the task). done/failed/cancelled/cancelling are left untouched.
    Returns {"cancelled": n_pending, "cancelling": n_running}."""
    pend = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.batch_id == batch_id, HomeworkJob.status == "pending")
        .values(status="cancelled", completed_at=func.now()))
    run = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.batch_id == batch_id, HomeworkJob.status == "running")
        .values(status="cancelling"))
    return {"cancelled": pend.rowcount, "cancelling": run.rowcount}


async def running_job_ids_in_batch(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Job ids that were `cancelling` after cancel_all — so the API can cancel any
    locally-running tasks instantly (rather than waiting for the heartbeat)."""
    rows = await session.execute(
        select(HomeworkJob.id).where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status == "cancelling"))
    return list(rows.scalars().all())
```

(`func` and `select` are already imported in `jobs.py`.)

- [ ] **Step 4: Add the endpoint** in `app/api/v1/batch.py` (mirror the per-job cancel's local-task kill — import the worker registry lazily like `jobs.py` does):

```python
@router.post("/jobs/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    counts = await jobs_repo.cancel_all_in_batch(session, batch_id)
    running_ids = await jobs_repo.running_job_ids_in_batch(session, batch_id)
    await session.commit()
    # Instant local kill for any task running in THIS process (others self-cancel
    # via the heartbeat within heartbeat_seconds).
    from app.services.worker import RUNNING_JOBS
    for jid in running_ids:
        task = RUNNING_JOBS.get(jid)
        if task is not None:
            task.cancel()
    return {"batch_id": str(batch_id), **counts}
```

- [ ] **Step 5: Write the mocked endpoint test** `tests/api/test_batch_cancel_endpoint.py`:

```python
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_cancel_batch_returns_counts():
    bid = uuid4()
    with patch("app.api.v1.batch.jobs_repo.cancel_all_in_batch",
               AsyncMock(return_value={"cancelled": 2, "cancelling": 1})), \
         patch("app.api.v1.batch.jobs_repo.running_job_ids_in_batch",
               AsyncMock(return_value=[])), \
         patch("app.api.v1.batch.AsyncSession.get", AsyncMock(return_value=SimpleNamespace(id=bid))):
        r = client.post(f"/api/v1/jobs/batch/{bid}/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] == 2 and r.json()["cancelling"] == 1


def test_cancel_batch_404():
    bid = uuid4()
    with patch("app.api.v1.batch.AsyncSession.get", AsyncMock(return_value=None)):
        r = client.post(f"/api/v1/jobs/batch/{bid}/cancel")
    assert r.status_code == 404
```

(If patching `AsyncSession.get` is awkward, patch `app.api.v1.batch.Batch` lookup via a `session` dependency override instead — match whatever is cleanest against the real import; the integration test is the authoritative coverage.)

- [ ] **Step 6: Run both test files**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/api/test_batch_cancel_endpoint.py -q && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_batch_cancel.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/repositories/jobs.py app/api/v1/batch.py tests/integration/test_batch_cancel.py tests/api/test_batch_cancel_endpoint.py
git commit -m "c2: batch cancel-all endpoint + cancel_all_in_batch (fleet-ctrl-1)"
```

---

### Task 4: Batch Resume — repo + `POST /jobs/batch/{id}/resume`

**Files:**
- Modify: `app/repositories/jobs.py` (add `resume_failed_in_batch`)
- Modify: `app/api/v1/batch.py` (add the route)
- Test: `tests/integration/test_batch_resume.py` (create)
- Test: `tests/api/test_batch_resume_endpoint.py` (create)

- [ ] **Step 1: Failing integration test** `tests/integration/test_batch_resume.py` (reuse `_seed_batch_with_statuses`/`_cleanup` — copy them into this file or a shared helper; seed `failed`, `cancelled`, `done`):

```python
"""Real-DB: resume_failed_in_batch resets only failed+cancelled jobs to pending
(attempts zeroed), leaves done. RUN_DB_INTEGRATION=1."""
from __future__ import annotations
import os
import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1", reason="needs Postgres")

# ... copy _seed_batch_with_statuses + _cleanup from test_batch_cancel.py ...


@pytest.mark.asyncio
async def test_resume_failed_in_batch():
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    book_id, batch_id, ids = await _seed_batch_with_statuses(
        ["failed", "cancelled", "done"])
    try:
        async with SessionLocal() as s:
            n = await jobs_repo.resume_failed_in_batch(s, batch_id)
            await s.commit()
        assert n == 2
        async with SessionLocal() as s:
            assert await jobs_repo.get_status(s, ids["failed"]) == "pending"
            assert await jobs_repo.get_status(s, ids["cancelled"]) == "pending"
            assert await jobs_repo.get_status(s, ids["done"]) == "done"
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run — expect FAIL** (`resume_failed_in_batch` missing).

- [ ] **Step 3: Implement** in `app/repositories/jobs.py` (reuse `reset_for_retry`, which resumes done phases):

```python
async def resume_failed_in_batch(session: AsyncSession, batch_id: UUID) -> int:
    """Re-enqueue every failed/cancelled job in a batch via reset_for_retry
    (status->pending, attempts->0). reset_for_retry keeps phase rows, so the
    pipeline RESUMES — done phases are reused, only unfinished ones re-run.
    Returns the count re-enqueued."""
    rows = await session.execute(
        select(HomeworkJob.id).where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status.in_(["failed", "cancelled"])))
    ids = list(rows.scalars().all())
    for jid in ids:
        await reset_for_retry(session, jid)
    return len(ids)
```

- [ ] **Step 4: Endpoint** in `app/api/v1/batch.py`:

```python
@router.post("/jobs/batch/{batch_id}/resume")
async def resume_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    resumed = await jobs_repo.resume_failed_in_batch(session, batch_id)
    await session.commit()
    return {"batch_id": str(batch_id), "jobs_resumed": resumed}
```

- [ ] **Step 5: Mocked endpoint test** `tests/api/test_batch_resume_endpoint.py` (mirror Task 3 Step 5: patch `resume_failed_in_batch` → assert `jobs_resumed`; 404 path).

- [ ] **Step 6: Run both**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/api/test_batch_resume_endpoint.py -q && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_batch_resume.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/repositories/jobs.py app/api/v1/batch.py tests/integration/test_batch_resume.py tests/api/test_batch_resume_endpoint.py
git commit -m "c2: batch resume endpoint + resume_failed_in_batch (fleet-ctrl-2)"
```

---

### Task 5: Resume-aware relaunch — stop discarding saved phases (`fleet-relaunch-dataloss-1`)

**Files:**
- Modify: `app/repositories/jobs.py` (add `latest_for_section` + `done_phase_count_for_job`)
- Modify: `app/api/v1/batch.py` (`launch_batch`: `preview` + `relaunch_mode`; resume failed/cancelled instead of create)
- Test: `tests/integration/test_relaunch_resume.py` (create)

- [ ] **Step 1: Failing integration test** `tests/integration/test_relaunch_resume.py` — seed a batch where one section's latest job is `cancelled` WITH a `done` phase row; launch again (no force); assert the section is **resumed** (same job id, status `pending`) and its phase row is **preserved**, not a new job:

```python
"""Real-DB: a no-force relaunch over a cancelled-with-saved-phases section
RESUMES it (reuses the job row + done phase) instead of creating a fresh job
that discards the saved output. RUN_DB_INTEGRATION=1 (fleet-relaunch-dataloss-1)."""
from __future__ import annotations
import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1", reason="needs Postgres")
_HDR = {"Authorization": "Bearer 123"}

# ... _seed: book(toc_ready) + 1 toc + a cancelled job with a done phase row,
#     all under a batch on transport=cli; + _cleanup (delete phases, jobs, batch,
#     toc, book) ...


@pytest.mark.asyncio
async def test_relaunch_resumes_saved_section():
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    book_id, batch_id, toc_id, old_job_id = await _seed_cancelled_with_phase()
    try:
        async with AsyncClient(transport=ASGITransport(app=__import__(
                "main", fromlist=["app"]).app), base_url="http://t") as ac:
            r = await ac.post("/api/v1/jobs/batch", headers=_HDR, json={
                "book_id": str(book_id), "transport": "cli"})
        assert r.status_code == 201
        body = r.json()
        assert body["jobs_resumed"] == 1
        assert body["jobs_created"] == 0
        async with SessionLocal() as s:
            jobs = (await s.execute(select(HomeworkJob).where(
                HomeworkJob.toc_entry_id == toc_id))).scalars().all()
            assert len(jobs) == 1            # no NEW job — reused the row
            assert jobs[0].id == old_job_id
            assert jobs[0].status == "pending"
    finally:
        await _cleanup(book_id)


@pytest.mark.asyncio
async def test_relaunch_preview_reports_saved_count_without_mutating():
    book_id, batch_id, toc_id, old_job_id = await _seed_cancelled_with_phase()
    try:
        async with AsyncClient(transport=ASGITransport(app=__import__(
                "main", fromlist=["app"]).app), base_url="http://t") as ac:
            r = await ac.post("/api/v1/jobs/batch", headers=_HDR, json={
                "book_id": str(book_id), "transport": "cli", "preview": True})
        assert r.status_code == 200
        assert r.json()["resumable"] == 1
        from app.db import SessionLocal
        from app.repositories import jobs as jobs_repo
        async with SessionLocal() as s:   # unchanged
            assert await jobs_repo.get_status(s, old_job_id) == "cancelled"
    finally:
        await _cleanup(book_id)
```

- [ ] **Step 2: Run — expect FAIL** (relaunch currently creates a fresh job; no `preview`/`jobs_resumed`).

- [ ] **Step 3: Implement repo helpers** in `app/repositories/jobs.py`:

```python
async def latest_for_section(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID, *,
    transport: Optional[str] = None,
) -> Optional[HomeworkJob]:
    """The most recent job for a (book, section) regardless of status — used by
    relaunch to find a failed/cancelled job to RESUME rather than recreate."""
    conds = [HomeworkJob.book_id == book_id,
             HomeworkJob.toc_entry_id == toc_entry_id]
    if transport is not None:
        conds.append(HomeworkJob.transport == transport)
    stmt = (select(HomeworkJob).where(*conds)
            .order_by(HomeworkJob.created_at.desc()).limit(1))
    return (await session.execute(stmt)).scalar_one_or_none()


async def done_phase_count_for_job(session: AsyncSession, job_id: UUID) -> int:
    """How many `done` phase rows with non-empty output a job has — the 'saved
    work' a relaunch would discard if it recreated the job."""
    from app.models.phase_output import PhaseOutput
    stmt = select(func.count()).select_from(PhaseOutput).where(
        PhaseOutput.job_id == job_id,
        PhaseOutput.status == "done",
        func.coalesce(PhaseOutput.output_md, "") != "")
    return int((await session.execute(stmt)).scalar_one())
```

- [ ] **Step 4: Rework `launch_batch`** in `app/api/v1/batch.py`. Add to `BatchLaunchRequest`:

```python
    preview: bool = False                 # compute disposition, don't mutate
    relaunch_mode: str = "resume"         # "resume" | "discard" for failed/cancelled-with-saved
```

**(a) STRICT zero-write preview** — compute the disposition and return **before** any batch create/mutation. Insert this immediately after `targets` is resolved and the provider/transport validations pass, and **before** `batch = await batches_repo.get_or_create_for_book(...)`:

```python
    if body.preview:
        new = resumable = empty = 0
        for t in targets:
            active = await jobs_repo.find_active_for_section(
                session, body.book_id, t.id, transport=body.transport)
            if active is not None:
                continue  # pending/running/done — not "remaining"
            latest = await jobs_repo.latest_for_section(
                session, body.book_id, t.id, transport=body.transport)
            if latest is not None and latest.status in ("failed", "cancelled"):
                if await jobs_repo.done_phase_count_for_job(session, latest.id) > 0:
                    resumable += 1
                else:
                    empty += 1
            else:
                new += 1
        return {"book_id": str(body.book_id), "preview": True,
                "new": new, "resumable": resumable, "empty": empty}
```

This returns before `get_or_create_for_book`, so a preview the operator never confirms leaves **no phantom batch row** in the rollups (the gatekeeper's strict-zero-write ruling — do NOT create the batch then document around it).

**(b) Resume-aware mutating path** — in the real (non-preview) per-target loop, after the existing `find_active_for_section` adopt/skip branch, replace the final `create` with resume-vs-create:

```python
        # No active (pending/running/done) job → "remaining". Resume a saved
        # failed/cancelled section instead of discarding it; else create fresh.
        latest = await jobs_repo.latest_for_section(
            session, body.book_id, t.id, transport=body.transport)
        if (latest is not None and latest.status in ("failed", "cancelled")
                and body.relaunch_mode != "discard"):
            await jobs_repo.reset_for_retry(session, latest.id)   # reuses done phases
            resumed += 1
            continue
        # brand-new section, OR discard mode → fresh job (discard leaves the old
        # failed/cancelled row as history; find_active ignores it)
        await jobs_repo.create(session, book_id=body.book_id, toc_entry_id=t.id,
                               subject=book.subject, provider=provider,
                               model=body.model, batch_id=batch.id,
                               transport=body.transport,
                               extract_transport=body.extract_transport,
                               judge_transport=body.judge_transport,
                               extract_provider=body.extract_provider,
                               extract_model=body.extract_model,
                               judge_provider=body.judge_provider,
                               judge_model=body.judge_model)
        created += 1
```

Initialize `resumed = 0` next to `created = adopted = skipped = 0`. After the loop (NO preview branch — preview already returned early), add `jobs_resumed=resumed` to the payload:

```python
    await session.flush()
    tally = await batches_repo.rollup_for_batch(session, batch.id)
    await session.commit()
    payload = _rollup_payload(batch, tally, book.original_filename)
    payload.update(jobs_created=created, jobs_adopted=adopted,
                   jobs_skipped=skipped, jobs_resumed=resumed)
    return payload
```

- [ ] **Step 5: Run the integration test — expect PASS**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_relaunch_resume.py -q`

- [ ] **Step 6: Existing batch tests still green** (the launch path changed):

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && uv run python -m pytest tests/api -q && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration/test_batches.py -q`
Expected: PASS (existing no-active-job-create cases still create for brand-new sections).

- [ ] **Step 7: Commit**

```bash
git add app/repositories/jobs.py app/api/v1/batch.py tests/integration/test_relaunch_resume.py
git commit -m "c2: resume-aware relaunch — reuse saved phases instead of discard (fleet-relaunch-dataloss-1)"
```

---

### Task 6: Frontend API client + types

**Files:**
- Modify: `web/src/lib/api.ts` (`cancelBatch`, `resumeBatch`; extend `launchBatch` body with `preview`/`relaunch_mode`; add a `previewBatch` helper)
- Modify: `web/src/lib/types.ts` (batch action response shapes)

- [ ] **Step 1: Add the client functions** in `web/src/lib/api.ts` (mirror `cancelJob`/`retryJob`):

```typescript
  async cancelBatch(batchId: string): Promise<{ batch_id: string; cancelled: number; cancelling: number }> {
    const res = await authFetch(`/api/v1/jobs/batch/${encodeURIComponent(batchId)}/cancel`, { method: "POST" });
    return unwrap(res);
  },
  async resumeBatch(batchId: string): Promise<{ batch_id: string; jobs_resumed: number }> {
    const res = await authFetch(`/api/v1/jobs/batch/${encodeURIComponent(batchId)}/resume`, { method: "POST" });
    return unwrap(res);
  },
```

Extend the `launchBatch` body type with `preview?: boolean; relaunch_mode?: "resume" | "discard";` and add a thin `previewBatch` that calls `launchBatch({ ...body, preview: true })` returning `{ preview: true; new: number; resumable: number; empty: number }` (add that union to the `launchBatch` return type, or a dedicated `previewBatch` signature).

- [ ] **Step 2: Typecheck**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume/web && npx tsc -p tsconfig.app.json --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/types.ts
git commit -m "c2: FE api client — cancelBatch/resumeBatch + launch preview/relaunch_mode"
```

---

### Task 7: Frontend batch-card actions — cancel-all, resume, resume-aware Launch dialog, Re-run-all → kebab

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` (ReadyCard action row: add Cancel-all + Resume buttons; rework Launch-remaining click to preview→dialog; move Re-run-all into a kebab)
- (Reference) `web/src/lib/ui.ts` for existing button/danger/overflow styles — reuse, don't invent.

- [ ] **Step 1: Add mutations** in `ReadyCard` (mirror the existing `launch` mutation, lines ~656-686):

```tsx
  const cancelAll = useMutation({
    mutationFn: () => api.cancelBatch(batchId),
    onSuccess: (r) => { toast.success(`Cancelling ${r.cancelling}, cancelled ${r.cancelled}`);
      qc.invalidateQueries({ queryKey: ["batches"] }); qc.invalidateQueries({ queryKey: ["books"] }); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Cancel failed"),
  });
  const resumeAll = useMutation({
    mutationFn: () => api.resumeBatch(batchId),
    onSuccess: (r) => { toast.success(`Resuming ${r.jobs_resumed} lessons`);
      qc.invalidateQueries({ queryKey: ["batches"] }); qc.invalidateQueries({ queryKey: ["books"] }); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Resume failed"),
  });
```

`batchId` for a ReadyCard: derive from the book's batch for the chosen transport (the card already resolves a batch to show rollups — reuse that id; if not in scope, look it up from the `batches` query by `book_id`+`transport`). Cancel-all/Resume render only when that batch exists and has the relevant states (non-terminal → Cancel-all; failed/cancelled → Resume).

- [ ] **Step 2: Rework the Launch-remaining click** to preview→dialog (replace the current `onClick={() => launch.mutate({})}`):

```tsx
    onClick={async () => {
      const p = await api.launchBatch({ /* same body builder as launch.mutate, */ preview: true });
      if ("resumable" in p && p.resumable > 0) {
        // dialog: "X of N lessons have saved work."
        const discard = window.confirm(
          `${p.resumable} of these lessons have saved work.\n\n` +
          `OK = Resume them (reuse saved phases, only unfinished re-run).\n` +
          `Cancel = stop (use the kebab "Discard & regenerate" to re-bill instead).`);
        if (discard) launch.mutate({ relaunch_mode: "resume" });
      } else {
        launch.mutate({});  // nothing saved at stake → straight launch
      }
    }}
```

(If the kit has a real dialog component in `lib/ui.ts`/`components/ui`, use it instead of `window.confirm` for the two-action choice — primary **Resume saved + launch new**, secondary **Discard & regenerate** which calls `launch.mutate({ relaunch_mode: "discard" })` behind a second confirm that states the re-bill count. Keep `window.confirm` only if no dialog primitive exists.)

Extend the `launch` mutation's `mutationFn` opts to thread `relaunch_mode` into the `api.launchBatch` body.

- [ ] **Step 3: Move "Re-run all" into a kebab (⋯) overflow** — remove the inline button (current lines ~825-846) from the primary row; render it inside an overflow menu (reuse an existing menu/danger affordance from `lib/ui.ts` if present, else a minimal single-item dropdown). Keep its `window.confirm`, and make the copy state the re-bill: `Regenerate ALL ${lessons} lessons, including completed ones? This discards finished outputs and re-bills all ${lessons}.`

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume/web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: PASS (tsc clean; vite build writes `web/dist/`).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/launcher.tsx
git commit -m "c2: FE — batch cancel-all/resume, resume-aware Launch dialog, Re-run-all to kebab"
```

---

## Acceptance (generation-affecting → REAL run required)

A real end-to-end live run on the local fleet (Mac head + a worker; transport `cli` so it's $0). Document the evidence in the worklog.

- [ ] **Integration suite (real PG):** `cd /Users/macmini5/Documents/HCGA-c2-cancel-resume && RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_copy uv run python -m pytest tests/integration -q` — all green.
- [ ] **Full unit suite:** `uv run python -m pytest tests/ -q` — no new failures (notion-503 env tests excepted).
- [ ] **FE:** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` — clean.
- [ ] **Live cancel→resume:** launch a small real batch (3–4 lessons) on a `toc_ready` book; mid-flight `POST /jobs/batch/{id}/cancel`; assert (a) running CLIs die, (b) `pending` jobs → `cancelled`, running → `cancelling` → `cancelled`, (c) any `done` phase rows are preserved (query `phase_outputs`). Then `POST /jobs/batch/{id}/resume`; assert only the unfinished phases re-run (the `done` ones are skipped — check the resume log line `resume: N done phase(s) skipped`). This closes `cancel-1`(a) + `fleet-test-4`.

## Finish (do not defer)

- [ ] Rebase-check: `git fetch origin` + `git log HEAD..origin/Nggaev-v2`; if behind, rebase onto `origin/Nggaev-v2`, resolve, re-run the suite.
- [ ] Worklog **[0078]** in `docs/memory/MASTER_MEMORY.md` + INDEX row (expect a trivial append-conflict with sibling clusters — keep both blocks).
- [ ] WISHLIST closes: `cancel-race-1`, `fleet-relaunch-dataloss-1` (**note the deliverable changed to steer-to-resume**), `fleet-ctrl-1`, `fleet-ctrl-2`, `cancel-1`(a), `fleet-test-4`.
- [ ] `git mv docs/superpowers/plans/2026-06-18-cluster-2-cancel-resume.md docs/superpowers/plans/shipped/`.
- [ ] De-stale reference docs touched: `docs/CODE_MAP.md` (new batch cancel/resume endpoints + the set_status guard), `docs/HOW_IT_WORKS.md` if it describes the cancel/resume lifecycle. No schema change → DATABASE/DEPLOY untouched.
- [ ] Open PR `[cluster-2] cancel/resume correctness` → `Nggaev-v2`; **do NOT self-merge**. Flag the gatekeeper: **must merge before Cluster 4**.
