# Persistent Solver Mismatch Fail-Closed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a phase whose independently re-solved answer key is still wrong after regeneration from becoming `done`, completing its job, or reaching Notion/export, while retaining bounded queue retries only for genuine transient failures in the repair path.

**Architecture:** Add a typed `PersistentSolverMismatch` content-quality error and a new persisted solver outcome, `mismatch_blocked`. The solver remains advisory until it has positively identified a high-confidence mismatch; after that point the pipeline becomes fail-closed: a successful repair may ship, a transient repair failure uses the existing three-attempt queue retry, and a persistent/hard mismatch becomes a failed phase and failed job. Reuse the shipped fenced-lease, cancel-wins, abandoned-phase, retry, dashboard, and archive gates rather than creating a parallel review queue.

**Tech Stack:** Python 3.14, asyncio, FastAPI, SQLAlchemy async ORM, PostgreSQL/Alembic, Pydantic, pytest/pytest-asyncio, existing React dashboard (visibility contract only; no new screen).

## Global Constraints

- This lane changes only the independent answer-key solver's **known-high-confidence mismatch** path. It MUST NOT make `judge_status="major_shipped"` terminal; the judge's historical contract false positives remain a separate lane.
- Initial solver `unavailable` / `refused` with no proven mismatch remains today's advisory behavior. Fail-closed starts only after `SolveOutcome.has_mismatch is True`.
- `mismatch -> regeneration -> agreement` remains successful and persists `solver_status="mismatch_regen"` with the regenerated artifact.
- `mismatch -> regeneration -> mismatch` persists the final artifact for operator inspection as a **failed** phase with `solver_status="mismatch_blocked"`, raises `PersistentSolverMismatch`, and fails the job without an automatic queue retry.
- Once a mismatch is proven, a 429, attempt timeout, or transient network failure during regeneration/re-solve propagates through the existing `TransientPhaseError -> Worker._mark_failed -> jobs_repo.mark_failed_with_retry` chain. It may retry only up to `settings.queue_max_attempts` (currently `3`); exhaustion is terminal. Auth, refusal, schema exhaustion, and other hard failures are terminal.
- A known-bad or unverified-after-repair phase MUST never publish `phase_completed`; its job MUST never publish `job_completed`, call automatic `notion_archive.archive_job`, or appear in a `done`-only export.
- Failed phase rows keep the attempted `output_md`, artifact provenance, token counts, warnings, and solver error so the operator can inspect the failure. `_done_phase_md` excludes them, so a later explicit retry regenerates only that phase and preserves clean `done` siblings.
- Fenced lease loss and cancel-wins remain control signals, never content failures. Every new write carries the active claim token; a zero-row fenced write re-raises `LeaseLostSignal` / `CancelWonSignal` through existing helpers.
- Queue retry/park reconciliation is same-transaction and preserves every `done` row. A retry, session-limit pause, or slot-saturation park resets all owned pending/running rows to `pending`; terminal exhaustion marks all owned pending/running rows `failed` with the same queue error.
- Historical remediation is DB-only, dry-run by default, snapshot-hash guarded, and performs **zero Notion writes**. Existing Notion pointers/timestamps remain evidence. Repaired archived jobs are force-rearchived only after the separately shipped R26 collision repair has actually been run by the operator.
- No production database mutation, migration, fleet restart, Notion write, or billed model call occurs during implementation or deterministic tests.
- The real API acceptance smoke is separately approved and capped before execution; this plan authorizes only writing the smoke harness and recording the estimate.

## Locked Outcome Matrix

| State after solver runs | Phase outcome | Job/queue outcome | Delivery |
|---|---|---|---|
| Initial solver agrees | `done`, `ok` | continue | allowed |
| Initial solver unavailable/refused, no known mismatch | `done`, `unavailable`/`refused` | continue (unchanged) | allowed with existing amber visibility |
| Initial mismatch; regenerated output agrees | `done`, `mismatch_regen` | continue | allowed |
| Initial mismatch; regenerated output still mismatches | `failed`, `mismatch_blocked` | terminal `failed` immediately | forbidden |
| Initial mismatch; repair/recheck hits true transient | row reconciled `pending` | bounded queue retry; terminal `failed` on attempt 3 | forbidden until a later clean run |
| Initial mismatch; repair/recheck hard-fails or refuses | `failed`, `mismatch_blocked` | terminal `failed` immediately | forbidden |
| Lease lost / cancel wins at any write | no obsolete mutation / cancelled | existing lease/cancel semantics | forbidden |

## Branch-Collision Gate (2026-08-10, read-only)

- Base fetched with `git fetch --all --prune`: `origin/Nggaev-v2 = d6b1c9f65e13ea5a6c2abd21b8a592303ece784b`.
- Plan branch/worktree: `plan/persistent-solver-mismatch-fail-closed` in `/Users/macmini5/Documents/HCGA-solver-fail-closed-plan`, cut exactly from that base.
- Merged contracts to reuse, not reimplement:
  - queue retry #109, squash `7a7c26f`;
  - orphan reconciliation #110, squash `0b73628`;
  - fenced leases #121, squash `2ebab53`;
  - solver core #80, `2cc3ebf`; boss-arena extension #88, `986a28c`;
  - current language-fidelity base #126, merge `e7b0aec` plus docs tip `d6b1c9f`.
