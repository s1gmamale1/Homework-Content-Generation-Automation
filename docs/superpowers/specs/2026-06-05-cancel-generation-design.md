# Cancel Generation — design

**Status:** Design approved in brainstorm — ready for writing-plans (pending user review of this doc).
**Date:** 2026-06-05
**Branch:** Nggaev-v2

## Goal

Let an operator cancel a homework-generation job. A cancel must **stop the work for real**: kill every running provider-CLI subprocess (and their children), unwind the pipeline, and mark the job `cancelled` — not merely flag it while the CLIs keep burning tokens. Works for a queued (not-yet-started) job and a running one.

User decision (brainstorm): **hard stop** — discard the in-flight phase's partial work. Completed phases are *preserved* (so `/retry` can resume; see §6).

## Background — verified findings (this is why the feature is mostly in the scheduler, not the endpoint)

1. **`_spawn` kills its subprocess only on its OWN `CancelledError`** — `agent.py:346-347` (`except asyncio.CancelledError: proc.kill()`). Real, but **top-process only** (`proc.kill()` kills the direct child on both Windows and Linux; node/python CLIs can spawn helpers that survive).
2. **🔴 Cancellation does NOT propagate through the parallel scheduler today.** `_run_content_phases_parallel` (`pipeline.py:395-453`) launches each phase as a detached `asyncio.create_task` into `in_flight` (`:402`) and waits at `await asyncio.wait(list(in_flight.values()), FIRST_COMPLETED)` (`:434`). `asyncio.wait()` does **not** cancel its awaitables when the waiter is cancelled. The only peer-cancel logic (`:447-452`) lives *inside* the `except Exception` from `task.result()` — it fires on a **phase failure**, never on external cancellation, and there's no `try/finally` around the loop. **So cancelling `pipeline.run` today orphans every in-flight phase task and its CLI.** This is the core of the feature.
3. **The sequential head is already fine.** `extract` runs as a plain `await _execute_one_phase(...)` inside `pipeline.run`, so external cancellation propagates normally → `_spawn`'s kill fires directly. **Only the parallel tail needs the explicit cancel-and-gather.**
4. **Worker treats `CancelledError` as shutdown, not cancel** — `worker.py:199-206` deliberately leaves the job `running` for reclaim. A user-cancel must instead end as `cancelled`. Needs an intent discriminator.
5. **`status` is a plain `String(32)`** (`homework_job.py:18`), no enum/CHECK → `cancelled`/`cancelling` are new values, **no migration**. Queue consumers are safe: `claim_next_job` filters `status='pending'` (`jobs.py:215`); `reclaim_stuck_jobs`/sweeps filter `running` (`jobs.py:167,270`).
6. **`/retry` is resume, not fresh** — guard at `jobs.py:207` (`status != "failed"` → 409); `reset_for_retry` (`jobs.py:141-163`) resets the job to `pending` and **does not touch phase rows**; resume is always-on (worklog 0031). So `/retry` reuses completed phases and re-runs the rest. `force=true` on `/generate` is the from-scratch path.

## Architecture

### 🔴 Core (the feature lives here)

**C1 — Scheduler cancellation cascade** (`pipeline.py` `_run_content_phases_parallel`).
Wrap the scheduling loop so an external `CancelledError` cancels every in-flight phase and reaps them before re-raising — reusing the exact pattern already at `:447-452`, but triggered by cancellation:

```python
try:
    while ...:                      # existing loop
        ...
        done, _ = await asyncio.wait(list(in_flight.values()), return_when=asyncio.FIRST_COMPLETED)
        ...
except asyncio.CancelledError:
    for t in in_flight.values():
        t.cancel()
    if in_flight:
        await asyncio.gather(*in_flight.values(), return_exceptions=True)  # lets each _spawn proc-kill fire
    raise
```

The `gather` is load-bearing: it gives each cancelled `_execute_phase` → `_spawn` the chance to run its `except CancelledError: <kill>` before the pipeline unwinds. Without it, the CLIs orphan.

**C2 — Portable process-tree kill** (`psutil`).
`proc.kill()` is top-process-only; production runs on **Linux/k8s** (separate worker pods, `worker_concurrency=0`), dev is Windows. Use `psutil` for one tested code path on both. New helper `app/services/proc_tree.py::kill_tree(pid)`:
1. `parent = psutil.Process(pid)`; `parent.suspend()` (freeze it so it can't spawn more children mid-kill — closes the snapshot window).
2. `children = parent.children(recursive=True)`.
3. kill children then parent (`proc.kill()` each, ignore `NoSuchProcess`).
4. `psutil.wait_procs([*children, parent], timeout=3)` to reap.
Wire into `_spawn`'s `except asyncio.CancelledError` (replace the bare `proc.kill()` with `kill_tree(proc.pid)`); the spawn call itself is **unchanged** (psutil's advantage over `start_new_session=True`).
*Dependency note:* `psutil` is a new dep (this repo is dep-disciplined — google-genai was removed). Justified: hand-rolled cross-platform tree-kill (Windows `taskkill /F /T` + Linux `os.killpg`) is two error-prone paths touching the hot spawn path. Add to `pyproject.toml`.

### 🟡 Plumbing (straightforward)

