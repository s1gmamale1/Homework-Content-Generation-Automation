# Job Resilience — Resume + Provider Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a generation job survive interruption — resume skips `done` phases on re-run; per-phase provider failover completes a phase on a fallback provider instead of failing the whole job — over one shared "rebuild phase context from persisted rows" foundation.

**Architecture:** Four mechanisms, **heartbeat sequenced first** (the fast-reclaim window can only drop below `job_timeout` once a claim heartbeat keeps live jobs fresh). Failover wraps the CLI call inside `_execute_phase` via a pure, injectable driver; resume seeds the scheduler's `pending` set from live `phase_outputs` rows; attribution records the producing provider per phase.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic (Postgres/JSONB), asyncio, pytest (DB-free suite — verify by signature / `inspect.getsource` / pure-function unit tests; migrations proven by `alembic upgrade`).

**Spec:** `docs/superpowers/specs/2026-06-03-job-resilience-resume-failover-design.md`

**Commands:**
- Backend tests: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q` (single: `… -m pytest tests/path::test -v`)
- Migration: `uv run alembic upgrade head` / `uv run alembic downgrade -1` / `uv run alembic heads`

**Test-harness note (critical):** this repo's suite is **DB-free** — `tests/conftest.py` injects sentinel env only ("no real database is wired up here"); repo tests assert by `inspect.signature` / `inspect.getsource` / pure functions, **never** a live session. Do NOT invent a DB fixture. Migrations are proven by `alembic upgrade`, not pytest. Mirror `tests/repositories/test_phase_validation_warnings.py` (the Effort A pattern) for any column/repo work.

**Ordering rule (hard):** T1→T2 additive (safe anytime). **T3 (heartbeat) MUST land before T4 (fast reclaim)** — without the heartbeat, `reclaim_stale_seconds < job_timeout` reclaims *live* jobs mid-run → concurrent duplicate execution. T5→T6 (classifier→failover) and T7 (resume) are independent of the reclaim pair. T8 is acceptance.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `app/config.py` | 4 resilience settings (heartbeat, lease, per-attempt timeout, failover order) | T1 |
| `app/models/phase_output.py` · `app/repositories/phase_outputs.py` · `alembic/versions/0019_*` | `phase_outputs.provider` (additive) | T2 |
| `app/repositories/jobs.py` · `app/services/worker.py` | claim **heartbeat** (`touch_claim` + refresh task) | T3 |
| `app/services/worker.py` · `main.py` | reclaim uses `reclaim_stale_seconds`; startup resets orphaned `running`→`pending` | T4 |
| `app/services/failure_classifier.py` (new) | pure `classify(error) -> transient\|wall\|hard` | T5 |
| `app/services/pipeline.py` | `_failover_chain` + `_run_with_failover`; `_execute_phase` uses it; records provider | T6 |
| `app/services/pipeline.py` | resume: seed `pending`/`prior_outputs`/`lesson_context` from done rows | T7 |
| acceptance + worklog | real kill-mid-run + forced-failover smokes | T8 |

---

## Task 1: Resilience settings

**Files:**
- Modify: `app/config.py` (after line 51, the `gemini_max_concurrency` line)
- Test: `tests/services/test_config_resilience.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_config_resilience.py
from app.config import Settings


def test_resilience_defaults_and_invariants():
    # _env_file=None isolates from the local .env (mirrors the notion-config lesson).
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    # Heartbeat MUST be well below the lease TTL, else a live job's claim goes stale.
    assert s.heartbeat_seconds < s.reclaim_stale_seconds
    # per-attempt timeout bounds a hung CLI (e.g. opencode) — must be positive.
    assert s.per_attempt_timeout_seconds > 0
    # claude is reserved for the user's Max allocation — never a fallback target.
    assert "claude" not in s.failover_provider_order
    assert s.failover_provider_order  # non-empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_config_resilience.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'heartbeat_seconds'`.

- [ ] **Step 3: Add the settings**

Confirm `Field` is imported at the top of `app/config.py` (`from pydantic import Field`); add it if absent. Then insert after line 51:

