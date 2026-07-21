# Orphan Phase Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD per task, commit per task, stage only listed files.

**Goal:** The two parent-only queue writes (`reclaim_stuck_jobs`, `fail_exhausted_pending_jobs`) reconcile their jobs' phase rows in the same transaction, so a reclaimed job never carries orphaned `running` rows and an attempts-exhausted terminal failure is finally visible at phase level.

**Architecture:** Widen #109's `phase_outputs.reset_abandoned_phases` into the single reconciliation primitive (batch `job_ids`, optional `phase_names`, explicit `source_statuses`, marker-aware `include_orphan_failed`), then call it from both jobs.py sites via `UPDATE … RETURNING id`. Marker-awareness is required because `main.py`'s boot sweep pre-marks every pending/running phase row `failed`/"orphaned: worker restarted" BEFORE the startup reclaim runs (main.py:41-51, verified 2026-07-21) — a pending/running-only filter matches nothing at boot.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest; real-DB tests behind `RUN_DB_INTEGRATION=1` on scratch `edu_scratch_qc` (`postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc`, PGPASSWORD=edu, always 127.0.0.1).

**Spec:** `docs/superpowers/specs/2026-07-21-orphan-phase-reconciliation-design.md` (user-locked: two sites only; marker-aware, evidence-preserving eligibility).

## Global Constraints

- **No migration.** No model calls anywhere (no spawn-path code is touched — acceptance is the suite + real-Postgres transaction tests, $0).
- Eligibility table is binding: reclaim → `running` + orphan-marker `failed` rows → `pending` (error cleared); exhausted → `pending`/`running` + orphan-marker `failed` rows → `failed` with `"attempts exhausted while pending (stale-pending sweep)"`; `done` rows and genuinely-`failed` rows (any other error) are NEVER touched.
- The orphan marker prose exists in exactly ONE place: `phase_outputs.ORPHANED_RESTART_MESSAGE = "orphaned: worker restarted"`; `main.py` consumes it for both its books and phases sweep writes.
- Stage ONLY the files each task lists; commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Suite bar: `uv run python -m pytest tests/ -q` (canonical, no flag) green.
- All work in worktree `/Users/macmini5/Documents/HCGA-orphan-recon`, branch `fix/orphan-phase-reconciliation`.

---

### Task 1: Widen `reset_abandoned_phases` + `ORPHANED_RESTART_MESSAGE` constant

**Files:**
- Modify: `app/repositories/phase_outputs.py` (the `reset_abandoned_phases` function + new module constant + `Sequence` import)
- Modify: `app/services/pipeline.py:505` (the one internal call site — `job_id` → `[job_id]`)
- Modify: `main.py:44,50` (both `error_message="orphaned: worker restarted"` literals → the constant; `phase_repo` is already imported)
- Test: `tests/repositories/test_phase_outputs_abandoned.py` (migrate 2 existing calls + add marker/no-op cases)

