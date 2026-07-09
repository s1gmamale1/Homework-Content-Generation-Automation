# Fleet Worker Version Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop stale fleet workers from silently claiming jobs (wishlist `fleet-worker-version-gate-1`, the Oliver incident worklog 0125): every process derives a monotonic code version from git; the head auto-stamps a raise-only deploy floor; workers below the floor claim nothing, loudly and visibly.

**Architecture:** New `app/services/code_version.py` derives `(CODE_VERSION, GIT_SHA)` at import (`git rev-list --count HEAD` — monotonic on the linear squash-merge `Nggaev-v2` — plus `git rev-parse --short HEAD`). The floor lives on the existing `budget_state` singleton (already read once per claim transaction — zero added queries). `main.lifespan` raise-only-stamps its own version at startup. `worker._claim_one` refuses to call `claim_next_job` when stale (pure-Python check — the floor is fleet-global, not per-job, so no SQL gate change). Heartbeat blob + `claimed_by` carry the vintage; the fleet page shows a STALE chip; a `PUT /workers/version-floor` escape hatch can lower/clear.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic (migration **0046**), React/TS (`web/`), pytest.

## Approach & key decisions

- **Version identity = git commit-count + short sha** (locked with user 2026-07-09). Zero maintenance — every merge advances it. Rejected: hand-bumped constant (forgetting to bump is the same human failure this feature fixes); exact-sha-equality vs head (blocks legitimately newer workers mid-rolling-pull).
- **Floor = auto-stamped, raise-only, on `budget_state`** (locked). Every process running `main.lifespan` stamps `max(floor, own_version)` — pulling ONE box fences all stale ones; a stale-head restart can't lower the floor. Known accepted edge: a head restarted with local unpushed commits raises the floor above origin until pushed — workers block LOUDLY, clears on push+pull; `PUT /workers/version-floor` is the manual lower/clear escape hatch. Rejected: new singleton table (`budget_state` is already the fleet-level row read once per claim tx at `worker.py:_claim_one` → `budget_repo.get_state`); `launch_defaults` (that row is UI launch defaults, and would add a second read per claim).
- **Enforcement = hard gate + loud + visible** (locked). Gate is a pure-Python early-return in `_claim_one` before `claim_next_job` — the floor is global, so pushing it into the SQL claim predicate buys nothing and costs testability. Throttled `logger.error` with grep token `version gate: STALE` (mirrors the `events_bus: LISTEN connection DOWN` diagnostic convention). Rejected: advisory-only (the silent-quality-leak failure mode survives).
- **Load-bearing verified facts:** `_claim_one` reads `budget_repo.get_state` inside the claim tx (`app/services/worker.py:366`); `budget_state` is a CHECK(id=1) singleton seeded by mig 0032; the heartbeat publishes `CAPABILITY_BLOB` on **every** full beat (`worker.py:656` `_drain_check_and_beat`); `_worker_id()` (`worker.py:120`) feeds both `homework_jobs.claimed_by` (String(128)) and `workers.pc_id` (String(128)), and nothing parses either; `/workers` (`app/api/v1/workers.py:12`) returns `list_with_liveness` rows which today do NOT include `capabilities`; FE `Worker` type at `web/src/lib/types.ts:383`; migration slot **0046** is free; worklog slot **0131**.
- **Unknown version is stale:** a worker that can't read git (`CODE_VERSION=None`) is blocked whenever a floor is set, with a loud startup error and the `WORKER_CODE_VERSION=<int>` env override as the non-git-deployment escape. All current fleet boxes are git clones, so this bites only on genuinely broken setups.
- **Shallow-clone hazard (gate condition, 2026-07-09):** `git rev-list --count HEAD` on a shallow clone returns the truncated depth, not the true count — a shallow-cloned worker would read as ancient and idle permanently with a misleading STALE that no pull fixes. `detect()` therefore checks `git rev-parse --is-shallow-repository`; on `true` it does NOT report the bogus count — it returns `(None, sha)` with an explicit error naming the fix (`git fetch --unshallow`). The deploy note gains a fleet pre-flight: verify each host's clone is full before the rollout restart.

## Global Constraints

- Branch `feat/fleet-worker-version-gate` cut from `origin/Nggaev-v2`, worktree `../HCGA-version-gate`, commit prefix `vgate:`.
- Migration file must be `alembic/versions/0046_worker_version_floor.py`, revision id `0046_worker_version_floor` (≤32 chars — Alembic VARCHAR(32) limit: this is exactly 26).
- Stage only the files each task lists — never `git add -A`.
- Canonical green bar: `uv run python -m pytest tests/ -q` WITHOUT `RUN_DB_INTEGRATION` (real-DB tests are extra, run against a scratch DB pinned to `127.0.0.1`, never `localhost`, and NEVER against `edu_copy`).
- No model calls anywhere in this feature — acceptance is $0.
- FE typecheck: `cd web && npx tsc -p tsconfig.app.json --noEmit`; build: `npm run build`.
- Log diagnostic token must be exactly `version gate: STALE` (grep-able, documented in worklog).
- The gate must never crash a worker: detection failures degrade to `None` + loud log, and the claim loop's existing `except Exception` safety net stays outermost.

---