- `origin/feat/fenced-job-leases@3253bb9` and `origin/feat/extract-coverage-check@e1b71af` are pre-squash historical lines (19/18 commits ahead but 55/54 behind). Do not base, cherry-pick, or revive them; their shipped behavior is already in `Nggaev-v2`.
- Open PR #118 (`fix/content-json-gate-corrections@fb38ba0`, 58 commits behind) changes earlier judge/provenance hunks in `pipeline.py` but not the solver block. It is structurally overlapping by file and currently conflicting. **Integration order:** this blocker lane merges first from a fresh current base; #118's owner later rebases and preserves this lane. Do not edit or update #118.
- Open PR #117 is a draft retrospective and #108 changes only the dashboard row wrapper; neither implements or overlaps the solver state transition. Both are read-only to this lane.
- `plan/source-integrity-coverage@d6b1c9f` is plan-only. Its future implementation owns page mapping/extract source windows; this lane owns the solver block and queue failure reconciliation. If both implementations run concurrently, this lane merges first and the source-integrity lane rebases; they must not share a worktree.
- The primary checkout's pre-existing untracked `Wishlist.md`, `docs/superpowers/plans/2026-08-06-fenced-job-leases.md`, and `scripts/export_homeworks.py` are untouched.
- Repeat the full collision gate before implementation, after any base movement, and before opening the eventual PR.

## Verified Production Scope (read-only at plan time)

The following query was executed inside `BEGIN READ ONLY` against `edu_copy` on 2026-08-10:

```sql
SELECT count(*) AS total,
       count(*) FILTER (
         WHERE po.completed_at >= now() - interval '7 days'
       ) AS last_7_days,
       count(*) FILTER (WHERE j.status = 'done') AS done_jobs,
       count(*) FILTER (WHERE j.notion_archived_at IS NOT NULL) AS archived
FROM phase_outputs po
JOIN homework_jobs j ON j.id = po.job_id
WHERE po.solver_status = 'mismatch_shipped';
```

Result: **31 total phases / 31 distinct jobs / 5 in the last seven days / 31 on done jobs / 29 already archived**. A second grouped query confirmed there is currently exactly one affected phase per job; the script still groups phases under jobs so a future multi-phase job is handled atomically. The recent five are:

```text
0999b866-31a7-4548-b29e-17c0afd718d1  boss-arena    matematika G6
0126352a-5f21-4f53-bc56-4e0770294bba  boss-arena    matematika G5
589dfe18-6040-464e-98bd-24ca3b1295a9  memory-check  geometriya G9
9653c83d-d430-4a75-a1a2-2643fc499e41  boss-arena    geometriya G8
7465c3d6-1690-4cf2-9913-4937ef61be66  practice-rlc  geometriya G8
```

The remediation script MUST re-read live state at execution time; `31/5` is evidence, not a hard-coded safety bypass. Any new mismatch is included only after appearing in the reviewed dry-run snapshot.

---

### Task 1: Typed Quality Error and Solver Failure Provenance

**Files:**
- Modify: `app/services/errors.py`
- Modify: `app/services/solver.py`
- Modify: `tests/services/test_solver.py`
- Create: `tests/services/test_solver_quality_errors.py`

**Interfaces:**
- Produces: `PersistentSolverMismatch(phase_name: str, warnings: list[str], repair_error: BaseException | None = None)`.
- Produces: `SolveOutcome.failure: BaseException | None`; populated only when `available=False` because a real exception was degraded.
- Consumes later: pipeline uses `SolveOutcome.failure` only after an earlier call already proved a high-confidence mismatch.

- [ ] **Step 1: Write the failing typed-error tests**

```python
from app.services.errors import PersistentSolverMismatch


def test_persistent_solver_mismatch_is_nonblank_and_bounded():
    exc = PersistentSolverMismatch(
        "memory-check",
        [f"[high] q{i}: wrong key" for i in range(10)],
    )
    assert exc.phase_name == "memory-check"
    assert len(exc.warnings) == 10
    assert "persistent answer-key mismatch" in str(exc)
    assert "memory-check" in str(exc)
    assert len(str(exc)) < 1000


def test_persistent_solver_mismatch_keeps_repair_cause():
    cause = ConnectionError("solver recheck disconnected")
    exc = PersistentSolverMismatch("practice-rlc", ["[high] step 2"], cause)
    assert exc.repair_error is cause
    assert "solver recheck disconnected" in str(exc)
```

- [ ] **Step 2: Write the failing solver provenance test**

Add to `tests/services/test_solver.py`:

```python
@pytest.mark.asyncio
async def test_unavailable_outcome_retains_failure_for_post_mismatch_policy(monkeypatch):
    failure = ConnectionError("temporary resolver failure")

    async def _boom(**kw):
        raise failure

    monkeypatch.setattr("app.services.agent.run_phase", _boom)
    out = await solver.solve(**COMMON)
    assert out.available is False
    assert out.failure is failure
```

- [ ] **Step 3: Run RED tests**

Run:

```bash
pytest tests/services/test_solver.py::test_unavailable_outcome_retains_failure_for_post_mismatch_policy \
       tests/services/test_solver_quality_errors.py -q
```

Expected: collection/import failure for `PersistentSolverMismatch` and missing `SolveOutcome.failure`.

- [ ] **Step 4: Implement the typed error and provenance field**

Add to `app/services/errors.py`:

```python
class PersistentSolverMismatch(Exception):
    """A solver-confirmed answer-key defect survived the bounded regen.

    This is a hard content-quality failure, not a provider transient. The
    phase and job must fail and must never be archived/distributed.
    """

    def __init__(
        self,
        phase_name: str,
        warnings: list[str],
        repair_error: BaseException | None = None,
    ) -> None:
        self.phase_name = phase_name
        self.warnings = tuple(warnings)
        self.repair_error = repair_error
        shown = "; ".join(self.warnings[:3]) or "solver supplied no detail"
        suffix = f"; repair failed: {repair_error}" if repair_error else ""
        super().__init__(
            f"{phase_name}: persistent answer-key mismatch after regeneration: "
            f"{shown}{suffix}"
        )
```