```python

    # ─── Resilience: job resume + provider failover ───────────────────────
    # Worker refreshes claimed_at every heartbeat_seconds while a job runs, so a
    # live long job's claim never looks stale. MUST be << reclaim_stale_seconds.
    heartbeat_seconds: int = 30
    # Lease TTL: a `running` job whose claimed_at is older than this is treated
    # as orphaned (dead worker) → reclaimed to `pending`. Safe BELOW job_timeout
    # ONLY because the heartbeat keeps live jobs fresh (spec §3).
    reclaim_stale_seconds: int = 120
    # Hard timeout for ONE failover attempt (one provider try), so a hung CLI
    # (e.g. opencode stdin hang) cannot stall a phase until job_timeout.
    per_attempt_timeout_seconds: int = 300
    # Fallback provider order for per-phase failover. claude is intentionally
    # ABSENT — reserved for the user's Claude Max allocation (provider isolation).
    failover_provider_order: list[str] = Field(
        default_factory=lambda: ["codex", "gemini", "kimi", "opencode"]
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_config_resilience.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/services/test_config_resilience.py
git commit -m "feat(config): resilience settings (heartbeat, lease TTL, per-attempt timeout, failover order)"
```

---

## Task 2: `phase_outputs.provider` (additive attribution column)

**Files:**
- Modify: `app/models/phase_output.py` (after `model_name`, line 20)
- Modify: `app/repositories/phase_outputs.py` (`set_status` `:96-123`; `create_or_reset` reset block `:63-74`)
- Create: `alembic/versions/0019_phase_provider.py`
- Test: `tests/repositories/test_phase_provider.py` (new)

- [ ] **Step 1: Write the failing test** (DB-free, mirrors `test_phase_validation_warnings.py`)

```python
# tests/repositories/test_phase_provider.py
import inspect

from app.models import PhaseOutput
from app.repositories import phase_outputs as phase_repo


def test_set_status_accepts_provider_param():
    assert "provider" in inspect.signature(phase_repo.set_status).parameters


def test_model_has_provider_attribute():
    po = PhaseOutput(
        job_id=None, phase_name="flashcards", phase_order=1,
        prompt_hash="h", model_name="m", status="pending",
    )
    assert po.provider is None


def test_create_or_reset_clears_provider():
    assert "provider = None" in inspect.getsource(phase_repo.create_or_reset)
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_phase_provider.py -q`
Expected: FAIL — `provider` not in signature; `AttributeError`; getsource check fails.

- [ ] **Step 3: Add the model column**

In `app/models/phase_output.py`, after the `model_name` column (line 20) add:

```python
    # The provider that ACTUALLY produced this phase (may differ from the job's
    # requested provider after failover). Nullable; job badge = requested.
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
```

(`String` and `Optional` are already imported in this file.)

- [ ] **Step 4: Thread it through the repo**

In `app/repositories/phase_outputs.py` `set_status` signature, after `error_message: Optional[str] = None,` add:

```python
    provider: Optional[str] = None,
```

and in the body, after the `error_message` block (line ~123):

```python
    if provider is not None:
        po.provider = provider
```

In `create_or_reset`, inside the `if existing is not None:` reset block (after `existing.completed_at = None`, line 73) add:

```python
        existing.provider = None
```

- [ ] **Step 5: Create the additive migration**

First confirm the current head: `uv run alembic heads` → expect `e2a5b8c4f1d9` (Effort A 0018). Then:

```python
# alembic/versions/0019_phase_provider.py
"""phase_outputs.provider (per-phase attribution)

Revision ID: a7c1e9d2b4f8
Revises: e2a5b8c4f1d9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e9d2b4f8"
down_revision: Union[str, Sequence[str], None] = "e2a5b8c4f1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phase_outputs", sa.Column("provider", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("phase_outputs", "provider")
```

- [ ] **Step 6: Apply + run tests**

Run: `uv run alembic upgrade head` then `& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_phase_provider.py -q`
Expected: migration OK; 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/phase_output.py app/repositories/phase_outputs.py alembic/versions/0019_phase_provider.py tests/repositories/test_phase_provider.py
git commit -m "feat(phases): per-phase provider attribution column + repo plumbing"
```

---

## Task 3: Claim heartbeat (PREREQUISITE for fast reclaim)

**Files:**
- Modify: `app/repositories/jobs.py` (add `touch_claim`)
- Modify: `app/services/worker.py` (`_execute_job` `:172-200`; add `_heartbeat`)
- Test: `tests/services/test_worker_heartbeat.py` (new)

- [ ] **Step 1: Write the failing test** (DB-free — signature + source)

```python
# tests/services/test_worker_heartbeat.py
import inspect

from app.repositories import jobs as jobs_repo
from app.services.worker import Worker


def test_touch_claim_exists_and_scoped():
    assert hasattr(jobs_repo, "touch_claim")
    src = inspect.getsource(jobs_repo.touch_claim)
    # Only refresh a row that is still RUNNING (never resurrect a finished job).
    assert 'status == "running"' in src or "status==\"running\"" in src
    assert "claimed_at" in src