### Task 1: `code_version` module (detect + is_stale)

**Files:**
- Create: `app/services/code_version.py`
- Test: `tests/services/test_code_version.py`

**Interfaces:**
- Produces: `code_version.detect(env: dict | None = None) -> tuple[int | None, str | None]`; `code_version.is_stale(version: int | None, floor: int | None) -> bool`; module globals `CODE_VERSION: int | None`, `GIT_SHA: str | None` (computed once at import). Later tasks import the module (`from app.services import code_version`) and read the globals — never re-run detection.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for app/services/code_version.py (fleet-worker-version-gate-1).

detect() is tested with subprocess mocked (deterministic) PLUS one real-git
integration test against this repo itself (git is guaranteed present in dev/CI
checkouts). is_stale() is a pure truth table.

RED-proofs:
  - is_stale: without the floor-None short-circuit, (None, None) would compare and crash.
  - is_stale: without the version-None branch, an undetectable worker would PASS the gate.
  - detect env override: without the override branch, WORKER_CODE_VERSION would be ignored.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import code_version


# ─── is_stale truth table ───────────────────────────────────────────────

def test_is_stale_no_floor_never_stale():
    assert code_version.is_stale(5, None) is False
    assert code_version.is_stale(None, None) is False


def test_is_stale_unknown_version_with_floor_is_stale():
    assert code_version.is_stale(None, 100) is True


def test_is_stale_below_floor():
    assert code_version.is_stale(99, 100) is True


def test_is_stale_at_or_above_floor():
    assert code_version.is_stale(100, 100) is False
    assert code_version.is_stale(101, 100) is False


# ─── detect() ───────────────────────────────────────────────────────────

def test_detect_env_override_wins_for_number():
    with patch.object(code_version, "_git", return_value="abc1234"):
        version, sha = code_version.detect({"WORKER_CODE_VERSION": "777"})
    assert version == 777
    assert sha == "abc1234"


def test_detect_env_override_non_integer_falls_through_to_git():
    def fake_git(*args):
        return "abc1234" if args[0] == "rev-parse" else "1234"
    with patch.object(code_version, "_git", side_effect=fake_git):
        version, sha = code_version.detect({"WORKER_CODE_VERSION": "not-a-number"})
    assert version == 1234
    assert sha == "abc1234"


def test_detect_git_unavailable_returns_none_pair():
    with patch.object(code_version, "_git", return_value=None):
        version, sha = code_version.detect({})
    assert version is None
    assert sha is None


def test_detect_shallow_clone_refuses_bogus_count():
    """A shallow clone's rev-list count is the truncated depth, not the true
    count — detect() must return None (fail-closed, loud) instead of reporting
    an ancient-looking version that no pull would ever fix.

    RED-proof: without the is-shallow check, this returns (1, sha)."""
    def fake_git(*args):
        if args[0] == "rev-parse" and args[1] == "--short":
            return "abc1234"
        if args == ("rev-parse", "--is-shallow-repository"):
            return "true"
        if args[0] == "rev-list":
            return "1"  # the bogus truncated depth
        return None
    with patch.object(code_version, "_git", side_effect=fake_git):
        version, sha = code_version.detect({})
    assert version is None
    assert sha == "abc1234"


def test_detect_real_git_in_this_repo():
    """Integration: this test runs inside the repo checkout (full clone or
    linked worktree), so real git must yield a positive count and a hex short
    sha. REQUIRES A FULL CLONE — a shallow CI checkout would (correctly)
    yield version=None and fail this test's environment assumption."""
    version, sha = code_version.detect({})
    assert isinstance(version, int) and version > 100
    assert isinstance(sha, str) and 6 <= len(sha) <= 12
    int(sha, 16)  # raises if not hex


def test_module_globals_computed_at_import():
    assert code_version.CODE_VERSION is None or isinstance(code_version.CODE_VERSION, int)
    # In this repo checkout they must actually be populated:
    assert code_version.CODE_VERSION is not None
    assert code_version.GIT_SHA is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_code_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.code_version'`

- [ ] **Step 3: Write the implementation**