Add `failure: BaseException | None = None` to `SolveOutcome`, and set `failure=exc` in the non-refusal unavailable result. The refusal result also keeps `failure=exc`; initial refusal behavior remains unchanged.

- [ ] **Step 5: Prove hard-vs-transient classification is explicit**

Add to `tests/services/test_solver_quality_errors.py`:

```python
from app.services import pipeline


def test_persistent_mismatch_is_not_queue_retry_worthy():
    exc = PersistentSolverMismatch("memory-check", ["[high] q1"])
    assert pipeline._requeue_worthy(exc) is False


def test_real_network_repair_failure_remains_queue_retry_worthy():
    assert pipeline._requeue_worthy(ConnectionError("connection reset")) is True
```

Do not add `PersistentSolverMismatch` to `TransientPhaseError` or the transient classifier.

- [ ] **Step 6: Run focused tests and commit**

```bash
pytest tests/services/test_solver.py tests/services/test_solver_quality_errors.py -q
git add app/services/errors.py app/services/solver.py \
        tests/services/test_solver.py tests/services/test_solver_quality_errors.py
git commit -m "fix(solver): type persistent answer-key quality failures"
```

---

### Task 2: Persist the Blocked Solver Outcome (Migration 0053)

**Files:**
- Create: `alembic/versions/0053_solver_mismatch_blocked.py`
- Create: `tests/integration/test_migration_0053_solver_blocked.py`
- Modify: `app/models/phase_output.py`
- Modify: `app/schemas/job.py`

**Interfaces:**
- Produces: persisted `solver_status="mismatch_blocked"` accepted by PostgreSQL and serialized by `PhaseOut`.
- Preserves: every migration-0043 status, including historical `mismatch_shipped` and `mismatch_regen_failed`.

- [ ] **Step 1: Write the migration contract test**

```python
import importlib


def test_0053_descends_from_current_head_and_names_blocked_status():
    m = importlib.import_module("alembic.versions.0053_solver_mismatch_blocked")
    assert m.down_revision == "0052_job_lease_fencing"
    assert "mismatch_blocked" in m._STATUS
    assert "mismatch_shipped" in m._STATUS


def test_0053_downgrade_relabels_blocked_before_shrinking_constraint():
    m = importlib.import_module("alembic.versions.0053_solver_mismatch_blocked")
    source = __import__("inspect").getsource(m.downgrade)
    assert "mismatch_blocked" in source
    assert "mismatch_shipped" in source
```

The real-Postgres leg seeds one phase row, runs the migration, proves `mismatch_blocked` inserts, runs downgrade, and proves the row becomes `mismatch_shipped` before the old CHECK is restored.

- [ ] **Step 2: Run RED**

```bash
pytest tests/integration/test_migration_0053_solver_blocked.py -q
```

Expected: missing migration module.

- [ ] **Step 3: Implement an additive CHECK-constraint migration**

```python
"""Add fail-closed solver outcome for persistent answer-key mismatches."""
from alembic import op

revision = "0053_solver_mismatch_blocked"
down_revision = "0052_job_lease_fencing"
branch_labels = None
depends_on = None

_OLD_STATUS = (
    "ok", "mismatch_regen", "mismatch_shipped", "mismatch_regen_failed",
    "unavailable", "refused",
)
_STATUS = (*_OLD_STATUS, "mismatch_blocked")


def _constraint(values: tuple[str, ...]) -> str:
    return "solver_status IS NULL OR solver_status IN " + str(values)


def upgrade() -> None:
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.create_check_constraint(
        "ck_phase_outputs_solver_status", "phase_outputs", _constraint(_STATUS)
    )


def downgrade() -> None:
    op.execute(
        "UPDATE phase_outputs SET solver_status='mismatch_shipped' "
        "WHERE solver_status='mismatch_blocked'"
    )
    op.drop_constraint("ck_phase_outputs_solver_status", "phase_outputs")
    op.create_check_constraint(
        "ck_phase_outputs_solver_status", "phase_outputs", _constraint(_OLD_STATUS)
    )
```

- [ ] **Step 4: Update source comments and serialization test**

Update the solver-status comments in `PhaseOutput` and `PhaseOut`. Extend `tests/api/test_job_serialization.py` with a failed row carrying `mismatch_blocked` and assert it round-trips unchanged.

- [ ] **Step 5: Verify on scratch PostgreSQL and commit**

```bash
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/integration/test_migration_0053_solver_blocked.py \
         tests/api/test_job_serialization.py -q
git add alembic/versions/0053_solver_mismatch_blocked.py \
        app/models/phase_output.py app/schemas/job.py \
        tests/integration/test_migration_0053_solver_blocked.py \
        tests/api/test_job_serialization.py
git commit -m "db(solver): add mismatch-blocked terminal outcome"
```

Before implementation, re-run `alembic heads`; if the head moved beyond 0052, renumber and re-anchor this migration rather than creating a fork.

---

### Task 3: Fail Closed in the Solver Regen Block

**Files:**
- Modify: `app/services/pipeline.py:1914-2023` (re-anchor after Task 2/base recheck)
- Modify: `tests/services/test_pipeline_solver.py`
- Modify: `tests/services/test_regen_slot_saturation.py`
- Create: `tests/services/test_pipeline_solver_fail_closed.py`

**Interfaces:**
- Consumes: `PersistentSolverMismatch`, `SolveOutcome.failure`, `solver_status="mismatch_blocked"`.
- Produces: `_persist_solver_blocked_phase(*, po_id, artifact, tin, tout, produced_by, warnings, judge_status, error, claim_token) -> None`, a single fenced failed-phase write that retains the final artifact. Its caller raises the typed error only after the helper commits successfully.