def test_execute_job_runs_and_cancels_heartbeat():
    src = inspect.getsource(Worker._execute_job)
    assert "_heartbeat" in src          # heartbeat task started
    assert "cancel()" in src            # ...and cancelled when the job ends


def test_heartbeat_uses_configured_interval():
    src = inspect.getsource(Worker._heartbeat)
    assert "heartbeat_seconds" in src
    assert "touch_claim" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_worker_heartbeat.py -q`
Expected: FAIL — `touch_claim` missing; `_heartbeat` missing.

- [ ] **Step 3: Add `touch_claim` to the jobs repo**

In `app/repositories/jobs.py`, after `claim_next_job` (ends line 239) add:

```python
async def touch_claim(session: AsyncSession, job_id: UUID) -> None:
    """Heartbeat: refresh claimed_at on a still-running job so the lease-TTL
    reclaim never treats a live worker's job as orphaned. No-ops once the job
    leaves `running` (done/failed), so it can't resurrect a finished row."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(claimed_at=datetime.now(timezone.utc))
    )
```

(`update`, `datetime`, `timezone`, `UUID`, `HomeworkJob` are already imported in this module.)

- [ ] **Step 4: Add the heartbeat task + wire it into `_execute_job`**

In `app/services/worker.py`, add a method on `Worker` (place it just above `_execute_job`):

```python
    async def _heartbeat(self, job_id: UUID) -> None:
        """Periodically refresh the job's claim while its pipeline runs."""
        while True:
            await asyncio.sleep(settings.heartbeat_seconds)
            try:
                async with SessionLocal() as session:
                    await jobs_repo.touch_claim(session, job_id)
                    await session.commit()
            except Exception:
                logger.warning(f"worker {self.id} heartbeat failed for job={job_id}")
```

Then wrap the run in `_execute_job` (currently `:175-200`). Start the heartbeat before the inner `try`, cancel it in the `finally`:

```python
    async def _execute_job(self, job_id: UUID) -> None:
        """Run one pipeline. Releases the slot in `finally` so the next
        iteration of the main loop can claim another job."""
        hb = asyncio.create_task(self._heartbeat(job_id))
        try:
            try:
                await asyncio.wait_for(
                    pipeline.run(job_id), timeout=self.job_timeout
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"worker {self.id} job={job_id} TIMED OUT after "
                    f"{self.job_timeout}s"
                )
                await self._mark_failed(job_id, f"timeout after {self.job_timeout}s")
            except asyncio.CancelledError:
                logger.warning(
                    f"worker {self.id} job={job_id} CANCELLED during shutdown"
                )
                raise
            except Exception as exc:
                logger.exception(
                    f"worker {self.id} job={job_id} CRASHED: {exc!r}"
                )
                await self._mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb   # let the cancellation settle — avoids a stray "Task destroyed" warning
            self._slots.release()
```

Add `import contextlib` at the top of `worker.py` (and confirm `asyncio` is imported — it is). Confirm `settings` is imported in `worker.py` (it is — `from app.config import settings` is used elsewhere; add if missing).

- [ ] **Step 5: Run tests**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_worker_heartbeat.py -q`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/repositories/jobs.py app/services/worker.py tests/services/test_worker_heartbeat.py
git commit -m "feat(worker): claim heartbeat (refresh claimed_at while a job runs)"
```

---

## Task 4: Fast reclaim via lease-TTL + startup orphan-reset

**Files:**
- Modify: `app/services/worker.py` (reclaim call `:235`, log `:240`)
- Modify: `main.py` (startup sweep `:30-48`)
- Test: `tests/services/test_reclaim_window.py` (new)

> Safe ONLY because Task 3 landed: a live job's `claimed_at` is refreshed every 30s, so a 120s lease never expires under a live worker; only a dead worker lets it lapse.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_reclaim_window.py
import inspect

import main
from app.services.worker import Worker


def test_reclaim_uses_lease_ttl_not_job_timeout():
    src = inspect.getsource(Worker._sweep_stuck_jobs)   # the periodic + startup reclaim sweep
    assert "reclaim_stale_seconds" in src
    assert "job_timeout * 2" not in src             # the old window is gone


def test_startup_resets_orphaned_running_jobs():
    src = inspect.getsource(main.lifespan)
    # Startup flips orphaned running jobs back to pending (stale_after_seconds=0
    # is correct at boot: no workers are alive, so every running row is orphaned).
    assert "reclaim_stuck_jobs" in src
```