**Interfaces:**
- Consumes: current `reset_abandoned_phases(session, job_id, *, phase_names, status, error_message=None)` (#109 shape).
- Produces (Task 2 relies on this exact signature):

```python
ORPHANED_RESTART_MESSAGE = "orphaned: worker restarted"

async def reset_abandoned_phases(
    session: AsyncSession,
    job_ids: Sequence[UUID],
    *,
    phase_names: Optional[list[str]] = None,
    status: str,
    error_message: Optional[str] = None,
    source_statuses: Sequence[str] = ("pending", "running"),
    include_orphan_failed: bool = False,
) -> int
```

- [ ] **Step 1: Migrate + extend the tests.** In `tests/repositories/test_phase_outputs_abandoned.py`: change both existing calls (lines ~115, ~133) from `phase_repo.reset_abandoned_phases(db_session, seeded_job.id, …)` to `phase_repo.reset_abandoned_phases(db_session, [seeded_job.id], …)` (keep every other kwarg identical). Append these tests (same real-DB skip idiom as the file already uses; the no-op tests are pure and take `session=None`):

```python
def test_empty_job_ids_is_noop_without_touching_session():
    """Contract: empty job_ids returns 0 before any session use."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(None, [], status="pending")
    ) == 0


def test_empty_phase_names_list_is_still_noop():
    """phase_names=[] keeps the #109 no-op contract (None means ALL)."""
    import asyncio
    assert asyncio.run(
        phase_repo.reset_abandoned_phases(
            None, [uuid.uuid4()], phase_names=[], status="pending"
        )
    ) == 0


async def test_orphan_marker_failed_rows_reconcile_but_genuine_failures_never(
    db_session, seeded_job
):
    """The load-bearing predicate (RED-proof: without the marker clause the
    orphan-failed row is untouched; without the equality guard the genuine
    failure would be rewritten).

    seeded_job rows: flashcards=pending, boss-arena=running, reading=done,
    reflection=failed(error=None). Re-point reflection to a GENUINE error and
    add the orphan marker to boss-arena as main.py's boot sweep would."""
    from app.repositories.phase_outputs import ORPHANED_RESTART_MESSAGE
    await db_session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == seeded_job.id,
               PhaseOutput.phase_name == "boss-arena")
        .values(status="failed", error_message=ORPHANED_RESTART_MESSAGE)
    )
    await db_session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == seeded_job.id,
               PhaseOutput.phase_name == "reflection")
        .values(error_message="judge crashed: real evidence")
    )
    n = await phase_repo.reset_abandoned_phases(
        db_session, [seeded_job.id],
        status="pending",
        source_statuses=("running",),
        include_orphan_failed=True,
    )
    # flashcards is 'pending' but source_statuses=('running',) excludes it;
    # boss-arena matches ONLY via the marker clause.
    assert n == 1
    rows = {r.phase_name: r for r in await phase_repo.list_for_job(db_session, seeded_job.id)}
    assert rows["boss-arena"].status == "pending"
    assert rows["boss-arena"].error_message is None
    assert rows["flashcards"].status == "pending"          # untouched
    assert rows["reading"].status == "done"                # frozen
    assert rows["reflection"].status == "failed"           # genuine evidence kept
    assert rows["reflection"].error_message == "judge crashed: real evidence"
```

  Add `import uuid` and extend the file's existing `update`/`PhaseOutput` imports as needed (the file already imports `update` and `PhaseOutput` for seeding — verify, add if missing).
- [ ] **Step 2: Run to verify failure.** `cd /Users/macmini5/Documents/HCGA-orphan-recon && PGPASSWORD=edu RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc uv run python -m pytest tests/repositories/test_phase_outputs_abandoned.py -q` → the migrated calls TypeError (list vs UUID) and the new tests fail (no constant / no kwargs). This is the RED for the signature change.
- [ ] **Step 3: Implement.** In `app/repositories/phase_outputs.py`: add `Sequence` to the `typing` import; add the constant above the function; replace the whole `reset_abandoned_phases` with:

```python
# The synthetic error main.lifespan's boot sweep stamps on every
# pending/running phase row before the startup reclaim runs. The
# reconciliation predicate matches THIS exact prose — single source,
# never duplicate the string (orphan-phase-reconciliation-1).
ORPHANED_RESTART_MESSAGE = "orphaned: worker restarted"


async def reset_abandoned_phases(
    session: AsyncSession,
    job_ids: Sequence[UUID],
    *,
    phase_names: Optional[list[str]] = None,
    status: str,
    error_message: Optional[str] = None,
    source_statuses: Sequence[str] = ("pending", "running"),
    include_orphan_failed: bool = False,
) -> int:
    """Reset a batch of jobs' abandoned phase rows (queue-correctness-1 +
    orphan-phase-reconciliation-1). 'done' rows are always untouched;
    'failed' rows are untouched unless they carry the synthetic
    ORPHANED_RESTART_MESSAGE and include_orphan_failed=True — genuine
    failure evidence is never rewritten.

    status='pending' (job requeued/parked — rows are WAITING, error cleared)
    or status='failed' (job terminal — error_message recorded).
    phase_names=None means every phase of the job; [] is a no-op (the #109
    scheduler contract). Empty job_ids is a no-op before any session use."""
    assert status in ("pending", "failed"), status
    if not job_ids:
        return 0
    if phase_names is not None and not phase_names:
        return 0
    from sqlalchemy import func as sa_func, or_
    values: dict = {"status": status}
    if status == "failed":
        values["error_message"] = error_message
        values["completed_at"] = sa_func.now()
    else:
        values["error_message"] = None
    eligible = PhaseOutput.status.in_(tuple(source_statuses))
    if include_orphan_failed:
        eligible = or_(
            eligible,
            (PhaseOutput.status == "failed")
            & (PhaseOutput.error_message == ORPHANED_RESTART_MESSAGE),
        )
    stmt = (
        update(PhaseOutput)
        .where(PhaseOutput.job_id.in_(list(job_ids)), eligible)
        .values(**values)
    )
    if phase_names is not None:
        stmt = stmt.where(PhaseOutput.phase_name.in_(phase_names))
    result = await session.execute(stmt)
    return result.rowcount
```

  In `app/services/pipeline.py` (the `_abandon_inflight` body, line ~505): `phase_repo.reset_abandoned_phases(session, job_id, …)` → `phase_repo.reset_abandoned_phases(session, [job_id], …)` — nothing else changes (defaults preserve #109 behavior exactly).

  In `main.py`: replace both `error_message="orphaned: worker restarted"` literals (books sweep line ~44, phases sweep line ~50) with `error_message=phase_repo.ORPHANED_RESTART_MESSAGE` (`phase_repo` is already imported there — verify the alias name used by main.py and match it).
- [ ] **Step 4: Run green.** The Step-2 command → all tests pass. Then the #109 neighborhood: `uv run python -m pytest tests/services/test_scheduler_abandoned_rows.py tests/services/test_pipeline_transient_propagation.py -q` → green (the scheduler mocks patch `_abandon_inflight` itself, so the internal call change is invisible to them — if anything fails, read it, don't force it).
- [ ] **Step 5: Commit.**

```bash
git add app/repositories/phase_outputs.py app/services/pipeline.py main.py tests/repositories/test_phase_outputs_abandoned.py
git commit -m "feat(phases): widen reset_abandoned_phases — batch job_ids, marker-aware orphan eligibility

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2: Reconcile at both jobs.py sites (+ the five spec tests)

**Files:**
- Modify: `app/repositories/jobs.py` — `reclaim_stuck_jobs` (~:567) and `fail_exhausted_pending_jobs` (~:629)
- Test: `tests/repositories/test_jobs_orphan_reconciliation.py` (new)

**Interfaces:**
- Consumes Task 1's exact signature (see Task 1 Produces block) plus `ORPHANED_RESTART_MESSAGE`.
- Produces: both functions keep returning `int` (count of jobs touched — callers in worker.py/main.py only log it).

- [ ] **Step 1: Write the five spec tests** in `tests/repositories/test_jobs_orphan_reconciliation.py` (real-DB skip idiom + seeding fixture copied from `tests/repositories/test_phase_outputs_abandoned.py`; the fixture must ALSO follow that file's post-#109 teardown craft — `await db_session.rollback()` then same-session deletes). Seed helper: one book + toc entry + a job with parametrizable `status/attempts/claimed_at`, and phase rows given as `(name, status, error_message)` tuples. The five tests, with exact assertions:

```python
async def test_reclaim_resets_running_phase_rows(db_session, seed):
    """Spec test 1. RED: today the running row survives the reclaim."""
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=9999,
                    phases=[("a", "done", None), ("b", "running", None),
                            ("c", "pending", None)])
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 1
    row = await _job(db_session, job.id)
    assert row.status == "pending"
    phases = await _phases(db_session, job.id)
    assert phases["b"].status == "pending" and phases["b"].error_message is None
    assert phases["a"].status == "done"
    assert phases["c"].status == "pending"


async def test_exhausted_sweep_fails_unfinished_phase_rows(db_session, seed):
    """Spec test 2 — the field-case pin (10 done + 1 running, invisible
    failure). RED: today the job fails with ZERO failed phase rows."""
    phases = [(f"p{i}", "done", None) for i in range(10)] + [("stuck", "running", None)]
    job = await seed(status="pending", attempts=3, phases=phases)
    n = await jobs_repo.fail_exhausted_pending_jobs(db_session, max_attempts=3)
    assert n == 1
    row = await _job(db_session, job.id)
    assert row.status == "failed"
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "failed"
    assert ph["stuck"].error_message == "attempts exhausted while pending (stale-pending sweep)"
    assert all(ph[f"p{i}"].status == "done" for i in range(10))


async def test_fresh_claims_and_their_rows_untouched(db_session, seed):
    """Spec test 3: a non-stale running job is not reclaimed, rows unchanged."""
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=0,
                    phases=[("b", "running", None)])
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 0
    assert (await _phases(db_session, job.id))["b"].status == "running"