- [ ] **Step 1: Replace the existing permissive RED assertion**

The current `test_target_phase_mismatch_triggers_regen` permits `mismatch_shipped`. Replace it with:

```python
async def test_persistent_mismatch_fails_phase_and_never_writes_done(patch_io):
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# still-wrong regen", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _mismatch()]

    with pytest.raises(PersistentSolverMismatch):
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert len(patch_io.solve_calls) == 2
    assert not [c for c in patch_io.set_status_calls if c[0] == "done"]
    failed = [c for c in patch_io.set_status_calls if c[0] == "failed"][-1][1]
    assert failed["solver_status"] == "mismatch_blocked"
    assert failed["output_md"] == "# still-wrong regen"
    assert "persistent answer-key mismatch" in failed["error_message"]
```

- [ ] **Step 2: Add repair-failure classification tests**

```python
async def test_known_mismatch_plus_transient_regen_failure_escapes_for_queue_retry(
    monkeypatch, patch_io
):
    # First generation succeeds; the solver proves a mismatch; repair fails.
    # ConnectionError must escape unchanged so _execute_one_phase classifies it.
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_mismatch()]

    async def transient_on_regen(*, requested_provider, model, run_fn, transport, **kw):
        patch_io.failover_calls.append((requested_provider, model, transport))
        if patch_io.failover_outputs:
            return patch_io.failover_outputs.pop(0)
        raise ConnectionError("connection reset")

    monkeypatch.setattr(pipeline, "_run_with_failover", transient_on_regen)
    with pytest.raises(ConnectionError, match="connection reset"):
        await pipeline._execute_phase(**_make_kwargs("memory-check"))


async def test_known_mismatch_plus_hard_recheck_unavailable_becomes_blocked(patch_io):
    failure = RuntimeError("invalid solver verdict")
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# regenerated output", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [
        _mismatch(),
        SolveOutcome(
            available=False, agrees=True,
            warnings=["solver-unavailable: RuntimeError"],
            feedback="", has_mismatch=False, refused=False,
            failure=failure,
        ),
    ]
    with pytest.raises(PersistentSolverMismatch):
        await pipeline._execute_phase(**_make_kwargs("memory-check"))
```

- [ ] **Step 3: Add initial-unavailable non-regression tests**

Prove an initial `SolveOutcome(available=False, agrees=True, warnings=["solver-unavailable: RuntimeError"], feedback="", has_mismatch=False, refused=False, failure=RuntimeError("model down"))` still completes `done` with `solver_status="unavailable"`. Prove the same full outcome with `refused=True` completes with `refused`. This prevents the lane from silently turning every infrastructure blip into a fleet-wide stop.

- [ ] **Step 4: Run RED**

```bash
pytest tests/services/test_pipeline_solver.py \
       tests/services/test_pipeline_solver_fail_closed.py \
       tests/services/test_regen_slot_saturation.py -q
```

Expected: the persistent case writes `done/mismatch_shipped`; hard regen failure writes `done/mismatch_regen_failed`.

- [ ] **Step 5: Implement one fenced terminal phase writer**

Add this helper local to `pipeline.py`:

```python
async def _persist_solver_blocked_phase(
    *,
    po_id: UUID,
    artifact: PhaseArtifact,
    tin: Optional[int],
    tout: Optional[int],
    produced_by: str,
    warnings: list[str],
    judge_status: Optional[str],
    error: PersistentSolverMismatch,
    claim_token: Optional[UUID],
) -> None:
    async with SessionLocal() as session:
        result = await phase_repo.set_status(
            session,
            po_id,
            "failed",
            completed_at=_utcnow(),
            output_md=artifact.output_md,
            tokens_input=tin,
            tokens_output=tout,
            error_message=str(error),
            validation_warnings=(warnings or None),
            provider=produced_by,
            judge_status=judge_status,
            solver_status="mismatch_blocked",
            content_json=artifact.content_json,
            authoring_mode=artifact.authoring_mode,
            content_schema_version=artifact.content_schema_version,
            renderer_version=artifact.renderer_version,
            claim_token=claim_token,
        )
        await session.commit()
    _raise_on_lease_signal(result)
```

The caller awaits this helper and then raises the same `PersistentSolverMismatch`. Never write a `done` row first.

- [ ] **Step 6: Implement the post-mismatch decision**

After every regenerated solver call:

```python
if s_outcome.available and not s_outcome.has_mismatch:
    solver_status = "mismatch_regen"
    break
if not s_outcome.available:
    repair_error = s_outcome.failure or RuntimeError(
        "solver recheck unavailable without an exception"
    )
    if _requeue_worthy(repair_error):
        raise repair_error
    raise PersistentSolverMismatch(
        phase_name, prior_mismatch_warnings, repair_error
    )
```

If the loop exhausts with `has_mismatch=True`, build `PersistentSolverMismatch` from the **final** mismatch warnings, persist the failed phase, then raise it. In the regen exception structure:

- re-raise `LeaseLostSignal`, `CancelWonSignal`, `SessionLimitPause`, and `SlotSaturation` unchanged;
- re-raise a true transient so the worker's bounded retry acts;
- convert API auth, refusal, schema/parse exhaustion, and any other hard repair error into `PersistentSolverMismatch(repair_error=exc)`, persist the failed phase, and raise the typed error;
- include `except PersistentSolverMismatch: raise` before any broad catch so the typed terminal signal cannot be caught and rewrapped by its own regen handler.