> Scene-setting (verified): the reclaim sweep is `Worker._sweep_stuck_jobs` (`worker.py:227`) — there is **no** `_reclaim_loop`. It runs **both** at worker startup (`run()`, `:86`) and inline-throttled in the main loop (`:95`). The `stale_after_seconds=self.job_timeout * 2` → `settings.reclaim_stale_seconds` change is inside it (`:233-240`). **Consequence to note:** after the change the *worker's own startup sweep* (`:86`) also uses the 120s lease window (down from 1h) — harmless, and in embedded mode the lifespan reset (Step 4) runs first anyway. `main.lifespan` is the FastAPI lifespan context in `main.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_reclaim_window.py -q`
Expected: FAIL — `reclaim_stale_seconds` not referenced in worker; `reclaim_stuck_jobs` not in lifespan.

- [ ] **Step 3: Point the periodic reclaim at the lease TTL**

In `app/services/worker.py` (the reclaim sweep, `:233-240`), change:

```python
                    n = await jobs_repo.reclaim_stuck_jobs(
                        session,
                        stale_after_seconds=settings.reclaim_stale_seconds,
                    )
```

and the following log line (`:240`) from `{self.job_timeout * 2}s` to:

```python
                    f"(stale > {settings.reclaim_stale_seconds}s)"
```

- [ ] **Step 4: Reset orphaned `running` jobs on startup**

In `main.py`, inside `lifespan`, after the existing `phase_outputs` sweep (after line 47, before the `log.info("Orphan sweep complete …")`) add a job reset and import `jobs_repo`:

```python
        n = await jobs_repo.reclaim_stuck_jobs(session, stale_after_seconds=0)
        if n:
            log.info(f"Startup: reclaimed {n} orphaned running job(s) -> pending")
```

At the top of `main.py`, add `from app.repositories import jobs as jobs_repo` if not already imported. `stale_after_seconds=0` makes the cutoff `now`, so every `running` row flips to `pending`. Combined with resume (T7) the restart picks up exactly where it left off.

> **⚠ Single-host assumption (review-flagged, verified real).** `stale_after_seconds=0` resets **every** `running` job globally, on the premise "no live workers exist at boot." That holds for the **single-host / embedded** deployment (the current Max setup) — and the spec accepts it (§3a). It is **NOT safe in a horizontally-scaled deploy** (multiple API pods, or split worker pods, per CLAUDE.md `worker_concurrency=0`): a restarting pod would reset jobs a **live peer** is actively heartbeating → the peer keeps running while another worker re-claims → duplicate execution + `create_or_reset` clobber, exactly the hazard the heartbeat exists to prevent. **For multi-pod, replace the `0` with `settings.reclaim_stale_seconds`** (rely on the lease: a dead pod's jobs lapse in ~120s; a live peer's stay fresh) — accepting ~120s slower restart recovery for correctness. Note: host-scoped reset (`claimed_by == this host`) does **not** work cleanly because identity is `hostname:pid` and pid changes across a restart. **State this assumption in the code comment at the edit site.**

- [ ] **Step 5: Run tests + full suite**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_reclaim_window.py tests/ -q`
Expected: new tests PASS; full suite still green (1 known pre-existing red `test_notion_defaults_disabled` is acceptable, unrelated).

- [ ] **Step 6: Commit**

```bash
git add app/services/worker.py main.py tests/services/test_reclaim_window.py
git commit -m "feat(worker): lease-TTL reclaim + startup orphan-reset (heartbeat-gated fast recovery)"
```

---

## Task 5: Failure classifier (pure module)

**Files:**
- Create: `app/services/failure_classifier.py`
- Test: `tests/services/test_failure_classifier.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_failure_classifier.py
from app.services import failure_classifier as fc


def test_transient_server_shed():
    assert fc.classify("claude CLI exited rc=1 :: Server is temporarily limiting requests") == "transient"
    assert fc.classify("gemini CLI exited rc=1 :: socket connection closed unexpectedly") == "transient"


def test_not_your_usage_limit_is_transient_not_wall():
    # 'not your usage limit' contains the 'usage limit' wall substring — transient must win.
    assert fc.classify("Rate limited (not your usage limit)") == "transient"


def test_allocation_wall():
    assert fc.classify("You have reached your weekly usage limit") == "wall"


def test_unknown_defaults_to_hard():
    assert fc.classify("codex CLI exited rc=1 :: ModelNotFoundError") == "hard"


def test_accepts_exception_object():
    assert fc.classify(RuntimeError("temporarily limiting requests")) == "transient"
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failure_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.failure_classifier`.

- [ ] **Step 3: Implement**

```python
# app/services/failure_classifier.py
"""Deterministic classification of a phase CLI failure into a recovery class.

Pure, no I/O. `agent.run_phase_prompt` raises `RuntimeError` whose message
embeds the provider, `rc=N`, and a stderr/result snippet — we classify off
that string. Signal lists are refined against real CLI stderr during build;
anything unrecognized falls to `hard`, which the failover driver treats as
"one same-provider retry then fail over" (safe default).
"""