async def test_startup_chain_reclaim_then_exhausted(db_session, seed):
    """Spec test 4: the exact main.py order in ONE transaction — stale
    running parent already at max attempts. Reclaim flips it pending (rows
    → pending), then the exhausted sweep terminal-fails it and the
    unfinished phase carries the sweep message."""
    phases = [(f"p{i}", "done", None) for i in range(10)] + [("stuck", "running", None)]
    job = await seed(status="running", attempts=3,
                    claimed_at_age_seconds=9999, phases=phases)
    await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    await jobs_repo.fail_exhausted_pending_jobs(db_session, max_attempts=3)
    row = await _job(db_session, job.id)
    assert row.status == "failed"
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "failed"
    assert ph["stuck"].error_message == "attempts exhausted while pending (stale-pending sweep)"


async def test_startup_marker_rows_reconcile_but_genuine_failures_kept(db_session, seed):
    """Spec test 5: main.py's boot sweep runs FIRST and pre-marks the
    unfinished row failed/ORPHANED_RESTART_MESSAGE — reclaim must still
    reconcile it; a genuinely-failed sibling keeps its evidence."""
    from app.repositories.phase_outputs import ORPHANED_RESTART_MESSAGE
    job = await seed(status="running", attempts=1,
                    claimed_at_age_seconds=9999,
                    phases=[("a", "done", None),
                            ("stuck", "failed", ORPHANED_RESTART_MESSAGE),
                            ("real", "failed", "judge crashed: real evidence")])
    n = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert n == 1
    ph = await _phases(db_session, job.id)
    assert ph["stuck"].status == "pending" and ph["stuck"].error_message is None
    assert ph["real"].status == "failed"
    assert ph["real"].error_message == "judge crashed: real evidence"
