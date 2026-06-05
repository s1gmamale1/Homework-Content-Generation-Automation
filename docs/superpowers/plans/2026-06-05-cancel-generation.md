# Cancel Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator cancel a homework job — kill every running provider-CLI subprocess (and its children), unwind the pipeline, and mark the job `cancelled` — for both queued and running jobs.

**Architecture:** Two core pieces carry the feature: (C1) make cancellation cascade through the parallel phase scheduler so each `_spawn` actually kills its CLI; (C2) a `psutil` whole-process-tree kill so no CLI children orphan. The rest is plumbing: a `cancelled`/`cancelling` status (no migration), an atomic cancel endpoint, an in-process running-job registry, a heartbeat that notices a cross-process cancel, a shielded finalize, a stale-`cancelling` sweep, `/retry`-allows-cancelled, and a frontend Cancel button.

**Tech Stack:** FastAPI, asyncio, psutil (new), pytest (DB-free — pure-function + `inspect`/signature tests, per `tests/conftest.py`). Windows dev host; Linux prod (k8s). React/Vite/TS frontend (verify with `tsc --noEmit` + `npm run build`).

**Spec:** `docs/superpowers/specs/2026-06-05-cancel-generation-design.md`

**Commands:** Tests via the venv python (uv not on PATH): `.\.venv\Scripts\python.exe -m pytest <args>` (PowerShell tool). **Stage only each task's listed files** — never `git add -A` (parallel sessions share the branch). Known pre-existing red: `tests/services/test_config_notion.py::test_notion_defaults_disabled`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml` | add `psutil` dependency | T1 |
| `app/services/proc_tree.py` (create) | `kill_tree(pid)` — psutil whole-tree kill | T1 |
| `app/services/agent.py` (modify) | `_spawn` cancel handler → `kill_tree(proc.pid)` | T2 |
| `app/services/pipeline.py` (modify) | C1 scheduler cancel-cascade | T3 |
| `app/repositories/jobs.py` (modify) | `cancel_if_pending`, `request_cancel`, `get_status`, `mark_cancelled`, `reclaim_stale_cancelling` | T4 |
| `app/services/worker.py` (modify) | `RUNNING_JOBS` registry + register in `_execute_job` | T5 |
| `app/services/worker.py` (modify) | `_heartbeat` status-read + self-cancel | T6 |
| `app/services/worker.py` (modify) | shielded `cancelled` finalize + discriminator | T7 |
| `app/services/worker.py` (modify) | stale-`cancelling` sweep in the loop | T8 |
| `app/api/v1/jobs.py` (modify) | `POST /jobs/{id}/cancel` | T9 |
| `app/api/v1/jobs.py` (modify) | `/retry` guard `failed` → `failed`/`cancelled` | T10 |
| `web/src/lib/types.ts`, `api.ts`, job route (modify) | `JobStatus` + `cancelJob` + Cancel button | T11 |
| acceptance + worklog | real cancel smoke + worklog | T12 |

---

## Task 1: `psutil` dep + `kill_tree` whole-process-tree kill

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Create: `app/services/proc_tree.py`
- Test: `tests/services/test_proc_tree.py`

- [ ] **Step 1: Add the dependency.** In `pyproject.toml`, add `"psutil>=5.9"` to the `dependencies` array. Then sync: `.\.venv\Scripts\python.exe -m pip install "psutil>=5.9"` (or `uv sync` if available). Verify: `.\.venv\Scripts\python.exe -c "import psutil; print(psutil.__version__)"`.

- [ ] **Step 2: Write the failing test** `tests/services/test_proc_tree.py`:

```python
import sys
import time
import subprocess

import psutil

from app.services.proc_tree import kill_tree


def test_kill_tree_kills_parent_and_child():
    # Parent python spawns a child python that sleeps 60s, then the parent
    # sleeps 60s. We kill the parent's tree and assert BOTH pids are gone.
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code])
    # wait until the child exists
    child_pids = []
    for _ in range(50):
        try:
            kids = psutil.Process(parent.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            kids = []
        if kids:
            child_pids = [k.pid for k in kids]
            break
        time.sleep(0.1)
    assert child_pids, "child process never started"

    kill_tree(parent.pid)

    # both parent and child must be gone shortly after
    for _ in range(50):
        if not psutil.pid_exists(parent.pid) and all(not psutil.pid_exists(c) for c in child_pids):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(parent.pid), "parent survived kill_tree"
    for c in child_pids:
        assert not psutil.pid_exists(c), f"child {c} survived kill_tree"


def test_kill_tree_nonexistent_pid_is_safe():
    # A pid that doesn't exist must not raise.
    kill_tree(2_000_000_000)
```