```python
"""Worker code-vintage detection (fleet-worker-version-gate-1, worklog 0131).

Every process derives a monotonic code version from git at import time:
`git rev-list --count HEAD` on the linear squash-merge branch is an orderable
integer; `git rev-parse --short HEAD` names the exact vintage. The claim gate
compares CODE_VERSION against the fleet floor (budget_state.min_worker_version);
the heartbeat publishes both values; claimed_by carries the sha.

Env override: WORKER_CODE_VERSION=<int> wins over git for the NUMBER (escape
hatch for non-git deployments); the sha still comes from git when available.
Detection failure is LOUD (logger.error) and yields None — a versionless
worker is blocked whenever a floor is set (fail-closed, never fail-silent).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

# Project root: app/services/code_version.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> Optional[str]:
    """Run one git command against the repo root; None on any failure."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


def detect(env: Optional[dict] = None) -> tuple[Optional[int], Optional[str]]:
    """Return (code_version, git_sha). WORKER_CODE_VERSION env wins for the number."""
    environ = os.environ if env is None else env
    sha = _git("rev-parse", "--short", "HEAD")
    override = environ.get("WORKER_CODE_VERSION")
    if override:
        try:
            return int(override), sha
        except ValueError:
            logger.error(
                f"WORKER_CODE_VERSION={override!r} is not an integer — ignoring override"
            )
    # Shallow-clone guard (gate condition): a shallow clone's rev-list count
    # is the truncated fetch depth, not the true commit count — reporting it
    # would make this box look ancient and idle it permanently with a STALE
    # that no pull fixes. Refuse the bogus number, name the actual fix.
    if _git("rev-parse", "--is-shallow-repository") == "true":
        logger.error(
            "code_version: this checkout is a SHALLOW clone — rev-list count "
            "would be the truncated depth, not the real version. Run "
            "`git fetch --unshallow` (or set WORKER_CODE_VERSION=<int>). "
            "Until then this process is BLOCKED from claiming whenever a "
            "version floor is set"
        )
        return None, sha
    count = _git("rev-list", "--count", "HEAD")
    if count is None:
        logger.error(
            "code_version: cannot detect code version (git unavailable or not a "
            "checkout) — this process will be BLOCKED from claiming whenever a "
            "version floor is set; set WORKER_CODE_VERSION=<int> to override"
        )
        return None, sha
    return int(count), sha


def is_stale(version: Optional[int], floor: Optional[int]) -> bool:
    """True when the claim gate must refuse: a floor exists and this worker is
    below it — or cannot prove its version at all (fail-closed)."""
    if floor is None:
        return False
    if version is None:
        return True
    return version < floor


# Computed once at import. Consumers read the globals; tests call detect().
CODE_VERSION, GIT_SHA = detect()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_code_version.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/code_version.py tests/services/test_code_version.py
git commit -m "vgate: code_version module — git commit-count + sha detection, is_stale gate predicate"
```

---

### Task 2: Migration 0046 + budget_state floor columns + repo write paths

**Files:**
- Create: `alembic/versions/0046_worker_version_floor.py`
- Modify: `app/models/budget_state.py`
- Modify: `app/repositories/budget.py`
- Test: `tests/services/test_version_floor_repo.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `BudgetState.min_worker_version: int | None`, `.min_worker_version_stamped_by: str | None`, `.min_worker_version_stamped_at: datetime | None`; `budget_repo.raise_version_floor(session, *, version: int, stamped_by: str) -> bool` (raise-only; True iff the row changed); `budget_repo.set_version_floor(session, *, version: int | None, stamped_by: str) -> None` (unconditional set/clear — operator escape hatch). Callers commit; the repo functions do not.

- [ ] **Step 1: Write the failing tests**

Real-DB tests (the raise-only WHERE clause is a SQL predicate — per the repo's vacuous-test lesson it must be proven against real Postgres, marked so the canonical suite stays DB-free):

```python
"""Real-DB tests for the version-floor write paths (fleet-worker-version-gate-1).

The raise-only predicate is SQL — mocks can't prove it BITES. Runs only with
RUN_DB_INTEGRATION=1 against a scratch DB (127.0.0.1, never edu_copy).
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_DB_INTEGRATION"),
    reason="needs RUN_DB_INTEGRATION=1 + scratch DATABASE_URL",
)


@pytest.mark.asyncio
async def test_raise_version_floor_sets_from_null_then_raises_then_refuses_lower():
    from app.db import SessionLocal
    from app.repositories import budget as budget_repo

    async with SessionLocal() as session:
        # normalize: clear any floor left by other tests
        await budget_repo.set_version_floor(session, version=None, stamped_by="test")
        await session.commit()

    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=100, stamped_by="t1") is True
        await session.commit()

    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version == 100
        assert state.min_worker_version_stamped_by == "t1"
        assert state.min_worker_version_stamped_at is not None

    # RAISE: 100 -> 150 succeeds
    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=150, stamped_by="t2") is True
        await session.commit()

    # LOWER attempt: 150 -> 120 must be a no-op (RED-proof: without the
    # WHERE min<version guard this would overwrite and the assert fails)
    async with SessionLocal() as session:
        assert await budget_repo.raise_version_floor(session, version=120, stamped_by="t3") is False
        await session.commit()

    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version == 150
        assert state.min_worker_version_stamped_by == "t2"

    # ESCAPE HATCH: set_version_floor CAN lower, and CAN clear
    async with SessionLocal() as session:
        await budget_repo.set_version_floor(session, version=90, stamped_by="operator")
        await session.commit()
    async with SessionLocal() as session:
        assert (await budget_repo.get_state(session)).min_worker_version == 90

    async with SessionLocal() as session:
        await budget_repo.set_version_floor(session, version=None, stamped_by="operator")
        await session.commit()
    async with SessionLocal() as session:
        state = await budget_repo.get_state(session)
        assert state.min_worker_version is None
        assert state.min_worker_version_stamped_by == "operator"
```

- [ ] **Step 2: Run to verify current state**

Run: `uv run python -m pytest tests/services/test_version_floor_repo.py -v` (without the env flag)
Expected: 1 skipped (guard works). Then with a scratch DB:
`createdb -h 127.0.0.1 -O edu edu_scratch_vgate` (superuser), then
`RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_vgate uv run python -m pytest tests/services/test_version_floor_repo.py -v`
Expected: FAIL with `AttributeError: module 'app.repositories.budget' has no attribute 'set_version_floor'` (after `uv run alembic upgrade head` fails too until the migration exists — write migration first, then re-run).

- [ ] **Step 3: Write the migration**

```python
"""budget_state: fleet worker version floor (fleet-worker-version-gate-1).