Delete the runtime assignment to `mismatch_shipped`; keep the legacy DB value only for history/remediation.

- [ ] **Step 7: Re-prove special signals**

Run the existing saturation tests and add lease/cancel sentinel tests proving neither is converted to `PersistentSolverMismatch` and no failed content write occurs under a foreign token/cancel win.

- [ ] **Step 8: Verify and commit**

```bash
pytest tests/services/test_pipeline_solver.py \
       tests/services/test_pipeline_solver_fail_closed.py \
       tests/services/test_regen_slot_saturation.py \
       tests/services/test_pipeline_lease_signals.py -q
git add app/services/pipeline.py tests/services/test_pipeline_solver.py \
        tests/services/test_pipeline_solver_fail_closed.py \
        tests/services/test_regen_slot_saturation.py
git commit -m "fix(pipeline): block persistent solver mismatches"
```

---

### Task 4: Same-Transaction Phase Reconciliation on Queue Retry/Park/Exhaustion

**Files:**
- Modify: `app/repositories/jobs.py:1000-1100`
- Create: `tests/repositories/test_mark_failed_phase_reconcile.py`
- Modify: `tests/repositories/test_jobs_requeue_slot.py`

**Interfaces:**
- Consumes: `phase_repo.reset_abandoned_phases` from #109/#110.
- Produces: `mark_failed_with_retry`, `requeue_session_limited`, and `requeue_slot_saturated` change the job and all eligible owned phase rows in the same DB transaction.

- [ ] **Step 1: Write real-Postgres retry-state tests**

Seed a running job with one `done`, one culprit `running`, and one sibling `pending` phase under the same claim token. After `mark_failed_with_retry(session, job_id, error_message="memory-check: connection reset", max_attempts=3, claim_token=token)` on attempt 1, assert:

```python
assert job.status == "pending"
assert phases["extract"].status == "done"
assert phases["memory-check"].status == "pending"
assert phases["boss-arena"].status == "pending"
assert phases["memory-check"].error_message is None
assert phases["memory-check"].claim_token is None
```

On attempt 3, assert the job and both non-done phases are `failed` with the same nonblank error, while `extract` remains frozen.

Add equivalent success-path tests for `requeue_session_limited` and `requeue_slot_saturated`: after each job transition wins, the culprit `running` row and pending siblings are `pending`, errors/completed timestamps/claim tokens are cleared, and done rows remain frozen. Preserve the existing attempt-refund distinction (session-limit and slot saturation do not burn the attempt; generic transient retry does).

- [ ] **Step 2: Add cancel-wins and foreign-token legs**

- A `cancelling` job returns `CancelRequested`, finalizes `cancelled`, and never becomes pending/failed.
- A foreign claim token returns `LeaseLost` and changes no phase row.
- Re-run the existing two-session stale-identity-map cancel test from `test_jobs_requeue_slot.py` unchanged.

- [ ] **Step 3: Run RED**

```bash
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/repositories/test_mark_failed_phase_reconcile.py \
         tests/repositories/test_jobs_requeue_slot.py -q
```

Expected: the culprit row remains `running` on retry/exhaustion under current code.

- [ ] **Step 4: Reconcile only after the guarded job transition wins**

In both fenced and legacy paths of `mark_failed_with_retry`, and in both guarded park functions:

1. Perform the existing guarded job update first.
2. If it returns `LeaseLost`, `CancelRequested`, `cancelled`, `skipped`, or `missing`, do not run a second phase mutation.
3. On retry/park success call:

```python
await phase_repo.reset_abandoned_phases(
    session,
    [job_id],
    status="pending",
    source_statuses=("pending", "running"),
    claim_token=claim_token,
)
```

4. On terminal success call the same helper with `status="failed"` and `error_message=error_message`.
5. Keep these calls in the caller's existing transaction; do not commit inside the repository.

- [ ] **Step 5: Verify and commit**

```bash
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/repositories/test_mark_failed_phase_reconcile.py \
         tests/repositories/test_jobs_requeue_slot.py \
         tests/repositories/test_phase_outputs_abandoned.py -q
git add app/repositories/jobs.py \
        tests/repositories/test_mark_failed_phase_reconcile.py \
        tests/repositories/test_jobs_requeue_slot.py
git commit -m "fix(queue): reconcile phase rows with bounded failure retries"
```

---

### Task 5: Real-Chain No-Archive, Retry, Sibling, and Cancel Proof

**Files:**
- Create: `tests/services/test_solver_fail_closed_e2e.py`
- Modify: `tests/services/test_queue_retry_e2e.py` only if a shared seed helper is extracted without changing its assertions

**Interfaces:**
- Exercises: real `claim_next_job -> Worker._execute_job -> pipeline.run -> scheduler -> solver policy -> job/phase repositories`.
- Stubs only: provider responses, PDF read, advisory event transport, and Notion network boundary.

- [ ] **Step 1: Seed a row-owned scratch fixture**

Follow `test_queue_retry_e2e.py` exactly: unique book/TOC/job rows, `priority=1_000_000`, an older decoy, exact claimed-job identity assertions, cleanup only rows owned by the test. Seed a completed extract so the chain begins at parallel content phases.

- [ ] **Step 2: Write persistent-mismatch RED chain**

Drive a selected `memory-check` plus an in-flight sibling. Make generation return a deterministic wrong-key artifact twice and solver outcomes return mismatch twice. Assert:

```python
assert job.status == "failed"
assert "persistent answer-key mismatch" in job.error_message
assert phases["memory-check"].status == "failed"
assert phases["memory-check"].solver_status == "mismatch_blocked"
assert phases["memory-check"].output_md == STILL_WRONG_REGEN
assert phases["extract"].status == "done"
assert phases["boss-arena"].status == "failed"
events_bus.publish.assert_not_awaited_with(resource_id, "job_completed", ANY)
notion_archive.archive_job.assert_not_awaited()
```