- [ ] **Step 3: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_proc_tree.py -q` → FAIL (`ImportError: cannot import name 'kill_tree'`).

- [ ] **Step 4: Implement** `app/services/proc_tree.py`:

```python
"""Whole-process-tree kill via psutil — one code path for Windows (dev) and
Linux (prod/k8s). `proc.kill()` only kills the direct child; provider CLIs
(node for claude/gemini, python for kimi) can spawn helpers that would orphan
and keep burning tokens after a cancel. We suspend the parent first so it can't
spawn new children during the kill (closes the snapshot window), then sweep all
descendants, kill them, and reap."""

from __future__ import annotations

import psutil
from loguru import logger


def kill_tree(pid: int, *, wait_timeout: float = 3.0) -> None:
    """Kill `pid` and every descendant. Best-effort and exception-safe: a
    process that's already gone is fine. Synchronous (callers are in await-free
    cancel handlers); may block up to `wait_timeout` reaping."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    # Freeze the parent so it can't fork more children while we enumerate.
    try:
        parent.suspend()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        descendants = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    victims = [*descendants, parent]
    for p in victims:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            logger.warning(f"kill_tree: access denied killing pid={p.pid}")
    gone, alive = psutil.wait_procs(victims, timeout=wait_timeout)
    if alive:
        logger.warning(f"kill_tree: {len(alive)} process(es) survived kill: {[p.pid for p in alive]}")
```

- [ ] **Step 5: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_proc_tree.py -q` → 2 passed.

- [ ] **Step 6: Commit.**

```bash
git add pyproject.toml app/services/proc_tree.py tests/services/test_proc_tree.py
git commit -m "feat(proc): kill_tree — psutil whole-process-tree kill (Win+Linux)"
```

---

## Task 2: Wire `kill_tree` into `_spawn`'s cancel handler

**Files:**
- Modify: `app/services/agent.py` (`_spawn`, the `except asyncio.CancelledError` at ~`:346`)
- Test: `tests/services/test_spawn_cancel_kill.py`

**Context:** `_spawn` currently does `except asyncio.CancelledError: proc.kill()` (top-process only). Replace with `kill_tree(proc.pid)`. The spawn call (`create_subprocess_exec`) is unchanged.

- [ ] **Step 1: Write the failing test** (structural — the real tree-kill is proven in T1; here we assert the wiring):

```python
import inspect
from app.services import agent


def test_spawn_uses_kill_tree_on_cancel():
    src = inspect.getsource(agent._spawn)
    assert "kill_tree(proc.pid)" in src, "_spawn must kill the whole tree on cancel"
    # the bare top-process-only kill must be gone from the cancel path
    assert "proc.kill()" not in src, "replace proc.kill() with kill_tree(proc.pid)"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_spawn_cancel_kill.py -q` → FAIL.

- [ ] **Step 3: Implement.** Add the import near the other `from app.services...` imports at the top of `agent.py`:

```python
from app.services.proc_tree import kill_tree
```

Then in `_spawn`, change the cancel handler (currently):

```python
        try:
            stdout_b, stderr_b = await proc.communicate(prompt.encode("utf-8"))
        except asyncio.CancelledError:
            proc.kill()
            try:
                last_msg_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
```

to:

```python
        try:
            stdout_b, stderr_b = await proc.communicate(prompt.encode("utf-8"))
        except asyncio.CancelledError:
            kill_tree(proc.pid)   # whole tree — provider CLIs spawn helpers
            try:
                last_msg_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
```

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_spawn_cancel_kill.py -q` → PASS. Also confirm import is clean: `.\.venv\Scripts\python.exe -c "import app.services.agent"`.

- [ ] **Step 5: Commit.**

```bash
git add app/services/agent.py tests/services/test_spawn_cancel_kill.py
git commit -m "feat(agent): _spawn kills the whole CLI process tree on cancel"
```

---

## Task 3: C1 — scheduler cancellation cascade

**Files:**
- Modify: `app/services/pipeline.py` (`_run_content_phases_parallel`, the loop at ~`:395-453`)
- Test: `tests/services/test_scheduler_cancel_cascade.py`

**Context:** The scheduler launches phases as detached `asyncio.create_task` into `in_flight` and waits at `await asyncio.wait(...)`. `asyncio.wait` does NOT cancel its awaitables when the waiter is cancelled, and the existing peer-cancel (`:447-452`) is an `except Exception` — which cannot catch `CancelledError` (a `BaseException`). So we add an explicit `except asyncio.CancelledError` that cancels every `in_flight` task and gathers them (letting each `_spawn`'s `kill_tree` fire), then re-raises.

- [ ] **Step 1: Write the failing test** (structural guard — the behavioral proof is the T12 smoke; a true behavioral unit test would require constructing the scheduler's full arg set):

```python
import inspect
from app.services import pipeline


def test_scheduler_cancels_inflight_on_external_cancel():
    src = inspect.getsource(pipeline._run_content_phases_parallel)
    assert "except asyncio.CancelledError" in src, "scheduler must catch external cancellation"
    # on cancel it must cancel peers AND gather them so each _spawn's kill fires
    assert src.count(".cancel()") >= 2, "must cancel in_flight tasks on CancelledError (not only on failure)"
    assert "gather" in src, "must gather cancelled in_flight tasks so subprocess kills run"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_scheduler_cancel_cascade.py -q` → FAIL (only one `.cancel()` today, inside the failure branch).

- [ ] **Step 3: Implement.** Wrap the scheduling `while` loop body's `await asyncio.wait(...)` so external cancellation tears down `in_flight`. Locate the loop in `_run_content_phases_parallel` (the `while pending or in_flight:` loop containing `done, _ = await asyncio.wait(...)` at `:434`). Wrap the entire loop in a `try/except asyncio.CancelledError`:

```python
    try:
        while pending or in_flight:
            # ... existing loop body unchanged (launch ready_now, the
            #     `if not in_flight:` break, the `await asyncio.wait(...)`,
            #     and the done-handling incl. the existing failure peer-cancel) ...
            ...
    except asyncio.CancelledError:
        # External cancel (user pressed Cancel). asyncio.wait() does NOT cancel
        # its awaitables, so we must: cancel every in-flight phase and gather
        # them — that lets each _execute_phase -> _spawn run its
        # `except CancelledError: kill_tree(...)` before we unwind.
        for t in in_flight.values():
            t.cancel()
        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)
            in_flight.clear()
        raise
```

Keep the existing failure-path peer-cancel (`:447-452`) exactly as-is; this new `except` is a sibling that fires only on external cancellation. (Indent the existing loop one level into the `try`.)

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_scheduler_cancel_cascade.py -q` → PASS. Import clean: `.\.venv\Scripts\python.exe -c "import app.services.pipeline"`.

- [ ] **Step 5: Regression.** `.\.venv\Scripts\python.exe -m pytest tests/ -q -k "pipeline or phase or scheduler"` → no new failures.

- [ ] **Step 6: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_scheduler_cancel_cascade.py
git commit -m "feat(pipeline): cascade cancellation through the parallel scheduler (kill in-flight CLIs)"
```

---

## Task 4: Repo helpers — cancel/finalize/sweep

**Files:**
- Modify: `app/repositories/jobs.py`
- Test: `tests/repositories/test_cancel_repo.py`

**Context:** All DB-free here — assert on the SQL the functions build (using `inspect.getsource`) since `tests/conftest.py` wires no DB, mirroring the repo's other source-level guards. Behavior is proven end-to-end by the T12 smoke. `update`, `select`, `datetime`, `timezone`, `HomeworkJob` are already imported in `jobs.py`.

- [ ] **Step 1: Write the failing tests** `tests/repositories/test_cancel_repo.py`:

```python
import inspect
from app.repositories import jobs as jobs_repo


def test_cancel_if_pending_is_atomic_pending_only():
    src = inspect.getsource(jobs_repo.cancel_if_pending)
    assert 'status == "pending"' in src or "status == 'pending'" in src
    assert '"cancelled"' in src or "'cancelled'" in src
    assert "rowcount" in src  # returns whether it actually transitioned


def test_request_cancel_sets_cancelling_on_running():
    src = inspect.getsource(jobs_repo.request_cancel)
    assert '"cancelling"' in src or "'cancelling'" in src
    assert 'status == "running"' in src or "status == 'running'" in src


def test_get_status_reads_status():
    src = inspect.getsource(jobs_repo.get_status)
    assert "select" in src.lower() and "status" in src.lower()


def test_mark_cancelled_preserves_done_phases():
    src = inspect.getsource(jobs_repo.mark_cancelled)
    assert '"cancelled"' in src or "'cancelled'" in src
    # in-flight phases -> failed, but NOT the done ones
    assert "PhaseOutput" in src
    assert '!= "done"' in src or "!= 'done'" in src


def test_reclaim_stale_cancelling_targets_cancelling():
    src = inspect.getsource(jobs_repo.reclaim_stale_cancelling)
    assert '"cancelling"' in src or "'cancelling'" in src
    assert '"cancelled"' in src or "'cancelled'" in src
    assert "claimed_at" in src  # staleness by lease window
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/repositories/test_cancel_repo.py -q` → FAIL (functions undefined).

- [ ] **Step 3: Implement** — append to `app/repositories/jobs.py` (add `from datetime import timedelta` if not present; `PhaseOutput` import: `from app.models import PhaseOutput` near the top if not already imported):

```python
async def cancel_if_pending(session: AsyncSession, job_id: UUID) -> bool:
    """Atomically cancel a still-queued job. Returns True iff it transitioned
    pending->cancelled (so the worker can never have claimed it). False means
    it was already claimed/running/done — caller falls through to request_cancel."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "pending")
        .values(status="cancelled", completed_at=datetime.now(timezone.utc))
    )
    return result.rowcount > 0