Revision ID: 0046_worker_version_floor
Revises: 0045_notion_archived_job
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_worker_version_floor"
down_revision = "0045_notion_archived_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_state", sa.Column("min_worker_version", sa.Integer(), nullable=True))
    op.add_column("budget_state", sa.Column("min_worker_version_stamped_by", sa.String(128), nullable=True))
    op.add_column(
        "budget_state",
        sa.Column("min_worker_version_stamped_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("budget_state", "min_worker_version_stamped_at")
    op.drop_column("budget_state", "min_worker_version_stamped_by")
    op.drop_column("budget_state", "min_worker_version")
```

NOTE: verify `down_revision` matches the actual revision id inside `alembic/versions/0045_notion_archived_job.py` (read the file; use its literal `revision` string).

- [ ] **Step 4: Extend the model**

Append to `BudgetState` in `app/models/budget_state.py` (below `api_paused_reason`):

```python
    # Fleet worker version floor (fleet-worker-version-gate-1, mig 0046):
    # workers whose code_version is below this claim NOTHING. NULL = gate off.
    # Auto-stamped raise-only by main.lifespan; PUT /workers/version-floor is
    # the operator escape hatch (can lower/clear).
    min_worker_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_worker_version_stamped_by: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    min_worker_version_stamped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

Add `DateTime` to the existing `from sqlalchemy import ...` line.

- [ ] **Step 5: Add the repo functions**

Append to `app/repositories/budget.py` (it imports `func, update` from sqlalchemy today — add `or_` there, and `from typing import Optional` if absent):

```python
async def raise_version_floor(
    session: AsyncSession, *, version: int, stamped_by: str
) -> bool:
    """Raise-only floor stamp (the main.lifespan auto-stamp). The WHERE guard
    makes a stale-process restart a no-op — the floor can never go DOWN through
    this path. Returns True iff the floor actually moved. Caller commits."""
    result = await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .where(
            or_(
                BudgetState.min_worker_version.is_(None),
                BudgetState.min_worker_version < version,
            )
        )
        .values(
            min_worker_version=version,
            min_worker_version_stamped_by=stamped_by,
            min_worker_version_stamped_at=func.now(),
        )
    )
    return (result.rowcount or 0) > 0


async def set_version_floor(
    session: AsyncSession, *, version: Optional[int], stamped_by: str
) -> None:
    """Unconditional floor set/clear — the OPERATOR escape hatch (unlike the
    lifespan auto-stamp, this may LOWER or clear). Caller commits."""
    await session.execute(
        update(BudgetState)
        .where(BudgetState.id == 1)
        .values(
            min_worker_version=version,
            min_worker_version_stamped_by=stamped_by,
            min_worker_version_stamped_at=func.now(),
        )
    )
```

- [ ] **Step 6: Apply migration to the scratch DB + run tests**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_vgate uv run alembic upgrade head` — Expected: runs through 0046.
Then re-run the Step-2 pytest command. Expected: 1 passed.
Then the canonical bar: `uv run python -m pytest tests/ -q` — Expected: green (new file skips without the flag).

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0046_worker_version_floor.py app/models/budget_state.py app/repositories/budget.py tests/services/test_version_floor_repo.py
git commit -m "vgate: mig 0046 — budget_state version floor columns + raise-only/set repo writes"
```

---

### Task 3: Hard claim gate in `worker._claim_one` (loud, throttled)

**Files:**
- Modify: `app/services/worker.py` (imports; `Worker.__init__`; `_claim_one`; new `_log_stale_gate`; startup log line in `start()`)
- Test: `tests/services/test_worker_version_gate.py`

**Interfaces:**
- Consumes: `code_version.CODE_VERSION` / `GIT_SHA` / `is_stale` (Task 1); `budget_state.min_worker_version` (Task 2).
- Produces: nothing new for later tasks; the gate itself.

- [ ] **Step 1: Write the failing tests**

Mirror the `test_worker_cooldown.py` pattern (instantiate `Worker`, mock `claim_next_job` + `budget_repo.get_state`, assert call/no-call):

```python
"""Unit tests for the worker version claim gate (fleet-worker-version-gate-1).

RED-proofs:
  - If _claim_one never consults is_stale, a stale worker still calls
    claim_next_job — the no-call assertion fails.
  - If the gate compared with <= instead of <, an at-floor worker would be
    blocked — the at-floor test fails.
  - If unknown version (None) passed the gate, the fail-closed test fails.
  - Throttle: second immediate blocked poll must NOT emit a second ERROR.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import code_version
from app.services.worker import Worker


def _mock_state(floor):
    state = MagicMock()
    state.api_paused_at = None
    state.min_worker_version = floor
    return state


def _run_claim(worker, *, floor, version):
    """Drive one _claim_one with a mocked session/budget/claim layer.
    Returns the claim_next_job mock."""
    import asyncio

    claim_mock = AsyncMock(return_value=None)

    # Patch SessionLocal with an async-context-manager double whose
    # session.begin() is also an async CM.
    class _AsyncCM:
        def __init__(self, value=None):
            self._value = value
        async def __aenter__(self):
            return self._value
        async def __aexit__(self, *exc):
            return False

    session = MagicMock()
    session.begin = MagicMock(return_value=_AsyncCM())

    with patch("app.services.worker.SessionLocal", MagicMock(return_value=_AsyncCM(session))), \
         patch("app.services.worker.budget_repo.get_state", AsyncMock(return_value=_mock_state(floor))), \
         patch("app.services.worker.jobs_repo.claim_next_job", claim_mock), \
         patch.object(code_version, "CODE_VERSION", version):
        asyncio.run(worker._claim_one())
    return claim_mock


def test_stale_worker_never_calls_claim():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=100)
    claim.assert_not_called()


def test_unknown_version_with_floor_is_blocked():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=None)
    claim.assert_not_called()


def test_at_floor_worker_claims():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=200)
    claim.assert_called_once()


def test_no_floor_claims():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=None, version=None)
    claim.assert_called_once()


def test_stale_log_is_throttled(caplog=None):
    """First blocked poll logs ERROR; an immediate second poll does not."""
    w = Worker(concurrency=1)
    emitted = []
    with patch("app.services.worker.logger") as mock_log:
        mock_log.error = MagicMock(side_effect=lambda *a, **k: emitted.append(a))
        _run_claim(w, floor=200, version=100)
        _run_claim(w, floor=200, version=100)
    assert len(emitted) == 1
    assert "version gate: STALE" in emitted[0][0]
```

NOTE to implementer: the exact mocking shape may need adjusting to match how `_claim_one` uses `async with SessionLocal() as session: async with session.begin():` — copy the working double from `tests/services/test_worker_cooldown.py` / `test_worker_guards.py` if one exists there; the ASSERTIONS above are the contract.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_worker_version_gate.py -v`
Expected: FAIL — `test_stale_worker_never_calls_claim` fails with `claim_next_job` called (gate absent).

- [ ] **Step 3: Implement the gate**

In `app/services/worker.py`:

(a) Import at top with the other service imports: `from app.services import code_version`.

(b) In `Worker.__init__`, add: `self._stale_gate_logged_at: float | None = None`.

(c) Module constant near the top: `_STALE_LOG_INTERVAL_SECONDS = 300.0`.

(d) New method on `Worker`:

```python
    def _log_stale_gate(self, floor: int | None) -> None:
        """Throttled ERROR for the version gate — loud on first block, then at
        most every _STALE_LOG_INTERVAL_SECONDS (the poll loop runs every few
        seconds; unthrottled this would flood the log). Grep token:
        'version gate: STALE'."""
        import time

        now = time.monotonic()
        if (
            self._stale_gate_logged_at is not None
            and now - self._stale_gate_logged_at < _STALE_LOG_INTERVAL_SECONDS
        ):
            return
        self._stale_gate_logged_at = now
        logger.error(
            f"worker {self.id} version gate: STALE worker — "
            f"code_version={code_version.CODE_VERSION} < floor={floor} "
            f"(sha={code_version.GIT_SHA}); claiming NOTHING until this box "
            f"pulls + restarts"
        )
```

(e) In `_claim_one`, right after `budget_state = await budget_repo.get_state(session)` / `fleet_api_paused = ...` (inside the same `session.begin()` block), insert:

```python
                    # Version gate (fleet-worker-version-gate-1): a worker below
                    # the fleet deploy floor claims NOTHING. Fleet-global, so a
                    # pure-Python check here beats a SQL predicate. Fail-closed:
                    # unknown version + floor set -> blocked.
                    floor = budget_state.min_worker_version
                    if code_version.is_stale(code_version.CODE_VERSION, floor):
                        self._log_stale_gate(floor)
                        return None
```

(f) In the worker startup log (`worker.py:214` region, the `worker {self.id} starting | ...` line), append `code_version={code_version.CODE_VERSION} sha={code_version.GIT_SHA}` to the message.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/services/test_worker_version_gate.py tests/services/test_worker_cooldown.py tests/services/test_worker_guards.py -v`
Expected: all pass (cooldown/guards prove no regression in `_claim_one`).

- [ ] **Step 5: Commit**

```bash
git add app/services/worker.py tests/services/test_worker_version_gate.py
git commit -m "vgate: hard claim gate — stale worker claims nothing, throttled 'version gate: STALE' error"
```

---

### Task 4: Lifespan auto-stamp (raise-only) in `main.py`

**Files:**
- Modify: `main.py` (lifespan, after the orphan sweep commit, before `events_bus.start_listener()`)
- Test: `tests/services/test_version_floor_stamp.py`

**Interfaces:**
- Consumes: `budget_repo.raise_version_floor` (Task 2), `code_version` globals (Task 1).

- [ ] **Step 1: Write the failing test**

Source-inspection test (same pattern the events_bus lifespan test uses — lifespan is not unit-invokable without a full app boot):

```python
"""Lifespan version-floor auto-stamp wiring (fleet-worker-version-gate-1).

main.lifespan cannot run without a live DB, so wiring is proven by source
inspection (the established pattern from the events_bus lifespan test), and
the stamp helper's semantics are already real-DB-proven in
tests/services/test_version_floor_repo.py.
"""
from __future__ import annotations

import inspect


def test_lifespan_stamps_version_floor():
    import main

    src = inspect.getsource(main.lifespan)
    assert "raise_version_floor" in src
    # stamped before the SSE listener starts (both are startup-critical order)
    assert src.index("raise_version_floor") < src.index("start_listener")
    # guarded: an undetectable version must NOT stamp
    assert "CODE_VERSION is not None" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_version_floor_stamp.py -v`
Expected: FAIL with `AssertionError` (`raise_version_floor` not in source).

- [ ] **Step 3: Implement**

In `main.py`, add imports: `from app.repositories import budget as budget_repo` and `from app.services import code_version` (alongside existing repo/service imports). Then, after the `log.info("Orphan sweep complete (books + phase_outputs)")` line and BEFORE the events_bus block:

```python
    # Fleet version floor auto-stamp (fleet-worker-version-gate-1): raise-only —
    # any process starting on newer code fences out every stale worker; a
    # stale-process restart is a no-op. PUT /workers/version-floor is the
    # operator escape hatch (lower/clear).
    if code_version.CODE_VERSION is not None:
        async with SessionLocal() as session:
            raised = await budget_repo.raise_version_floor(
                session,
                version=code_version.CODE_VERSION,
                stamped_by=f"{socket.gethostname()}@{code_version.GIT_SHA or 'unknown'}",
            )
            await session.commit()
        if raised:
            log.info(
                f"Startup: version floor raised to {code_version.CODE_VERSION} "
                f"(sha={code_version.GIT_SHA})"
            )
        else:
            log.info(
                f"Startup: version floor unchanged (own version "
                f"{code_version.CODE_VERSION} <= current floor)"
            )
    else:
        log.warning(
            "Startup: code version undetectable — version floor NOT stamped; "
            "this process is BLOCKED from claiming if a floor is set"
        )
```

Add `import socket` to main.py's stdlib imports.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/services/test_version_floor_stamp.py -v && uv run python -c "import main"`
Expected: 1 passed; import clean.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/services/test_version_floor_stamp.py
git commit -m "vgate: lifespan auto-stamps raise-only version floor at startup"
```

---

### Task 5: Vintage visibility — heartbeat blob, claimed_by sha, /workers API + escape hatch

**Files:**
- Modify: `app/services/worker.py` (`_capability_blob`, `_worker_id`)
- Modify: `app/repositories/workers.py` (`list_with_liveness` row dict)
- Modify: `app/api/v1/workers.py` (`list_workers` + new `PUT /workers/version-floor`)
- Test: `tests/services/test_worker_version_identity.py`
- Test (extend): `tests/services/test_workers_liveness.py` (only if it asserts the row-dict keys — add `capabilities`)

**Interfaces:**
- Consumes: `code_version` globals (Task 1), `budget_repo.get_state`/`set_version_floor` (Task 2).
- Produces: `GET /workers` response gains top-level `"version_floor": int | None` and each worker row gains `"capabilities": dict | None` (blob now contains `"code_version"` and `"git_sha"`); `PUT /workers/version-floor` with body `{"value": int | null}` → `{"version_floor": value}`. `_worker_id()` returns `hostname:pid@sha` when sha is known (else the old `hostname:pid`).

- [ ] **Step 1: Write the failing tests**

```python
"""Worker vintage identity + capability blob (fleet-worker-version-gate-1).

RED-proofs:
  - blob without code_version/git_sha keys -> FE has nothing to render.
  - _worker_id without the @sha suffix -> claimed_by attribution stays blind
    (the exact gap worklog 0125 recorded).
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import code_version
from app.services import worker as worker_mod


def test_capability_blob_carries_version_and_sha():
    with patch.object(code_version, "CODE_VERSION", 1234), \
         patch.object(code_version, "GIT_SHA", "abc1234"):
        blob = worker_mod._capability_blob({})
    assert blob["code_version"] == 1234
    assert blob["git_sha"] == "abc1234"
    assert "cli" in blob and "api" in blob  # existing shape untouched


def test_worker_id_carries_sha_suffix():
    with patch.object(code_version, "GIT_SHA", "abc1234"):
        wid = worker_mod._worker_id()
    assert wid.endswith("@abc1234")
    assert ":" in wid  # hostname:pid core intact


def test_worker_id_without_sha_falls_back_to_bare():
    with patch.object(code_version, "GIT_SHA", None):
        wid = worker_mod._worker_id()
    assert "@" not in wid
    assert len(wid) <= 128  # fits claimed_by/pc_id String(128)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_worker_version_identity.py -v`
Expected: FAIL (`KeyError: 'code_version'`, missing `@` suffix).

- [ ] **Step 3: Implement**

(a) `worker._capability_blob` — add to the returned dict:

```python
    return {
        "cli": {name: agent.provider_cli_installed(name) for name in providers.PROVIDERS},
        "api": {"claude": api["claude"], "gemini": api["gemini"]},
        # Code vintage (fleet-worker-version-gate-1): read at call time (not
        # captured at def time) so tests can patch the module globals.
        "code_version": code_version.CODE_VERSION,
        "git_sha": code_version.GIT_SHA,
    }
```

(b) `worker._worker_id`:

```python
def _worker_id() -> str:
    """Stable identity for `claimed_by` + workers.pc_id. hostname:pid attributes
    a job to a process; the @sha suffix (fleet-worker-version-gate-1) attributes
    it to a code vintage — the post-hoc answer worklog 0125 lacked. Fits
    String(128): hostname<=63 + pid + 8-char sha."""
    base = f"{socket.gethostname()}:{os.getpid()}"
    sha = code_version.GIT_SHA
    return f"{base}@{sha}" if sha else base
```

VERIFY before committing: `grep -rn "claimed_by\|pc_id" app/ web/src/ --include="*.py" --include="*.ts" --include="*.tsx" | grep -v test` and confirm nothing splits/parses the string (exploration says nothing does — re-confirm, it's load-bearing). Check `aggregate_fleet_capability` in `app/repositories/workers.py` tolerates the two extra blob keys (it reads `cli`/`api` sub-dicts).

(c) `workers_repo.list_with_liveness` — add to the row dict: `"capabilities": w.capabilities,`.

(d) `app/api/v1/workers.py`:

```python
from typing import Optional

from pydantic import BaseModel, Field

from app.repositories import budget as budget_repo
```

In `list_workers`, before the return: `state = await budget_repo.get_state(session)`; add `"version_floor": state.min_worker_version,` to the response dict.

New endpoint:

```python
class VersionFloorIn(BaseModel):
    value: Optional[int] = Field(default=None, ge=0)


@router.put("/workers/version-floor")
async def put_version_floor(
    body: VersionFloorIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Operator escape hatch: set (may LOWER) or clear (value=null) the fleet
    version floor. The lifespan auto-stamp is raise-only; this is the way back
    down when a head accidentally stamps a floor above origin."""
    await budget_repo.set_version_floor(
        session, version=body.value, stamped_by="operator"
    )
    await session.commit()
    return {"version_floor": body.value}
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/services/test_worker_version_identity.py tests/services/test_workers_liveness.py tests/services/test_worker_capabilities.py -v`
Expected: all pass (fix any liveness/capabilities fixtures that assert exact dict shapes).

- [ ] **Step 5: Commit**

```bash
git add app/services/worker.py app/repositories/workers.py app/api/v1/workers.py tests/services/test_worker_version_identity.py tests/services/test_workers_liveness.py
git commit -m "vgate: vintage visibility — heartbeat blob version/sha, claimed_by @sha, /workers floor + escape hatch"
```

(Drop `tests/services/test_workers_liveness.py` from the add list if it needed no change.)

---

### Task 6: FE — version chip + STALE state + floor display/clear

**Files:**
- Modify: `web/src/lib/types.ts` (`Worker` interface + workers response type)
- Modify: `web/src/lib/api.ts` (workers response type; new `setVersionFloor`)
- Modify: `web/src/components/fleet/worker-cards.tsx`

**Interfaces:**
- Consumes: Task 5's API shape — worker rows carry `capabilities.code_version` / `capabilities.git_sha`; response carries `version_floor`; `PUT /api/v1/workers/version-floor` body `{"value": number | null}`.

- [ ] **Step 1: Extend types**

In `web/src/lib/types.ts`, extend `Worker` (line ~383):

```typescript
export interface WorkerCapabilities {
  cli?: Record<string, boolean>;
  api?: Record<string, boolean>;
  code_version?: number | null;
  git_sha?: string | null;
}

export interface Worker {
  pc_id: string;
  last_heartbeat: string | null;
  status: string;
  notes: string | null;
  online: boolean;
  capabilities?: WorkerCapabilities | null;
}
```

Find the workers-list response type (grep `workers` in `types.ts` / `api.ts` — it may be inlined as `{ workers: Worker[]; online: number; total: number; ... }`) and add `version_floor: number | null;` to it, mirroring the API exactly.

- [ ] **Step 2: Extend api client**

In `web/src/lib/api.ts`, next to `drainWorker`/`undrainWorker` (match their exact fetch style):

```typescript
  setVersionFloor: (value: number | null) =>
    request<{ version_floor: number | null }>(`/workers/version-floor`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
```

(Adapt to the file's actual helper — copy the shape of the adjacent mutation calls verbatim.)

- [ ] **Step 3: Render vintage + STALE chip + floor**

In `web/src/components/fleet/worker-cards.tsx`:

(a) The component receives `data` — extend its prop type with `version_floor?: number | null` (threading from the parent that fetches `/workers`; find the parent via grep `WorkerCards` and pass the field through).

(b) In the header row (next to `online {n} / {n}`), when `data?.version_floor != null` render:

```tsx
<span className="font-mono text-[0.72rem] text-white/45">
  floor v{data.version_floor}
</span>
```

(c) Per worker card, compute and render:

```tsx
const ver = w.capabilities?.code_version ?? null;
const sha = w.capabilities?.git_sha ?? null;
const floor = data?.version_floor ?? null;
const isStale = floor != null && (ver == null || ver < floor);
```

Inside the card, after the status dot / pc_id block:

```tsx
{(ver != null || sha) && (
  <span className="font-mono text-[0.68rem] text-white/40">
    {ver != null ? `v${ver}` : "v?"}{sha ? ` @${sha}` : ""}
  </span>
)}
{isStale && (
  <span className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold text-red-300">
    STALE{ver != null && floor != null ? ` ${ver} < ${floor}` : ""}
  </span>
)}
```

Match the file's existing chip/badge styling idiom (see the draining amber styling already in the file) rather than inventing a new one.

- [ ] **Step 4: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/components/fleet/worker-cards.tsx
git commit -m "vgate: FE — worker vintage v{n} @sha, STALE chip vs floor, floor display"
```

(If the parent route needed a change to thread `version_floor`, include that exact file too.)

---

### Task 7: Acceptance (live two-config proof, $0) + docs + finish bookkeeping

**Files:**
- Modify: `docs/HOW_IT_WORKS.md` (Queue + worker section), `docs/CODE_MAP.md`, `docs/DATABASE.md` (budget_state columns)
- Modify: `docs/memory/MASTER_MEMORY.md` (worklog **0131**), `docs/memory/INDEX.md` (0131 row), `docs/memory/WISHLIST.md` (close `fleet-worker-version-gate-1`)
- Move: `git mv docs/superpowers/plans/2026-07-09-fleet-worker-version-gate.md docs/superpowers/plans/shipped/`

**Steps (controller-run, not subagent):**

- [ ] **Step 1: Full suite + FE gates**

```bash
uv run python -m pytest tests/ -q
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```
Expected: suite green (~1510+ tests, 0 failures — the 2 known pre-existing failover reds were fixed by 0124; nothing new red), tsc/build clean.

- [ ] **Step 2: Live acceptance on scratch DB (the RED→GREEN gate proof, $0 — no model calls)**

Against `edu_scratch_vgate` (migrated in Task 2):

1. Seed: one `pending` cli-transport job (INSERT via psql or a tiny script; provider `gemini`, `transport='cli'` so no api capability is needed) + set floor: `curl -X PUT .../api/v1/workers/version-floor -d '{"value": 999999}'` (or direct SQL `UPDATE budget_state SET min_worker_version=999999`).
2. STALE leg: `WORKER_CODE_VERSION=100 DATABASE_URL=<scratch> uv run uvicorn main:app --port 8010` — wait 2 poll cycles. Assert: job still `pending`, log contains `version gate: STALE` exactly once (throttle), heartbeat row's blob has `code_version: 100`, floor NOT lowered (raise-only proven live: startup stamped nothing).
3. CURRENT leg: restart with `WORKER_CODE_VERSION=1000000`. Assert: startup log `version floor raised to 1000000`; job gets claimed (`claimed_by` ends with `@<sha>`); kill the process promptly (the job may fail on a missing book — irrelevant, claiming is the proof; scratch DB).
4. Escape hatch: `PUT /workers/version-floor {"value": null}` → `GET /workers` shows `version_floor: null`.

Record every command + observed output in the worklog entry.

- [ ] **Step 3: Rebase check** — `git fetch origin && git log HEAD..origin/Nggaev-v2 --oneline`; if the base moved, rebase onto `origin/Nggaev-v2` and re-run the full suite.

- [ ] **Step 4: Docs de-stale + worklog 0131 + INDEX row + WISHLIST close + `git mv` plan to `shipped/`.** Worklog must include the DEPLOY NOTE: *the gate only bites once ONE box (normally the head) runs post-merge code and stamps the floor; the currently-owed fleet pull (#84+#88+#90+#91) plus this change is the rollout — after it, any box that misses a future pull idles loudly instead of silently serving stale output. **Fleet pre-flight before the rollout restart:** on each host verify the clone is FULL — `git rev-parse --is-shallow-repository` must print `false`; if `true`, run `git fetch --unshallow` first (a shallow clone reads as versionless and idles once a floor exists). Diagnostic: grep worker log for `version gate: STALE`; fleet page shows the red STALE chip. Escape hatch: `PUT /api/v1/workers/version-floor {"value": null}`.*

- [ ] **Step 5: Commit finish bookkeeping; verify the finish commit CONTENTS with `git show --stat` (the git-add-atomic-failure trap). Then push and open the PR to GK2 — no self-merge.**

---

## Self-review notes

- **Coverage vs wishlist:** claim-gate handshake ✅ (Tasks 1–4), sha into `claimed_by` for attribution ✅ (Task 5), loud + visible ✅ (Tasks 3, 5, 6), durable "one pull fences the fleet" ✅ (raise-only stamp from any process, Task 4).
- **Type consistency:** `raise_version_floor(session, *, version: int, stamped_by: str) -> bool` and `set_version_floor(session, *, version: int | None, stamped_by: str) -> None` used identically in Tasks 2/4/5; blob keys `code_version`/`git_sha` identical in Tasks 5/6; response key `version_floor` identical in Tasks 5/6.
- **Known risks called out to reviewers:** Task 3's mocking shape may need adaptation to the real `SessionLocal` double (assertions are the contract); Task 5 must re-verify nothing parses `claimed_by`/`pc_id`; `aggregate_fleet_capability` must tolerate the new blob keys; FE parent must thread `version_floor`.