from __future__ import annotations

# Checked FIRST. NOTE: 'not your usage limit' must be matched here before the
# 'usage limit' wall substring below, or a transient server-shed is miscaught.
_TRANSIENT = (
    "not your usage limit",
    "temporarily limiting requests",
    "socket connection closed unexpectedly",
    "connection reset",
    "timed out",
    "timeout",
    "overloaded",
    "503",
    "try again",
)
_WALL = (
    "weekly limit",
    "usage limit reached",
    "usage limit",
    "quota",
    "rate limit reached",
)


def classify(error: "str | BaseException") -> str:
    """-> 'transient' | 'wall' | 'hard'. Transient is checked before wall."""
    msg = str(error).lower()
    if any(s in msg for s in _TRANSIENT):
        return "transient"
    if any(s in msg for s in _WALL):
        return "wall"
    return "hard"
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failure_classifier.py -q`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/failure_classifier.py tests/services/test_failure_classifier.py
git commit -m "feat(resilience): deterministic CLI failure classifier (transient/wall/hard)"
```

---

## Task 6: Provider failover driver + `_execute_phase` integration

**Files:**
- Modify: `app/services/pipeline.py` (add `_failover_chain`, `_run_with_failover`; rewrite `_execute_phase` non-extract branch `:542-558`; record provider on the done `set_status`)
- Test: `tests/services/test_failover_driver.py` (new)

> The driver is a **pure, injectable** helper (takes a `run_fn(provider, model)` coroutine), so it's unit-testable with no DB and no CLI. `_execute_phase` supplies the real `run_fn`. The backoff sleep is OUTSIDE `run_fn`, so it does **not** hold the `gemini_max_concurrency` slot (the slot is held only inside `agent.run_phase_prompt`) — satisfies the spec's "backoff releases the slot."
>
> **Timeout-budget interaction — RATIFIED (review-flagged, accepted 2026-06-03):** the per-phase failover budget can exceed `job_timeout`. Worst case = 5-provider chain × (1 + transient-budget 2) attempts × `per_attempt_timeout_seconds` (300s) + backoff ≈ 4500s, past `job_timeout` (1800s) → the worker's outer `asyncio.wait_for` fires → whole-job hard-fail → reclaim + **resume recovers it** (re-runs only the still-unfinished phase). So it's **self-healing, not fatal**, and realistic failures are fast (rc=1 in seconds), not 300s hangs — the worst case needs multiple providers each *hanging* (which `per_attempt_timeout` already bounds). **Decision: ACCEPTED** — `job_timeout` is the backstop, resume recovers, no per-phase total-failover deadline added. Revisit only if a real smoke shows a timeout collision (a one-line `asyncio.wait_for(_run_with_failover(...), timeout=job_timeout - margin)` is the escape hatch if ever needed).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_failover_driver.py
import asyncio

import pytest

from app.services.pipeline import _failover_chain, _run_with_failover


def test_chain_requested_first_then_order_no_claude():
    chain = _failover_chain("claude")
    assert chain[0] == "claude"            # requested honored first
    assert "claude" not in chain[1:]       # never a fallback target
    # the configured fallbacks, minus a duplicate of the requested
    assert chain[1:] == ["codex", "gemini", "kimi", "opencode"]


def test_chain_skips_requested_in_fallbacks():
    assert _failover_chain("gemini") == ["gemini", "codex", "kimi", "opencode"]


def test_failover_switches_provider_on_hard_failure():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError("claude CLI exited rc=1 :: ModelNotFoundError")  # hard
        return f"# ok from {provider}", 1, 2

    out, tin, tout, produced = asyncio.run(
        _run_with_failover(requested_provider="claude", model="claude-sonnet-4-6", run_fn=run_fn)
    )
    assert produced == "codex"
    assert out == "# ok from codex"
    # claude tried (1 hard same-retry) then codex; never a later fallback.
    assert calls.count("claude") == 2 and calls[-1] == "codex"


def test_wall_fails_over_with_no_same_retry():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError("weekly usage limit reached")  # wall
        return "# ok", 0, 0

    asyncio.run(_run_with_failover(requested_provider="claude", model="m", run_fn=run_fn))
    assert calls.count("claude") == 1     # wall = 0 same-provider retries