Inspect `events_bus.publish.await_args_list` instead of using the invalid `assert_not_awaited_with` helper; the committed test must explicitly assert no event tuple has event name `job_completed` or `phase_completed` for the blocked phase.

- [ ] **Step 3: Write transient-repair bounded retry chain**

After an initial mismatch, make the regen boundary raise `ConnectionError`. First claim must end delayed `pending` with non-done phases reconciled to pending. Fast-forward to the final allowed attempt, re-claim, drive again, and assert terminal failed plus every non-done phase failed. `archive_job` remains zero calls on both passes.

- [ ] **Step 4: Write cancel-race chain**

Pause immediately before the failed-phase write, commit `status='cancelling'` from a second session, release the pause, and assert the final job is `cancelled`, not failed/pending. No job completion or archive occurs; done siblings stay done.

- [ ] **Step 5: Run RED, implement only missing glue, then GREEN**

```bash
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/services/test_solver_fail_closed_e2e.py -q
```

Task 5 should need test-only fixture work after Tasks 1-4. Any production-code change discovered here requires a fresh focused RED test and its own review before being folded into the Task 5 commit.

- [ ] **Step 6: Commit**

```bash
git add tests/services/test_solver_fail_closed_e2e.py \
        tests/services/test_queue_retry_e2e.py
git commit -m "test(solver): prove blocked keys never complete or archive"
```

---

### Task 6: Watcher, API, and Dashboard Visibility Without Showing Bad Content

**Files:**
- Modify: `tests/api/test_job_serialization.py`
- Modify: `tests/api/test_dashboard_coverage.py`
- Modify: `tests/services/test_pipeline_notion_hook.py`
- Modify: `web/src/routes/preview.tsx` (label map only)
- Modify: `web/src/lib/types.ts` (comment/type documentation only)

**Interfaces:**
- Produces: operators see a failed lesson through existing rollups and a failed phase carrying `mismatch_blocked`; student-facing preview continues to render only `done` phases.

- [ ] **Step 1: Pin API visibility**

Seed a failed phase with retained `output_md`, `error_message`, and `solver_status="mismatch_blocked"`. Assert `GET /jobs/{id}` serializes all three and job status is failed.

- [ ] **Step 2: Pin dashboard/watcher visibility**

Extend the real-DB dashboard coverage test so the blocked job contributes `failed=1`, `done=0`, and therefore the existing frontend `stuckCount/coverageState` path reports `needs_attention`. Add a repository-level watcher query test if any watcher filters phase errors; it must match `phase_outputs.status='failed'`, not the legacy `mismatch_shipped` string.

- [ ] **Step 3: Pin non-delivery visibility**

Add a Notion-hook test proving `archive_job` rejects/non-processes a failed job even when its failed phase retains non-empty markdown. Keep preview's `p.status === "done"` filter unchanged so known-bad markdown is not rendered as a deliverable.

- [ ] **Step 4: Add the operator label**

Add `mismatch_blocked: "answer-key blocked"` to `SOLVER_STATUS_LABEL` and the rose class map. This is an observability label for any phase-status console that displays the solver result; it must not widen the preview's done-only filter.

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/api/test_job_serialization.py \
       tests/services/test_pipeline_notion_hook.py -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/api/test_dashboard_coverage.py -q
cd web && npm run typecheck && cd ..
git add tests/api/test_job_serialization.py tests/api/test_dashboard_coverage.py \
        tests/services/test_pipeline_notion_hook.py \
        web/src/routes/preview.tsx web/src/lib/types.ts
git commit -m "feat(solver): surface blocked answer keys as failed work"
```

---

### Task 7: Dry-Run Historical Quarantine for Eligible Current-Tuple Rows

> **Final-gate correction (implemented):** the read-only corpus still contains 31
> `mismatch_shipped` jobs, but 26 pin at least one retired Gemini role. This lane
> deliberately does not restamp them. The tool reports 5 `eligible_current_tuple`
> and 26 `blocked_retired_tuple`; only the five current-tuple jobs enter the
> plan hash, manifest, or apply transaction. All 12 role fields are part of the
> expected snapshot. The 26 remain evidence for the separately filed
> `solver-retired-mismatch-restamp-1` follow-up.

**Files:**
- Create: `scripts/quarantine_solver_mismatches.py`
- Create: `tests/scripts/test_quarantine_solver_mismatches.py`
- Create: `tests/scripts/test_quarantine_solver_mismatches_integration.py`

**Interfaces:**
- Produces: deterministic `RemediationJob` (one job plus one-or-more `RemediationPhase` rows), snapshot hash, JSON manifest, `run(database_url, apply=False, expect_plan_hash=None, manifest_out=None)`.
- Mutating gesture: `--apply --expect-plan-hash HASH --manifest-out PATH` only.
- Explicitly does not import or call `notion_archive`, a Notion client, model transport, or `.env`-guessed database URL.

- [ ] **Step 1: Write the explicit-target and dry-run tests**

Tests must prove:

- missing raw `DATABASE_URL` exits before importing `app.config`;
- default invocation is dry-run and byte-for-byte preserves seeded rows;
- `--apply` without both hash and manifest path exits nonzero;
- plan hash changes when job status, phase status, solver status, output hash, `notion_archived_at`, or `toc_entries.notion_archived_job_id` changes;
- dry-run prints total/recent/archived counts and every job/phase id.

- [ ] **Step 2: Define the exact candidate query**

```sql
SELECT po.id AS phase_output_id,
       po.job_id,
       po.phase_name,
       po.status AS phase_status,
       po.solver_status,
       encode(digest(coalesce(po.output_md, ''), 'sha256'), 'hex') AS output_sha256,
       po.completed_at AS phase_completed_at,
       j.status AS job_status,
       j.completed_at AS job_completed_at,
       j.notion_archived_at,
       j.notion_skip_reason,
       j.claim_token,
       t.notion_archived_job_id