**P1 — `POST /api/v1/jobs/{job_id}/cancel`** (`app/api/v1/jobs.py`).
- **Atomic queue-cancel:** `UPDATE homework_jobs SET status='cancelled' WHERE id=:id AND status='pending'`. If it updated a row → done (the job is unclaimed; the worker never starts it). Return the job.
- **Race fall-through:** if 0 rows updated, re-read. If `running` → set `status='cancelling'` (the cross-process signal) and, **if this process holds the task** (registry, P2), cancel it immediately (instant for the embedded-worker default). If already `done`/`failed`/`cancelled` → 409.

**P2 — Running-job registry + intent discriminator** (`app/services/worker.py`).
- A process-local `RUNNING_JOBS: dict[UUID, asyncio.Task]` mapping job_id → the `_execute_job` task (populated at `:119-121`, removed in the done-callback). The same-process cancel endpoint looks the job up here and cancels its task directly = instant.
- **Cross-process (separate pods):** the worker that owns the job sees `status='cancelling'` on its next heartbeat (`_heartbeat`, every `settings.heartbeat_seconds`) and cancels its local task. Latency for the separate-pod case ≈ `heartbeat_seconds`; instant for embedded. (A dedicated faster cancel-watcher is a tunable follow-on, not v1.)
- **Discriminator:** `_execute_job`'s `except asyncio.CancelledError` (`:199`) re-reads the job; if `status` is `cancelling` (or job_id is in a process-local "cancelling" set) → mark `cancelled`; else → existing shutdown behavior (leave `running` for reclaim).

**P3 — Cancelled end-state** (`worker.py` + `app/repositories/jobs.py`).
On user-cancel finalize: job → `cancelled`; the **in-flight (killed) phase(s)** → `failed` (they were interrupted); **completed phase rows are left intact** (so `/retry` resume can reuse them — §6). New repo helper `mark_cancelled(job_id)`.

**P4 — `/retry` allows cancelled** (`jobs.py:207`).
Extend the guard `status != "failed"` → `status not in ("failed", "cancelled")`. Document in the docstring that this **resumes** (reuses completed phases); point at `force=true` on `/generate` for a clean redo. No change to `reset_for_retry`.

**P5 — Frontend** (`web/`).
A **Cancel** button on the job/monitor screen for a `pending`/`running` job → `api.cancelJob(id)` → `POST .../cancel`. Show the `cancelled` status (distinct badge) wherever `JobStatus` is rendered. Add `"cancelling" | "cancelled"` to the `JobStatus` type.

## Status lifecycle

```
pending  --cancel-->  cancelled                      (atomic UPDATE; never claimed)
running  --cancel-->  cancelling  --(task ends)-->  cancelled
                                  in-flight phase(s) -> failed; done phases preserved
cancelled --/retry--> pending  --(resume)-->  reuses done phases, re-runs the rest
```

## Data flow (running-job cancel)

```
POST /jobs/{id}/cancel
  ├─ atomic UPDATE WHERE status='pending'  → 1 row? → cancelled, return
  └─ 0 rows → re-read
        running → set status='cancelling'
                ├─ job in RUNNING_JOBS (same process)? cancel task NOW
                └─ else: owning worker sees 'cancelling' on next heartbeat → cancels its task
  task cancelled → CancelledError into _execute_job → into wait_for(pipeline.run)
     → scheduler C1: cancel + gather in_flight → each _spawn C2: kill_tree(pid) → all CLIs (+children) dead
  _execute_job sees CancelledError + status='cancelling' → mark_cancelled (job=cancelled, in-flight phases=failed)
```

## Testing strategy

- **DB-free unit/integration** (per `tests/conftest.py`): endpoint transitions — atomic pending→cancelled; running→cancelling; race fall-through (0-rows → running path); done/failed/cancelled → 409. `/retry` accepts `cancelled`. Status-consumer guards unaffected.
- **`kill_tree` real test** — spawn a parent process that itself spawns a child (e.g. a python one-liner that `subprocess.Popen`s a `sleep`/`timeout`), call `kill_tree(parent.pid)`, assert **both** PIDs are gone (`psutil.pid_exists`). Runs on the dev host (Windows); CLAUDE.md notes Linux is the prod target — the plan must also exercise it on Linux (CI/container) since psutil's behavior, while uniform, should be proven on both.
- **Scheduler cascade unit** — a fake `_execute_one_phase` that blocks on an `asyncio.Event`; launch the parallel scheduler, cancel the outer task, assert every in-flight fake was cancelled (no orphans).
- **Acceptance smoke (CLAUDE.md gate — generation-affecting):** real generation, cancel mid-flight while ≥2 phases run in parallel; assert (a) job → `cancelled`, (b) **no** provider-CLI processes survive (`tasklist`/`ps`), (c) completed phase rows preserved + in-flight phase `failed`, (d) `/retry` resumes (reuses the preserved phases).

## Risks / notes

- **Tree-kill snapshot window** — mitigated by `suspend()`-parent-first then sweep (C2).
- **Cross-process latency** — separate-pod cancel is bounded by `heartbeat_seconds`; embedded is instant. Faster watcher is a logged follow-on.
- **psutil dependency** — new; justified for a single tested kill path (see C2).
- **Double-cancel / cancel-after-done** — idempotent: the atomic UPDATE and the `cancelling`-guarded finalize both no-op if the job already moved on.

## Out of scope

- Pause/resume UI; partial-output recovery beyond the existing resume model.
- Fresh-redo-on-cancel (wiping phase rows) — diverges from the resume model; use `force=true` generate instead.
- A sub-`heartbeat_seconds` cross-process cancel watcher (tunable follow-on).