def test_all_providers_exhausted_raises():
    async def run_fn(provider, model):
        raise RuntimeError("weekly usage limit reached")  # wall everywhere

    with pytest.raises(RuntimeError):
        asyncio.run(_run_with_failover(requested_provider="claude", model="m", run_fn=run_fn))
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failover_driver.py -q`
Expected: FAIL — `cannot import name '_failover_chain'`.

- [ ] **Step 3: Add the chain + driver to `pipeline.py`**

Add near the top of `app/services/pipeline.py` (after the imports; add `from app.services import failure_classifier` to the `from app.services import …` line, and ensure `settings` is imported — it is):

```python
def _failover_chain(requested_provider: str) -> list[str]:
    """Requested provider first, then settings.failover_provider_order, skipping
    the requested one and any dup. claude is absent from the configured order, so
    a claude job tries claude first but never falls *back* to claude."""
    chain = [requested_provider]
    for p in settings.failover_provider_order:
        if p != requested_provider and p not in chain:
            chain.append(p)
    return chain


# Same-provider retry budget per failure class before moving to the next provider.
_SAME_RETRY_BUDGET = {"transient": 2, "hard": 1, "wall": 0}


async def _run_with_failover(*, requested_provider: str, model: Optional[str], run_fn):
    """Run a phase across the failover chain. `run_fn(provider, model)` returns
    (output_md, tokens_in, tokens_out). On failure, classify → retry same (per
    budget, exp backoff) or move to the next provider. Each attempt is bounded by
    settings.per_attempt_timeout_seconds (kills a hung CLI). Fallback providers
    get model=None (the job's model is provider-specific; None → provider default,
    preserving the _resolve_model no-leak invariant). Returns
    (output_md, tin, tout, produced_by); raises the last error when all fail."""
    last_exc: Optional[Exception] = None
    for prov in _failover_chain(requested_provider):
        attempt_model = model if prov == requested_provider else None
        same = 0
        while True:
            try:
                out, tin, tout = await asyncio.wait_for(
                    run_fn(prov, attempt_model),
                    timeout=settings.per_attempt_timeout_seconds,
                )
                return out, tin, tout, prov
            except Exception as exc:  # noqa: BLE001 — classify, don't swallow
                budget = _SAME_RETRY_BUDGET[failure_classifier.classify(exc)]
                if same < budget:
                    same += 1
                    await asyncio.sleep(2 ** same)  # ~2s, ~4s — slot already released
                    continue
                last_exc = exc
                break  # exhausted this provider → next in chain
    raise last_exc or RuntimeError(f"{requested_provider}: all providers exhausted")
```

- [ ] **Step 4: Use the driver in `_execute_phase`**

Replace the non-extract `else` branch (`:542-558`) with a `run_fn` + driver call, and capture `produced_by`. Also set `produced_by` for the extract path so attribution is uniform:

In the extract branch, after `output_md, tin, tout = await agent.extract_lesson_context(...)` (line 540) add:

```python
            produced_by = settings.extract_provider
```

(and in the cached-extract early return at `:525`, attribution is the original extractor — leave as-is; resume/attribution for a cached extract is out of scope.)

Replace the `else` branch with:

```python
        else:
            phase_prompt = get_prompt(subject, phase_name)

            async def _run(prov: str, mdl: Optional[str]):
                return await agent.run_phase_prompt(
                    provider=prov,
                    model=mdl,
                    phase_prompt=phase_prompt,
                    attachments=[pdf_path] if attach_file else [],
                    lesson_context=lesson_context or "",
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                    phase_name=phase_name,
                    max_output_tokens=max_output_tokens_for(phase_name),
                    homework_job_id=job_id,
                    phase_output_id=po_id,
                    source_map_digest=source_map_digest,
                )

            output_md, tin, tout, produced_by = await _run_with_failover(
                requested_provider=provider, model=model, run_fn=_run,
            )
            parsed_struct = None
```

Then in the final done `set_status` (after the validator block, `:569+`), pass the producing provider:

```python
        await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md=output_md,
            tokens_input=tin,
            tokens_output=tout,
            validation_warnings=warnings or None,
            provider=produced_by,
        )
```

The existing `except Exception` block (`:559-567`) stays as the **final** failure handler — it now only fires when `_run_with_failover` has exhausted every provider, marking the phase `failed` and re-raising (recoverable later by resume).

- [ ] **Step 5: Run tests + full suite**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failover_driver.py tests/ -q`
Expected: driver tests PASS; full suite green (1 known pre-existing red OK). Also `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline"` imports cleanly.

- [ ] **Step 6: Commit**

```bash
git add app/services/pipeline.py tests/services/test_failover_driver.py
git commit -m "feat(pipeline): per-phase provider failover (classify → retry/switch) + attribution"
```

---