async def request_cancel(session: AsyncSession, job_id: UUID) -> bool:
    """Signal cancel for a RUNNING job: running->cancelling. Returns True iff it
    transitioned (the owning worker / same-process registry then cancels the
    task and finalizes). False means it wasn't running (done/failed/etc)."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(status="cancelling")
    )
    return result.rowcount > 0


async def get_status(session: AsyncSession, job_id: UUID) -> Optional[str]:
    """Lightweight status read (used by the heartbeat to notice a cancel)."""
    return (
        await session.execute(
            select(HomeworkJob.status).where(HomeworkJob.id == job_id)
        )
    ).scalar_one_or_none()


async def mark_cancelled(session: AsyncSession, job_id: UUID) -> None:
    """Finalize a user-cancelled job: job -> cancelled; any non-done phase rows
    -> failed (they were interrupted/killed). DONE phases are preserved so a
    later /retry can resume (worklog 0031)."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(status="cancelled", completed_at=datetime.now(timezone.utc))
    )
    await session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == job_id)
        .where(PhaseOutput.status != "done")
        .values(status="failed")
    )


async def reclaim_stale_cancelling(
    session: AsyncSession, stale_after_seconds: int
) -> int:
    """Finalize jobs stuck in `cancelling` whose claim is older than the lease
    window — i.e. the owning worker crashed mid-cancel. They're excluded from
    both claim (pending) and reclaim (running) sweeps, so without this they'd
    hang forever. The intent was to cancel, so -> cancelled."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.status == "cancelling")
        .where(HomeworkJob.claimed_at < cutoff)
        .values(status="cancelled", completed_at=datetime.now(timezone.utc))
    )
    return result.rowcount
```

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/repositories/test_cancel_repo.py -q` → 5 passed. Import clean: `.\.venv\Scripts\python.exe -c "import app.repositories.jobs"`.

- [ ] **Step 5: Commit.**

```bash
git add app/repositories/jobs.py tests/repositories/test_cancel_repo.py
git commit -m "feat(jobs-repo): cancel_if_pending, request_cancel, get_status, mark_cancelled, reclaim_stale_cancelling"
```

---

## Task 5: `RUNNING_JOBS` registry in the worker

**Files:**
- Modify: `app/services/worker.py` (module-level registry + register/unregister in `_execute_job`)
- Test: `tests/services/test_running_jobs_registry.py`

**Context:** The cancel endpoint (in `app/api/v1/jobs.py`, a different module) needs to reach the in-memory task running a job. A module-level `RUNNING_JOBS: dict[UUID, asyncio.Task]` is importable from both. `_execute_job` registers itself on entry and removes itself in `finally`.

- [ ] **Step 1: Write the failing test:**

```python
import inspect
from app.services import worker


def test_running_jobs_registry_exists_and_is_populated():
    assert hasattr(worker, "RUNNING_JOBS"), "module-level RUNNING_JOBS registry required"
    assert isinstance(worker.RUNNING_JOBS, dict)
    src = inspect.getsource(worker.Worker._execute_job)
    assert "RUNNING_JOBS[job_id]" in src, "_execute_job must register its task"
    assert "current_task()" in src, "register the running task handle"
    assert "RUNNING_JOBS.pop(job_id" in src, "must unregister in finally"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_running_jobs_registry.py -q` → FAIL.

- [ ] **Step 3: Implement.** Near the top of `app/services/worker.py` (after imports), add:

```python
# Maps job_id -> the in-flight _execute_job task, so a same-process cancel
# endpoint can cancel the exact running job instantly. Process-local: in a
# separate-pod deployment the API's registry is empty and the owning worker
# self-cancels via the heartbeat (see _heartbeat).
RUNNING_JOBS: dict[UUID, asyncio.Task] = {}
```

Then in `_execute_job`, register at the very start and unregister in the existing `finally`. Change the method's opening + `finally`:

```python
    async def _execute_job(self, job_id: UUID) -> None:
        RUNNING_JOBS[job_id] = asyncio.current_task()
        hb = asyncio.create_task(self._heartbeat(job_id))
        try:
            ...  # unchanged body
        finally:
            RUNNING_JOBS.pop(job_id, None)
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb
            self._slots.release()
```

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_running_jobs_registry.py -q` → PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/worker.py tests/services/test_running_jobs_registry.py
git commit -m "feat(worker): RUNNING_JOBS registry (job_id -> task) for instant in-process cancel"
```

---

## Task 6: `_heartbeat` reads status + self-cancels on `cancelling`

**Files:**
- Modify: `app/services/worker.py` (`_heartbeat`)
- Test: `tests/services/test_heartbeat_cancel.py`

**Context:** `_heartbeat` today only calls `touch_claim` (an UPDATE WHERE status='running' — it won't even match a `cancelling` row). For the cross-process path, the owning worker must NOTICE the `cancelling` flag and cancel its local task. Add a `get_status` read each beat; if `cancelling`, cancel `RUNNING_JOBS[job_id]`.

- [ ] **Step 1: Write the failing test:**

```python
import inspect
from app.services import worker


def test_heartbeat_self_cancels_on_cancelling():
    src = inspect.getsource(worker.Worker._heartbeat)
    assert "get_status" in src, "heartbeat must read job status"
    assert '"cancelling"' in src or "'cancelling'" in src
    assert "RUNNING_JOBS" in src and ".cancel()" in src, "must self-cancel the local task"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_heartbeat_cancel.py -q` → FAIL.

- [ ] **Step 3: Implement.** Replace the `_heartbeat` body so each beat checks for a cancel before touching the claim:

```python
    async def _heartbeat(self, job_id: UUID) -> None:
        """Refresh the job's claim while its pipeline runs, AND notice a
        cross-process cancel: if the API (possibly in another pod) flipped the
        job to `cancelling`, self-cancel the local task so its CLIs die."""
        while True:
            await asyncio.sleep(settings.heartbeat_seconds)
            try:
                async with SessionLocal() as session:
                    status = await jobs_repo.get_status(session, job_id)
                    if status == "cancelling":
                        task = RUNNING_JOBS.get(job_id)
                        if task is not None:
                            task.cancel()
                        return  # nothing more to do; the task will finalize
                    await jobs_repo.touch_claim(session, job_id)
                    await session.commit()
            except Exception:
                logger.warning(f"worker {self.id} heartbeat failed for job={job_id}")
```

(Confirm the original `_heartbeat` committed after `touch_claim`; preserve that. If it didn't commit, the `await session.commit()` above is still correct.)

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_heartbeat_cancel.py -q` → PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/worker.py tests/services/test_heartbeat_cancel.py
git commit -m "feat(worker): heartbeat notices cross-process cancel and self-cancels the task"
```

---

## Task 7: Shielded `cancelled` finalize + discriminator in `_execute_job`

**Files:**
- Modify: `app/services/worker.py` (`_execute_job`'s `except asyncio.CancelledError`)
- Test: `tests/services/test_cancel_finalize.py`

**Context:** Today the handler leaves the job `running` (shutdown semantics). For a user-cancel we must finalize `cancelled`. CRITICAL: the cancellation is already delivered, so a naive `await mark_cancelled(...)` can re-raise `CancelledError` before the write lands — wrap it in `asyncio.shield(...)`. Discriminate user-cancel from shutdown by re-reading status: `cancelling` → finalize; else → shutdown (leave running).

- [ ] **Step 1: Write the failing test:**

```python
import inspect
from app.services import worker


def test_cancel_finalize_is_shielded_and_status_gated():
    src = inspect.getsource(worker.Worker._execute_job)
    # discriminator: only finalize when the job is being cancelled
    assert "get_status" in src and ("'cancelling'" in src or '"cancelling"' in src)
    assert "mark_cancelled" in src
    # the finalize write must survive the already-delivered CancelledError
    assert "shield" in src, "wrap the cancelled finalize in asyncio.shield"
    # double-cancel hardening: clear our own cancel state before the finalize
    assert "uncancel" in src, "uncancel() before finalize so a double-cancel can't skip the write"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_cancel_finalize.py -q` → FAIL.

- [ ] **Step 3: Implement.** Replace the `except asyncio.CancelledError` block in `_execute_job` (currently logs + `raise`) with a discriminating, shielded finalize:

```python
            except asyncio.CancelledError:
                # Distinguish a user-cancel from a worker-shutdown cancel.
                cancelling = False
                try:
                    async with SessionLocal() as session:
                        cancelling = (await jobs_repo.get_status(session, job_id)) == "cancelling"
                except Exception:
                    logger.warning(f"worker {self.id} job={job_id} cancel status read failed")
                if cancelling:
                    # User cancel. Clear our own cancellation so a rare
                    # double-cancel (idempotent endpoint hit twice, or shutdown
                    # racing the user-cancel) can't re-fire a CancelledError at
                    # the finalize awaits — `except Exception` can't catch it
                    # (CancelledError is BaseException). Python 3.13 uncancel().
                    # shield() is belt-and-suspenders; T8's reclaim_stale_cancelling
                    # sweep is the ultimate backstop for anything that slips past.
                    current = asyncio.current_task()
                    if current is not None:
                        current.uncancel()
                    async def _finalize() -> None:
                        async with SessionLocal() as session:
                            await jobs_repo.mark_cancelled(session, job_id)
                            await session.commit()
                    try:
                        await asyncio.shield(_finalize())
                        logger.warning(f"worker {self.id} job={job_id} CANCELLED by user")
                    except Exception:
                        logger.exception(f"worker {self.id} job={job_id} cancel finalize failed")
                    # do NOT re-raise: the job is finalized cancelled.
                else:
                    # Shutdown cancel — leave the row running for reclaim.
                    logger.warning(f"worker {self.id} job={job_id} CANCELLED during shutdown")
                    raise
```

(Note: on user-cancel we deliberately swallow the `CancelledError` after finalizing, so the task ends normally and the `finally` releases the slot + unregisters. On shutdown we re-raise, preserving the existing drain semantics.)

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_cancel_finalize.py -q` → PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/worker.py tests/services/test_cancel_finalize.py
git commit -m "feat(worker): shielded cancelled-finalize, gated on cancelling status (vs shutdown)"
```

---

## Task 8: Stale-`cancelling` sweep in the worker loop

**Files:**
- Modify: `app/services/worker.py` (the sweep already invoked each loop / `_sweep_stuck_jobs` neighbor)
- Test: `tests/services/test_stale_cancelling_sweep.py`

**Context:** `_sweep_stuck_jobs` already runs at startup + every `sweep_interval`. Add a sibling call that finalizes stale `cancelling` rows so a worker that crashed mid-cancel doesn't strand them.

- [ ] **Step 1: Write the failing test:**

```python
import inspect
from app.services import worker


def test_worker_sweeps_stale_cancelling():
    src = inspect.getsource(worker.Worker)
    assert "reclaim_stale_cancelling" in src, "worker must finalize stale cancelling rows"
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_stale_cancelling_sweep.py -q` → FAIL.

- [ ] **Step 3: Implement.** In `_sweep_stuck_jobs` (which already opens a session and calls `reclaim_stuck_jobs`), add a sibling call in the same transaction:

```python
                async with session.begin():
                    n = await jobs_repo.reclaim_stuck_jobs(
                        session,
                        stale_after_seconds=settings.reclaim_stale_seconds,
                    )
                    n_cancel = await jobs_repo.reclaim_stale_cancelling(
                        session,
                        stale_after_seconds=settings.reclaim_stale_seconds,
                    )
            if n or n_cancel:
                logger.info(
                    f"worker {self.id} sweep reclaimed running={n} stale-cancelling={n_cancel}"
                )
```

(Adapt to the function's existing logging shape; keep the existing `reclaim_stuck_jobs` behavior.)

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/services/test_stale_cancelling_sweep.py -q` → PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/worker.py tests/services/test_stale_cancelling_sweep.py
git commit -m "feat(worker): sweep stale cancelling jobs -> cancelled (crash-mid-cancel safety)"
```

---

## Task 9: `POST /jobs/{job_id}/cancel` endpoint

**Files:**
- Modify: `app/api/v1/jobs.py`
- Test: `tests/api/test_cancel_endpoint.py`

**Context:** Mirror the `/retry` endpoint's shape (`router.post("/jobs/{job_id}/retry")`, `Depends(get_session)`, `Depends(get_current_user)`, returns `JobOut`). Order: try atomic `cancel_if_pending`; if it didn't transition, re-read — `running` → `request_cancel` (set `cancelling`) + cancel the in-process task if present; terminal states → 409.

- [ ] **Step 1: Write the failing tests** `tests/api/test_cancel_endpoint.py`:

```python
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.schemas import JobOut

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def _job_out(status):
    return JobOut(id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status=status)


def test_cancel_pending_is_atomic():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=True)), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="cancelled"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_running_sets_cancelling_and_cancels_task():
    jid = uuid4()
    fake_task = SimpleNamespace(cancel=lambda: setattr(fake_task, "cancelled", True))
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.request_cancel", AsyncMock(return_value=True)), \
         patch.dict("app.services.worker.RUNNING_JOBS", {jid: fake_task}, clear=False), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="cancelling"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200
    assert getattr(fake_task, "cancelled", False) is True


def test_cancel_done_job_409():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.cancel_if_pending", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.request_cancel", AsyncMock(return_value=False)), \
         patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(
             id=jid, status="done"))):
        r = client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 409
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/api/test_cancel_endpoint.py -q` → FAIL (404, no route).

- [ ] **Step 3: Implement.** Add the import at the top of `app/api/v1/jobs.py`:

```python
from app.services.worker import RUNNING_JOBS
```

Add the endpoint (near `retry_job`):

```python
@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Cancel a job. A queued job is cancelled atomically (never starts). A
    running job is flagged `cancelling` and its in-process task (if this process
    owns it) is cancelled immediately; otherwise the owning worker self-cancels
    on its next heartbeat. Terminal jobs (done/failed/cancelled) -> 409."""
    if await jobs_repo.cancel_if_pending(session, job_id):
        await session.commit()
        job = await jobs_repo.get(session, job_id)
        return JobOut.model_validate(job)

    # Not pending — maybe running (or already terminal / gone).
    if await jobs_repo.request_cancel(session, job_id):
        await session.commit()
        task = RUNNING_JOBS.get(job_id)
        if task is not None:
            task.cancel()  # same-process: instant
        job = await jobs_repo.get(session, job_id)
        return JobOut.model_validate(job)

    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    raise HTTPException(409, f"cannot cancel a job with status={job.status!r}")