FROM phase_outputs po
JOIN homework_jobs j ON j.id = po.job_id
JOIN toc_entries t ON t.id = j.toc_entry_id
WHERE po.solver_status = 'mismatch_shipped'
  AND po.status = 'done'
  AND j.status = 'done'
ORDER BY po.completed_at, po.id;
```

Use SQLAlchemy/PostgreSQL `sha256` in Python if `pgcrypto.digest` is unavailable; the snapshot must still cover the same fields.

- [ ] **Step 3: Implement guarded, atomic DB quarantine**

Inside one transaction, re-read and re-hash the plan. Abort on any mismatch. Group candidate phases by `job_id`; for every `RemediationJob`:

1. Update every listed phase with expected predicates on id/job/status/solver status/completed time/output hash; set `status='failed'`, `solver_status='mismatch_blocked'`, and a nonblank remediation error. Preserve `output_md`, token counts, warnings, and completion timestamp.
2. Update the parent job exactly once with expected predicates on id/status/completed/archive fields/token; set `status='failed'`, `error_message` and `last_error` to the remediation message, clear `claim_token/claimed_at/claimed_by`, and preserve `notion_archived_at`, `notion_skip_reason`, and the TOC pointer.
3. Require phase rowcount exactly equal to the manifest's phase count and job rowcount exactly one. Any mismatch rolls back the entire transaction.
4. Write the manifest only after commit succeeds.

- [ ] **Step 4: Prove zero external side effects and idempotence**

Patch network/model/Notion constructors to raise if called. Apply to a scratch fixture, assert zero calls, re-run dry-run and assert zero remaining candidates. A second apply with the old hash must abort without writes.

- [x] **Step 5: Pin and classify the 31/5 production snapshot read-only**

The final read-only run found 31 total / 5 last-seven-days / 29 archived, then
classified them by executable role tuple: 5 current (all recent/archived) and
26 retired (24 archived). Current plan hash:
`25a684c41df0e90536b1d7058003ab6ff870ca4a7d2331a2fa795d31b6ae693f`.

- [ ] **Step 6: Document the post-quarantine operator path**

- The script makes jobs retryable and visible; it does not delete/overwrite Notion.
- Retry each failed job through the existing in-place retry path so only the blocked phase regenerates.
- The two currently unarchived jobs can auto-archive after a clean retry.
- The 29 archived jobs require `retry-archive?force=true` only **after** R26's guarded collision repair has been executed on production and the job is clean/done. This lane never bypasses that ordering.

- [ ] **Step 7: Verify and commit**

```bash
pytest tests/scripts/test_quarantine_solver_mismatches.py -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/scripts/test_quarantine_solver_mismatches_integration.py -q
git add scripts/quarantine_solver_mismatches.py \
        tests/scripts/test_quarantine_solver_mismatches.py \
        tests/scripts/test_quarantine_solver_mismatches_integration.py
git commit -m "fix(solver): quarantine historical shipped key mismatches"
```

---

### Task 8: Bounded Real-API Acceptance, Canonical Verification, and Handoff

**Files:**
- Create: `scripts/smoke_solver_fail_closed.py`
- Modify: `docs/HOW_IT_WORKS.md`
- Modify: `docs/CODE_MAP.md`
- Modify: `docs/DATABASE.md`
- Modify: `docs/memory/WISHLIST.md` or `docs/memory/ROADMAP.md` only after reserving the current counter under the repository's collision protocol
- Modify: `docs/memory/MASTER_MEMORY.md`
- Modify: `docs/memory/INDEX.md`
- Move after acceptance: this plan to `docs/superpowers/plans/shipped/2026-08-10-persistent-solver-mismatch-fail-closed.md`

**Interfaces:**
- Acceptance uses scratch PostgreSQL and the real configured `solver_transport='api'`; production `edu_copy` is read-only for source fixtures and receives no usage rows.
- Smoke budget request: **two real `gemini-3.1-pro-preview` solver calls, estimated $0.06-$0.12, hard cap $0.20**. No call runs until separately approved.

- [ ] **Step 1: Build the deterministic acceptance harness without spending**

The harness seeds one scratch `memory-check` job, stubs only content generation/judge to return a known wrong-key phase twice, and lets both calls to `solver.solve` use the real API transport. It asserts both real solver calls find the planted high-confidence mismatch; otherwise the smoke fails as a solver-recall failure, not a code pass.

- [ ] **Step 2: Add all five acceptance assertions**

1. Phase ends `failed/mismatch_blocked`, retains final wrong output and nonblank error.
2. Job ends failed; attempts and lease fields are consistent.
3. No `phase_completed` for the blocked phase, no `job_completed`, and zero archive calls.
4. Exactly two token-bearing `agent_usages` rows with operation `solve:memory-check`, model `gemini-3.1-pro-preview`, and total priced cost `0 < cost <= $0.20`.
5. Control fixture with a correct key produces `done/ok` and does not regenerate.

- [ ] **Step 3: Request approval, then run once**

```bash
DATABASE_URL="$SCRATCH_DATABASE_URL" SOURCE_DB_URL="$READ_ONLY_PRODUCTION_URL" \
  PYTHONPATH=. uv run python scripts/smoke_solver_fail_closed.py --max-cost-usd 0.20