## Task 7: Resume — skip `done` phases on recovery re-run

**Files:**
- Modify: `app/services/pipeline.py` (`run` head `:85-152`; scheduler `pending` seed `:351`)
- Test: `tests/services/test_resume_seed.py` (new)

> Resume is **always on** and needs no flag: a reclaimed job keeps its `done` phase rows; a fresh/forced job is a brand-new `homework_jobs` row (`force=True` skips the find-active idempotency and creates a fresh job — the logic is `jobs.py:134` `if not body.force:`) with no rows, so nothing is skipped. The carrier is the presence of `done` rows with `output_md`.
>
> **Behavior note — RATIFIED (review-flagged, verified, accepted 2026-06-03):** the manual `POST /jobs/{id}/retry` endpoint (`api/v1/jobs.py:186`) calls `reset_for_retry` (`jobs.py:141`), which flips the job to `pending` but **deliberately leaves `done` phase rows intact** (its docstring: "no phase-output cleanup is needed here"). Pre-resume, a retry regenerated **all** phases; with always-on resume it now **skips `done` phases and re-runs only the failed/unfinished ones** — this is the **intended** behavior. Rationale: `/retry` only acts on `failed` jobs, so resuming finishes what's left instead of wasting weekly-allocation budget redoing good phases. A full clean regenerate from scratch is **already available** via `POST /generate` with `force=true` (creates a brand-new job, all phases fresh) — so `/retry` (cheap resume) and `force`-generate (full redo) are complementary. No code change to `reset_for_retry`.

- [ ] **Step 1: Write the failing test** (pure helpers, no DB)

```python
# tests/services/test_resume_seed.py
from types import SimpleNamespace

from app.services.pipeline import _done_phase_md, _pending_phases


def _row(name, status, md):
    return SimpleNamespace(phase_name=name, status=status, output_md=md)


def test_done_phase_md_filters_done_with_output():
    rows = [
        _row("extract", "done", "summary"),
        _row("case-based-preview", "done", "# C"),
        _row("flashcards", "failed", None),
        _row("boss-arena", "done", "   "),   # whitespace-only → not resumable
    ]
    assert _done_phase_md(rows) == {"extract": "summary", "case-based-preview": "# C"}


def test_pending_excludes_already_present():
    content = ["case-based-preview", "flashcards", "boss-arena"]
    prior = {"case-based-preview": "# C"}     # done content phase pre-injected
    assert _pending_phases(content, prior) == {"flashcards", "boss-arena"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_resume_seed.py -q`
Expected: FAIL — `cannot import name '_done_phase_md'`.

- [ ] **Step 3: Add the pure helpers**

In `app/services/pipeline.py`, near the other module-level helpers, add:

```python
def _done_phase_md(rows) -> dict[str, str]:
    """Phase rows that are `done` with non-empty markdown — the resumable set."""
    return {
        r.phase_name: r.output_md
        for r in rows
        if r.status == "done" and (r.output_md or "").strip()
    }


def _pending_phases(content_phases: list[str], prior_outputs: dict[str, str]) -> set[str]:
    """Content phases still to run: everything not already in prior_outputs
    (done phases get pre-injected, so they're excluded and serve as deps)."""
    return {p for p in content_phases if p not in prior_outputs}
```

- [ ] **Step 4: Seed resume state in `run`**

In `pipeline.run`, right after `prior_outputs: dict[str, str] = {}` (line 94) and before the head loop, load the done set:

```python
        async with SessionLocal() as session:
            _existing_rows = await phase_repo.list_for_job(session, job_id)
        _done_md = _done_phase_md(_existing_rows)
        if _done_md:
            log.info(f"[job {job_id}] resume: {len(_done_md)} done phase(s) skipped: {sorted(_done_md)}")
```

Guard the extract head loop (`:115-133`) so a done extract is skipped and its output reused:

```python
        for idx, phase_name in enumerate(head_phases):
            if phase_name in _done_md:
                if phase_name == "extract":
                    lesson_context = _done_md["extract"]
                    log.info(f"[job {job_id}] resume: reused extract ({len(lesson_context)} chars)")
                continue
            try:
                output_md, _tin, _tout, _parsed = await _execute_one_phase(
                    # …unchanged kwargs…
                )
            except Exception:
                return
            if phase_name == "extract":
                lesson_context = output_md
                # …unchanged source-map-dropped block…
```

After `content_phases = sequence[len(head_phases):]` (line 152), pre-inject done content phases into `prior_outputs`:

```python
        for _name, _md in _done_md.items():
            if _name not in head_phases:
                prior_outputs[_name] = _md
```