```

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/api/test_cancel_endpoint.py -q` → 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add app/api/v1/jobs.py tests/api/test_cancel_endpoint.py
git commit -m "feat(api): POST /jobs/{id}/cancel — atomic queue-cancel + running cancelling+task-cancel"
```

---

## Task 10: `/retry` allows cancelled (documented as resume)

**Files:**
- Modify: `app/api/v1/jobs.py` (`retry_job` guard at ~`:207`)
- Test: `tests/api/test_retry_cancelled.py`

- [ ] **Step 1: Write the failing test:**

```python
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user
from app.schemas import JobOut

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_retry_allows_cancelled():
    jid = uuid4()
    job = SimpleNamespace(id=jid, status="cancelled")
    updated = SimpleNamespace(id=jid, book_id=uuid4(), toc_entry_id=uuid4(), subject="kimyo-g7-11", status="pending")
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=job)), \
         patch("app.api.v1.jobs.jobs_repo.reset_for_retry", AsyncMock(return_value=updated)):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_retry_still_rejects_running():
    jid = uuid4()
    with patch("app.api.v1.jobs.jobs_repo.get", AsyncMock(return_value=SimpleNamespace(id=jid, status="running"))):
        r = client.post(f"/api/v1/jobs/{jid}/retry")
    assert r.status_code == 409