```

If either planted mismatch is not detected, stop and report the acceptance failure; do not reroll until green or loosen the bar. The solver is probabilistic, and hiding a miss would invalidate the safety claim.

- [ ] **Step 4: Run canonical verification**

```bash
pytest -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  pytest tests/integration/test_migration_0053_solver_blocked.py \
         tests/repositories/test_mark_failed_phase_reconcile.py \
         tests/services/test_solver_fail_closed_e2e.py \
         tests/scripts/test_quarantine_solver_mismatches_integration.py -q
cd web && npm run typecheck && npm run build && cd ..
alembic heads
```

- [ ] **Step 5: De-stale docs precisely**

Document:

- `mismatch_shipped` is legacy-only and never newly emitted;
- `mismatch_blocked` is a failed, non-deliverable phase;
- true repair-path transients get bounded queue retry and phase reconciliation;
- initial unavailable/refused remains advisory;
- dashboard failed counts/watchers expose blocked lessons;
- historical 31/5/29-archived snapshot, its 5-current/26-retired split, and the DB-only eligible quarantine posture;
- no claim that `major_shipped` became terminal.

Reserve worklog/ROADMAP identifiers at execution time using the current counter protocol; do not reuse the plan-time next number.

- [ ] **Step 6: Re-run collision and rebase checks**

```bash
git fetch --all --prune
git log --oneline HEAD..origin/Nggaev-v2
git diff --name-only origin/Nggaev-v2...HEAD
gh pr list --state open --json number,headRefName,baseRefName,author,title
```

If base moved, rebase the implementation branch, rerun all verification, and re-anchor migration 0053 if another migration landed. Never modify #108/#117/#118 or another session's worktree.

- [ ] **Step 7: Commit finish artifacts and open, but do not merge, the PR**

```bash
git add docs scripts/smoke_solver_fail_closed.py
git commit -m "docs(solver): record fail-closed answer-key policy"
git push -u origin feat/persistent-solver-mismatch-fail-closed
gh pr create --base Nggaev-v2 --head feat/persistent-solver-mismatch-fail-closed \
  --title "Fail closed on persistent answer-key solver mismatches" \
  --body-file /tmp/persistent-solver-mismatch-pr.md
```

The implementation agent must not self-merge.

## Deployment and Rollback Ordering

1. Keep generation paused for the affected key-bearing scope while deploying.
2. Run the additive 0053 migration on the head database first. Old workers safely continue writing old status values; new code cannot safely write `mismatch_blocked` before this step.
3. Pull the final merged SHA on head and every worker, restart the head once (raising the worker-version floor), then restart/verify every worker against the new floor. Do not rebuild the frontend unless Task 6 changes bundled web assets; if it does, build once from final head.
4. Run the deterministic scratch acceptance and one bounded live canary before reopening key-bearing phases broadly.
5. Only after fleet health is clean, run the historical remediation dry-run. Review the live hash/scope. Run `--apply` as a separate operator gesture; it performs DB quarantine only for exact current-tuple jobs and must report retired tuples without mutating them.
6. Retry the five eligible quarantined jobs. Do not force-rearchive them until R26's production collision-repair sequence is complete and each job is clean/done. The 26 retired-tuple jobs require the separate in-place restamp lane first.

Rollback:

- Pause claims, roll workers/head back to the prior code SHA, and leave migration 0053 installed; it is additive and old code tolerates the wider CHECK.
- Failed/quarantined jobs remain failed, so rollback cannot accidentally distribute them.
- If the database migration itself must be downgraded, its downgrade first relabels `mismatch_blocked -> mismatch_shipped`, then restores the old CHECK. It does **not** change failed job/phase status or archive anything.
- Never rollback by converting failed jobs to done. Recovery is explicit retry after the cause is understood.

## Plan Self-Review

- **Spec coverage:** typed terminal outcome (Tasks 1/3), bounded transient retry and terminal exhaustion (Tasks 3/4/5), sibling/state consistency (Tasks 4/5), watcher/dashboard visibility (Task 6), cancel-wins/leases (Tasks 3-5), historical 5-current/26-retired classification and eligible remediation (Task 7), billed smoke (Task 8), collision refs and deploy/rollback ordering (global/Task 8).

### Final acceptance correction (2026-08-11)

The original paid gate wrapped `_spawn`, so a transient internal retry could
have exceeded its two-call accounting. The shipped harness gates `_spawn_once`
instead; a simulated repeated 429 proves the third actual attempt is rejected
before provider transport. The one corrected billed run made exactly two calls,
caught both planted mismatches, cost `$0.022020`, and left the control `done/ok`.
Cumulative spend across the original and corrected runs is `$0.046644` of the
approved `$0.20`; there were no production writes or model rerolls.
- **Major-shipped exclusion:** explicitly pinned in Global Constraints and docs acceptance; no task edits judge terminal policy.
- **No silent delivery:** phase failed before raise; job completion/archive event absence is tested in Tasks 5/6/8.
- **No placeholder implementation:** every task names exact files, signatures, assertions, commands, and expected RED/GREEN behavior. The only triple-dot forms are valid Python tuple typing (`tuple[str, ...]`) and Git's three-dot diff syntax.
- **Type consistency:** `PersistentSolverMismatch` and `SolveOutcome.failure` from Task 1 feed Task 3; `mismatch_blocked` from Task 2 feeds Tasks 3/6/7; Task 4's repository semantics feed Task 5's real chain.
- **Scope discipline:** no new review-state table, no generalized judge fail-closed policy, no automatic Notion mutation, no changes to solver phase coverage, and no model/prompt change.