- [ ] **Step 5: Make the scheduler honor pre-injected phases**

In `_run_content_phases_parallel`, change the `pending` seed (`:351`) from:

```python
    pending: set[str] = set(content_phases)
```

to:

```python
    pending: set[str] = _pending_phases(content_phases, prior_outputs)
```

A done content phase is now absent from `pending` (not re-run) but present in `prior_outputs` (so `_ready` resolves dependents). `_execute_phase`'s `create_or_reset` only fires for phases actually scheduled, so done rows are never wiped.

- [ ] **Step 6: Run tests + import + full suite**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_resume_seed.py tests/ -q` and `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline"`
Expected: resume tests PASS; import clean; full suite green (1 known red OK).

- [ ] **Step 7: Commit**

```bash
git add app/services/pipeline.py tests/services/test_resume_seed.py
git commit -m "feat(pipeline): resume — skip done phases, reuse extract, seed prior_outputs"
```

---

## Task 8: Acceptance smoke + worklog

**No code.** Generation-affecting behavior is proven by real runs (CLAUDE.md gate).

- [ ] **Step 1: Suites green**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: all green except the known pre-existing `test_notion_defaults_disabled`.

- [ ] **Step 2: Resume smoke (Case B)**

Start the server, generate a section on `claude`, and **kill the worker process mid-run** (after ≥2 phases are `done`). Confirm:
- on restart, `main.lifespan` flips the orphaned `running` job → `pending` (log line), OR the periodic lease reclaim fires within `reclaim_stale_seconds`;
- the re-run **skips** the already-`done` phases (resume log: "N done phase(s) skipped"; their `completed_at` is unchanged) and only the unfinished phases get fresh rows;
- the job reaches `done` and Notion/download contain all phases.

- [ ] **Step 3: Failover smoke (Case A)**

Force one phase's primary provider to fail (e.g. temporarily set the job's `model` to a bogus value so `claude` errors, or block the CLI). Confirm:
- the phase completes on a **fallback** provider from `failover_provider_order`;
- `phase_outputs.provider` for that phase shows the fallback (not the requested provider), while the job badge stays the requested provider;
- `claude` is never used as a fallback target.

- [ ] **Step 4: Heartbeat sanity**

During a normal long run (>`reclaim_stale_seconds`), confirm the job is **not** falsely reclaimed mid-flight (no duplicate execution; `claimed_at` advances over time in the DB).

- [ ] **Step 5: Worklog**

Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`; note the heartbeat as the enabler for the short lease window, and the relaxed one-provider-per-job invariant (job badge = requested, phase rows = actual).

---

## Self-review

**Spec coverage:** resume (T7) ✓ · provider failover policy-b classify→act (T5 classifier + T6 driver, transient-exhaustion→failover via budget=2 then next provider) ✓ · faster reclaim (T4) **gated on heartbeat** (T3, sequenced first) ✓ · per-phase attribution (T2 column + T6 write) ✓ · claude excluded from fallback (T6 `_failover_chain` + test) ✓ · extract excluded from failover (T6 — only the non-extract branch uses the driver; extract retains its pinned single-provider path) ✓ · `per_attempt_timeout_seconds` bounds each attempt / opencode hang (T1 + T6 `asyncio.wait_for`) ✓ · backoff releases the slot (T6 — sleep outside `run_fn`/the semaphore) ✓ · startup orphan-reset (T4) ✓ · resume-vs-force carrier (T7 — force makes a new job, so presence of done rows is the carrier; no flag) ✓.

**Placeholder scan:** no TBD/TODO. The pipeline-flip (T6/T7) and worker (T3/T4) tasks are verified by pure-helper unit tests + `inspect.getsource` + import + the T8 real smokes (the DB-free harness + live-CLI gate, consistent with CLAUDE.md and the Effort A pattern). Migration head (`e2a5b8c4f1d9`) is verified by `alembic heads` in T2 Step 5.

**Type consistency:** `provider: str` end-to-end (model T2 ↔ `set_status(provider=)` T2 ↔ `_run_with_failover` returns `produced_by` ↔ `set_status(provider=produced_by)` T6). `_run_with_failover(requested_provider, model, run_fn)` signature matches both the test stub (T6 Step 1) and the `_execute_phase` call (T6 Step 4). `_done_phase_md(rows)->dict` and `_pending_phases(content, prior)->set` match their tests and call sites (T7). Migration chain: `e2a5b8c4f1d9 → a7c1e9d2b4f8` (T2). Settings names (`heartbeat_seconds`, `reclaim_stale_seconds`, `per_attempt_timeout_seconds`, `failover_provider_order`) are identical across T1, T3, T4, T6.