```

  `_job` / `_phases` are two tiny module helpers (`select(HomeworkJob)…scalar_one()` with `populate_existing`, and `{r.phase_name: r for r in phase_repo.list_for_job(...)}` after `db_session.expire_all()` — expire first so the asserts read DB state, not stale identity-map copies). `claimed_at_age_seconds` seeds `claimed_at = func.now() - make_interval(...)`; `0` means fresh.
- [ ] **Step 2: RED.** `PGPASSWORD=edu RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc uv run python -m pytest tests/repositories/test_jobs_orphan_reconciliation.py -q` → tests 1, 2, 4, 5 FAIL on the phase-row assertions (rows currently never change); test 3 passes (guard baseline).
- [ ] **Step 3: Implement** in `app/repositories/jobs.py`. Add `from app.repositories import phase_outputs as phase_repo` to the module imports (phase_outputs imports only models — no cycle). Then:

  `reclaim_stuck_jobs` — replace the execute/return tail:

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
        .returning(HomeworkJob.id)
    )
    result = await session.execute(stmt)
    reclaimed = [row[0] for row in result.fetchall()]
    if reclaimed:
        # Same-transaction phase reconciliation (orphan-phase-reconciliation-1):
        # a reclaimed job's in-flight rows go back to WAITING. Marker-aware
        # because main.lifespan's boot sweep pre-marks them failed/"orphaned:
        # worker restarted" before the startup reclaim runs.
        await phase_repo.reset_abandoned_phases(
            session, reclaimed,
            status="pending",
            source_statuses=("running",),
            include_orphan_failed=True,
        )
    return len(reclaimed)
```

  `fail_exhausted_pending_jobs` — replace the execute/return tail (keep `_msg` as-is):

