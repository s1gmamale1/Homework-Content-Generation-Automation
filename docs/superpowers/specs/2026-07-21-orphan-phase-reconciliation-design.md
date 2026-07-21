# Orphan Phase Reconciliation — Design

Closes WISHLIST `orphan-phase-reconciliation-1` (GK round-5 scope reminder on PR #109; field case: 10 `done` + 1 orphaned `running` phase → parent reclaimed `pending` → stale-pending sweep terminal-failed the job with ZERO `failed` phase rows, invisible to the dashboard's failed/cancelled-based "needs a look" logic).

## Problem

Two parent-only write sites leave phase rows contradicting the job:

1. `jobs.reclaim_stuck_jobs` (jobs.py:567) — stale `running` job → `pending`; phase rows untouched. Called by the worker's periodic sweep AND (via `reclaim_orphans_on_startup`) at boot.
2. `jobs.fail_exhausted_pending_jobs` (jobs.py:629) — exhausted `pending` job → terminal `failed`; phase rows untouched. The claim gate refuses `attempts >= max`, so `create_or_reset`'s re-claim self-heal never fires on this path.

**Startup wrinkle (verified 2026-07-21, main.py:41-51):** BEFORE either function runs at boot, the lifespan sweep marks every `pending`/`running` phase row `failed` with the synthetic message `"orphaned: worker restarted"` — so at startup a pending/running-only phase filter matches nothing. Eligibility must be marker-aware.

## Locked decisions

- **Two sites only** (user-locked 2026-07-21): reconcile in the SAME transaction at both mutation sites via `UPDATE … RETURNING id` + one set-based phase UPDATE per batch. No one-time heal of historical rows, no new periodic consistency watcher.
- **Marker-aware eligibility, evidence-preserving** (gate correction): synthetic startup-orphan failures are reconcilable; genuine failures keep their original error.

| Parent transition | Phase rows changed |
|---|---|
| Reclaim → `pending` | `running`, **plus `failed` rows whose `error_message` equals the startup-orphan marker** → `pending`, error cleared |
| Exhausted → `failed` | `pending`/`running`, **plus startup-orphan `failed` rows** → `failed` with the stale-pending message (`"attempts exhausted while pending (stale-pending sweep)"`) |
| Always preserved | `done` rows; genuinely `failed` rows with their original error |

- **Shared constant** `ORPHANED_RESTART_MESSAGE = "orphaned: worker restarted"` exported from `app/repositories/phase_outputs.py`; `main.py` uses it for both its books and phases sweep writes; the new eligibility predicate matches against it — never duplicated prose.
- **Rejected:** reordering/filtering `main.py`'s global phase sweep (cleaner long-term but expands beyond the locked two-site scope); a per-job Python loop over the #109 helper (N queries; callers don't know phase names).

## Helper contract

Widen #109's `phase_outputs.reset_abandoned_phases` (single home for the pending-vs-failed semantics):

```
async def reset_abandoned_phases(
    session,
    job_ids: Sequence[UUID],          # was: single job_id; empty list → no-op (return 0)
    *,
    phase_names: Optional[list[str]] = None,  # None = all phases; [] stays a no-op
    status: str,                       # "pending" | "failed" (assert kept from #109)
    error_message: str | None = None,  # required shape when status="failed"
    source_statuses: Sequence[str] = ("pending", "running"),  # NEW: explicit eligibility
    include_orphan_failed: bool = False,  # NEW: also match failed rows whose
                                          # error_message == ORPHANED_RESTART_MESSAGE
) -> int
```

- The #109 call sites in `pipeline._abandon_inflight` migrate to `job_ids=[job_id]` with their current behavior unchanged (`source_statuses` default, `include_orphan_failed=False`).
- `reclaim_stuck_jobs` calls it with `status="pending"`, `source_statuses=("running",)`, `include_orphan_failed=True`.
- `fail_exhausted_pending_jobs` calls it with `status="failed"`, `error_message=<stale-pending message>`, `source_statuses=("pending","running")`, `include_orphan_failed=True`.
- Both callers keep returning their existing job counts; reconciliation is internal.

## Testing (real Postgres, scratch `edu_scratch_qc`, RUN_DB_INTEGRATION idiom)

1. **Reclaim reconciles:** stale running job + rows `done`/`running`/`pending` → sweep → parent `pending`; `running` row → `pending` (error NULL); `done` frozen.
2. **Exhausted reconciles (the field-case pin):** pending job at `attempts=max` + 10 `done` + 1 `running` → sweep → parent `failed`; the `running` row `failed` with the stale-pending message; `done` rows untouched — the failure is finally visible at phase level.
3. **Fresh claims untouched:** a non-stale running job's rows unchanged by the reclaim sweep.
4. **Exact startup chain:** stale running parent at max attempts + 10 `done` / 1 `running`; call reclaim then exhausted in the SAME transaction (the main.py order) → final parent AND the unfinished phase both `failed` with the sweep message.
5. **Startup ordering / marker path:** pre-mark the unfinished phase `failed`/`ORPHANED_RESTART_MESSAGE` (as main.py's sweep does) before reclaim → prove it is still reconciled (→ `pending` on reclaim; → sweep-`failed` in the exhausted chain). A sibling assertion seeds a genuinely-failed row (different error) and proves it is NEVER touched.

All RED-provable against current code. Evidence-preservation (the genuine-failure guard) is the load-bearing predicate — RED-prove it bites.

## Acceptance

No spawn-path or generation code is touched — the standing real-model smoke gate does not apply. Acceptance = full canonical suite + the real-Postgres transaction tests above ($0).