```

- [ ] **Step 2: Run, verify FAIL.** `.\.venv\Scripts\python.exe -m pytest tests/api/test_retry_cancelled.py -q` → FAIL (cancelled currently 409s).

- [ ] **Step 3: Implement.** In `retry_job`, change the guard (`jobs.py:207`):

```python
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(
            409,
            f"only failed or cancelled jobs can be retried; current status={job.status!r}",
        )
```

And update the docstring to note: retry **resumes** — it reuses completed phase rows (worklog 0031 resume) and re-runs the rest; use `force=true` on `/generate` for a clean from-scratch redo.

- [ ] **Step 4: Run, verify PASS.** `.\.venv\Scripts\python.exe -m pytest tests/api/test_retry_cancelled.py -q` → 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add app/api/v1/jobs.py tests/api/test_retry_cancelled.py
git commit -m "feat(api): /retry accepts cancelled jobs (resumes; force-generate for fresh)"
```

---

## Task 11: Frontend — Cancel button + status

**Files:**
- Modify: `web/src/lib/types.ts` (`JobStatus`), `web/src/lib/api.ts` (`cancelJob`), the job view route (the file rendering a job's status/actions — locate via `grep -rl "JobStatus\|job.status" web/src/routes`).

- [ ] **Step 1: Extend the status type** in `web/src/lib/types.ts`:

```typescript
export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelling" | "cancelled";
```

- [ ] **Step 2: Add the API method** to the `api` object in `web/src/lib/api.ts` (match the existing method style; `Job` already imported):

```typescript
  async cancelJob(jobId: string): Promise<Job> {
    const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    return unwrap<Job>(res);
  },
```

- [ ] **Step 3: Add the Cancel button** to the job view. Read the job route first (`grep -rl "job.status\|JobStatus" web/src/routes`), then add a Cancel button shown only when `job.status === "pending" || job.status === "running"`, calling `api.cancelJob(job.id)` and refreshing (invalidate the react-query job query / refetch). Render `cancelling`/`cancelled` with a distinct (muted/error) badge wherever the status badge is shown. Follow the file's existing button + badge vocabulary (the repo uses `Button`, `cn`, `--color-*` tokens; status badges already exist for the other states). Disable the button while the request is in flight.

- [ ] **Step 4: Typecheck + build.** `cd web; npx tsc -p tsconfig.app.json --noEmit` → 0 errors; `npm run build` → OK.

- [ ] **Step 5: Commit.**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/routes/<job-route-file>
git commit -m "feat(web): Cancel button + cancelling/cancelled job status"
```

---

## Task 12: Acceptance smoke + worklog

**No code.** Generation-affecting → proven by a real run (CLAUDE.md gate).

- [ ] **Step 1: Suites green.** `.\.venv\Scripts\python.exe -m pytest tests/ -q` → all green except the known pre-existing red.

- [ ] **Step 2: Real cancel smoke (the core proof).** Restart the server. Start a generation on a real lesson; once it's past `extract` and **≥2 content phases are running in parallel** (watch the job/monitor or `agent_usages`), `POST /api/v1/jobs/{id}/cancel`. Confirm, with evidence:
  - The job row → `cancelled` (and `cancelling` was observed transiently).
  - **No provider-CLI processes survive** — check `tasklist | findstr /I "node python"` (Windows) for stragglers spawned by this job, or capture the spawned PIDs from the server log and assert `psutil.pid_exists(pid) is False` for each. This proves C1 + C2 (no orphaned CLIs).
  - **Completed phase rows preserved**, in-flight phase(s) → `failed`: `SELECT phase_name, status FROM phase_outputs WHERE job_id='<id>' ORDER BY phase_order;`.

- [ ] **Step 3: Resume-on-retry smoke.** `POST /api/v1/jobs/{id}/retry` → job → `pending`, re-claimed, and the run **reuses** the preserved `done` phases (only the cancelled/remaining phases re-run — check the logs / `agent_usages` show no re-run of already-done phases).

- [ ] **Step 4: Queue-cancel smoke.** Enqueue a job while the worker is busy (so it stays `pending`), cancel it → `cancelled` immediately, and confirm the worker never claims it.

- [ ] **Step 5: Worklog.** Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + an `INDEX.md` row; note the new `psutil` dep and the `cancelling`/`cancelled` states; cross-link the spec. If a faster cross-process cancel watcher is still wanted, log it in WISHLIST.

---

## Self-Review

**1. Spec coverage:** C1 cascade → T3; C2 psutil kill_tree + wiring → T1,T2; `cancelled`/`cancelling` no-migration status → used across T4/T9 (plain string values, no migration task needed); atomic queue-cancel + race fall-through → T4 (`cancel_if_pending`/`request_cancel`) + T9; RUNNING_JOBS registry → T5; heartbeat status-read + self-cancel (net-new) → T6; shielded discriminating finalize → T7; mark_cancelled preserve-done → T4 + T7; stale-cancelling sweep → T4 + T8; `/retry` allows cancelled as resume → T10; frontend → T11; both-platform + worker-trap tests → T1 (real kill), T6/T7/T8 (traps), T12 (smoke). No gaps.

**2. Placeholder scan:** Every code step has real code. The frontend button step (T11.3) gives exact state/handler rules + the precise type/api additions and is gated by `tsc`+`build` (the repo has no FE unit suite) — a scoped instruction, not a TODO. The scheduler/worker source-level tests are `inspect`-based by necessity (DB-free harness; deeply-integrated functions), with behavior proven by the T12 smoke — consistent with the repo's existing `inspect` tests and CLAUDE.md's acceptance gate.

**3. Type consistency:** `kill_tree(pid, *, wait_timeout=3.0)` defined T1, called `kill_tree(proc.pid)` T2. Repo fns `cancel_if_pending->bool`, `request_cancel->bool`, `get_status->str|None`, `mark_cancelled->None`, `reclaim_stale_cancelling->int` defined T4, consumed in T6/T7/T8/T9 with matching shapes. `RUNNING_JOBS: dict[UUID, asyncio.Task]` defined T5, used T6/T7/T9. `JobStatus` adds `cancelling|cancelled` T11 matching the backend strings. Endpoint returns `JobOut` (T9/T10) consistent with the existing `retry_job`.