```python
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.attempts >= max_attempts)
        .values(
            status="failed",
            completed_at=func.now(),
            error_message=_msg,
            last_error=_msg,
            claimed_at=None,
            claimed_by=None,
        )
        .returning(HomeworkJob.id)
    )
    result = await session.execute(stmt)
    failed_ids = [row[0] for row in result.fetchall()]
    if failed_ids:
        # Terminal job ⇒ every unfinished row terminal too (mirrors
        # mark_cancelled) — makes the failure VISIBLE at phase level: the
        # 10-done+1-running field case previously failed with zero failed
        # phase rows, invisible to failed/cancelled-based watchers.
        await phase_repo.reset_abandoned_phases(
            session, failed_ids,
            status="failed",
            error_message=_msg,
            source_statuses=("pending", "running"),
            include_orphan_failed=True,
        )
    return len(failed_ids)
```

  Update both docstrings with one line each naming the reconciliation.
- [ ] **Step 4: GREEN + neighborhood.** Step-2 command → 5 passed. Then `uv run python -m pytest tests/ -q -k "reclaim or exhausted or orphan or worker"` (no flag) and the full no-flag sweep `uv run python -m pytest tests/services/ tests/repositories/ -q` → green.
- [ ] **Step 5: Commit.**

```bash
git add app/repositories/jobs.py tests/repositories/test_jobs_orphan_reconciliation.py
git commit -m "fix(queue): reclaim + exhausted sweep reconcile phase rows in the same transaction

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3: Finish (controller)

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/WISHLIST.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`
- Move: this plan + the spec stay put; `git mv docs/superpowers/plans/2026-07-21-orphan-phase-reconciliation.md docs/superpowers/plans/shipped/`

- [ ] **Step 1:** Full canonical suite `uv run python -m pytest tests/ -q` green; WITH-flag run of the two touched real-DB files green.
- [ ] **Step 2:** Docs: worklog **0156** entry + INDEX row (**re-check the INDEX tail number first** — 0154/0155 taken); WISHLIST: strike `orphan-phase-reconciliation-1` → `**SHIPPED (worklog 0156)**`; `docs/HOW_IT_WORKS.md` — rewrite the 0155 scope-note parenthetical (the "Scope note: this covers the in-process scheduler only…" sentence) to say the startup reclaim and attempts-exhausted sweep now reconcile phase rows too (marker-aware, evidence-preserving); `docs/CODE_MAP.md` — update the `errors.py`/queue-repo companion line: `reset_abandoned_phases` now batch + marker-aware, and name `ORPHANED_RESTART_MESSAGE`.
- [ ] **Step 3:** `git fetch origin && git log HEAD..origin/Nggaev-v2` → rebase + re-run suite if the base moved (PR #108 may land). `git mv` the plan to `shipped/`, commit docs with trailer, push `fix/orphan-phase-reconciliation`, open PR to `Nggaev-v2` for GK2 — body: what/why (field case), the eligibility table, verification (suite counts + the five real-DB proofs), "no migration, no model calls".

## Self-review (done at write time)

- **Spec coverage:** eligibility table → Task 1 predicate + Task 2 call args; shared constant → Task 1 (both main.py literals); helper contract (job_ids/phase_names/source_statuses/include_orphan_failed + no-ops) → Task 1 code + no-op tests; five spec tests → Task 2 (1:1); $0 acceptance → Global Constraints + Task 3.
- **Type consistency:** Task 2 consumes exactly Task 1's Produces signature; both jobs.py functions still return `int`.
- **Callers audited:** `reset_abandoned_phases` callers = pipeline.py:505 (migrated in Task 1) + the two new jobs.py sites; `reclaim_stuck_jobs` callers (worker.py:687, jobs.py:626 via startup) and `fail_exhausted_pending_jobs` callers (worker.py:699, main.py:66) only consume the int — no signature fallout.
- **No placeholders:** every code step carries complete code; the only copy-from-source items name their exact source file (seeding/teardown idiom from `test_phase_outputs_abandoned.py`).
