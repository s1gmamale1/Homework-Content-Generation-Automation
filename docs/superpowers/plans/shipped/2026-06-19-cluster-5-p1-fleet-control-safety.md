# Cluster 5 — Fleet scale, Sub-plan P1: Fleet control & safety

> Branch `cluster-5-fleet-scale` (worktree `../HCGA-c5-fleet-scale`), cut off `origin/Nggaev-v2` tip `b6bbe90` (#37). Worklog **0081** (verify live head at finish). Commit prefix `c5:`. PR title `[cluster-5]`.
> This is **P1 of a 3-way split** of Cluster 5 (user-approved 2026-06-19): **P1 = fleet control & safety** (this plan), P2 = shared token-bucket + Retry-After backoff (heavy, hot spawn path), P3 = multi-pod SSE (LISTEN/NOTIFY). `fleet-infra-1/2` deferred to ROADMAP/runbook.

## Approach & key decisions

Three low-risk, **non-generation-affecting** fleet items that share the worker/claim/registry lane, so they ride one plan but stay independent tasks. **Zero migrations** — every column already exists (verified against `b6bbe90`).

- **`fleet-restart-reclaim-1` (peer-aware startup reset).** `main.py:52` resets **every** `running` job (`reclaim_stuck_jobs(stale_after_seconds=0)`) on boot — double-runs a live peer's heartbeated job in a multi-PC fleet (real $ on api). `reclaim_stuck_jobs` is **already lease-aware** (`claimed_at < now - stale`); the bug is only the `0` window. **Chosen:** *peer-aware* — at the startup sweep (which runs **before** the embedded worker registers, so the registry shows only peers) check `has_live_workers`; reset-all (`stale=0`, preserving today's instant single-host recovery) **only when no peer is live**, else use `reclaim_stale_seconds` so a peer's fresh-beat job is untouched. Rejected *pure lease-aware-always* because it regresses single-host recovery to a ~120 s wait for no benefit. **Best-effort caveat (accept-and-document, T2 comment):** on a sub-`reclaim_stale_seconds` restart the prior process's `workers` row (same hostname, old pid) may still beat fresh → read as a live peer → lease path that one time (instant recovery just doesn't fire). Correctness is unaffected (never double-runs; the dead process's stale jobs still reclaim via the window). Do **not** filter same-hostname to "fix" it — two processes on one host are legitimately distinct peers, so filtering would let a sibling's live job be reclaimed (same-host double-run).
- **`fleet-ctrl-3` (manual batch pause/resume).** C4 already shipped the primitive (`batches.pause_batch`/`unpause_batch` + the `claim_next_job` paused-batch skip). P1 adds **only** the manual operator layer: `POST /jobs/batch/{id}/pause|unpause` (mirror C2's cancel/resume in `batch.py`) + an FE button. **Load-bearing decision:** manual pause uses reason **`"manual"`** — distinct from C4's `"batch-cap"`/`"fleet-daily-cap"`. The two pause systems cannot clobber each other **in either direction**: (a) *unpause* — the monitor reconciles only `paused_batch_ids_by_reason("batch-cap")` (worker.py:484), never a `"manual"` pause; (b) *pause* — the monitor pauses only `active_batch_ids`, defined as `paused_at IS NULL` (batches.py), so an already-manually-paused batch is excluded and its reason can't be overwritten to `"batch-cap"`. **Self-correcting corollary (intended):** manually *unpausing* a batch that is still over its budget cap lets the monitor re-pause it (`"batch-cap"`) next cycle — correct, not a bug. Reuse the primitive; do **not** build a second pause path.
- **`fleet-ctrl-4` (PC-level drain).** Workers are individually addressable (`workers` table, `pc_id=hostname:pid`, `status String(32)` free-text → no migration). Head sets `status="draining"` via `POST /workers/{pc_id}/drain`; the worker's registry-heartbeat loop reads its **own** status each beat and, when `"draining"`, calls `self.stop()` (existing `_stop_event` → stops claiming, drains in-flight via `_drain()`, deregisters). **Trap:** `upsert_heartbeat` rewrites `status` to `"online"` every beat (workers.py:40), so the loop must **read status first and, if draining, stop without re-upserting `"online"`** — otherwise the head's signal is overwritten before the worker sees it. Bounded by `heartbeat_seconds` (≤30 s) — acceptable for graceful drain.

**Verified facts:** reclaim helper `reclaim_stuck_jobs(*, stale_after_seconds)` (jobs.py:424); startup call `main.py:52`; `workers_repo.upsert_heartbeat`/`list_with_liveness` (workers.py:34,69) + clobber semantics; `batches.pause_batch(..., reason)`/`unpause_batch` (batches.py:149,162); budget-monitor reason-scoping (worker.py:474,484); cancel/resume endpoint pattern (batch.py:311,330); worker `_stop_event`/`stop()`/`_drain()`/`_registry_heartbeat` (worker.py:178,280,596,571); FE surfaces `launcher.tsx` (cancel/resume buttons 874-907, mutations 711-735), `batch-funnel.tsx` badge (54-64), `worker-cards.tsx`, `api.ts` (335-353), `types.ts` (372-383); `GET /workers` router gated by `get_current_user` (api/v1/__init__.py:18, workers.py:11).

**No generation impact → no CLI smoke.** Acceptance = full suite green + **real-DB integration tests** (scratch DB `createdb -O edu …`, `RUN_DB_INTEGRATION=1`) for the reclaim/status predicates, RED-proving each guard.

## Global constraints (binding — copy into every reviewer dispatch)

1. **No migration.** `workers.status` is free `String(32)`; batch pause columns shipped in C4 (mig 0031). Any task that reaches for `alembic revision` is wrong — stop and escalate.
2. **Manual pause reason MUST be the exact string `"manual"`** — never `"batch-cap"`/`"fleet-daily-cap"`. This reason-scoping is what keeps the C4 budget monitor from clobbering a manual pause (and vice-versa). A test must assert it.
3. **Reuse C4's `pause_batch`/`unpause_batch`** — do not add a parallel pause column or mechanism.
4. **Drain must not be clobbered:** the heartbeat loop reads own status and, when `"draining"`, stops WITHOUT re-upserting `"online"`.
5. **Peer-aware reclaim preserves single-host instant recovery** (`stale=0` when no live peer); only a live peer forces the lease window.
6. TDD per task; a new function gets a test that runs its **real body** (mock only the I/O boundary). **RED-prove every predicate/guard** (delete the guard → test fails → restore) and report it.
7. Stage only the files each task lists (`git add <paths>`, never `-A`); one commit per task, `c5:` prefix. Other sessions may commit to shared files.

---

## Task 1 — `workers_repo.has_live_workers`

**Files:** `app/repositories/workers.py`, `tests/repositories/test_workers.py` (or the existing workers test module).

Add `async def has_live_workers(session, *, stale_after_seconds: int) -> bool` — returns True iff at least one `workers` row has `last_heartbeat >= db_now - stale_after_seconds` (reuse the `db_now`/`is_online` clock discipline already in `list_with_liveness`). Pure registry read; no side effects.

**Test (real DB, `RUN_DB_INTEGRATION=1`):** seed a fresh-beat row → True; seed only a stale row (heartbeat older than window) → False; empty table → False. RED-prove: a stub returning constant True must fail the stale/empty cases.

**Commit:** `c5: add has_live_workers registry helper (fleet-restart-reclaim-1)`

## Task 2 — peer-aware startup reclaim

**Files:** `app/repositories/jobs.py`, `main.py`, `tests/repositories/test_jobs_reclaim.py` (real-DB), plus de-stale the `main.py:48-51` comment.

Add `async def reclaim_orphans_on_startup(session, *, reclaim_stale_seconds: int) -> int`: if `await workers_repo.has_live_workers(session, stale_after_seconds=reclaim_stale_seconds)` → window = `reclaim_stale_seconds`, else window = `0`; return `await reclaim_stuck_jobs(session, stale_after_seconds=window)`. Wire `main.py:52` to call it with `settings.reclaim_stale_seconds`; rewrite the now-false "NOT safe for multi-pod" comment to describe peer-aware behavior.

**Test (real DB):** seed a fresh-claimed `running` job. (a) No worker rows → it IS reset to `pending` (alone ⇒ stale=0). (b) Add a fresh-beat *peer* worker row → the fresh-claimed job is NOT reset, but a separately-seeded stale-claimed `running` job IS reset. RED-prove: forcing window=0 unconditionally must fail case (b)'s "fresh job untouched" assertion.

**Commit:** `c5: peer-aware startup reclaim — don't yank live peers' jobs (fleet-restart-reclaim-1)`

## Task 3 — backend manual pause/unpause endpoints

**Files:** `app/api/v1/batch.py`, `tests/api/test_batch_pause.py`.

Add `POST /jobs/batch/{batch_id}/pause` → `pause_batch(session, batch_id, "manual")`; `POST /jobs/batch/{batch_id}/unpause` → `unpause_batch(session, batch_id)`. Mirror cancel/resume exactly: `session.get(Batch, batch_id)` → 404 → repo → `session.commit()` → return `{"batch_id": str(batch_id), "paused": <bool>}`. **No `RUNNING_JOBS` local-kill** — pause never touches in-flight work.

**Tests:** pause sets `paused_at` non-null and `paused_reason == "manual"`; unpause clears both; unknown id → 404. **Reason-scoping regression:** pause a batch with reason `"manual"`, run `batches_repo.unpause_by_reason(session, "batch-cap")`, assert the manual batch stays paused (guards the reason choice / constraint #2).

**Commit:** `c5: manual batch pause/unpause endpoints, reason="manual" (fleet-ctrl-3)`

## Task 4 — FE manual pause/resume button

**Files:** `web/src/lib/api.ts`, `web/src/lib/types.ts`, `web/src/components/fleet/launcher.tsx`, `web/src/components/fleet/batch-funnel.tsx`.

`api.ts`: `pauseBatch`/`unpauseBatch` (POST, mirror `cancelBatch`). `types.ts`: `BatchPauseResponse { batch_id: string; paused: boolean }`. `launcher.tsx`: a Pause/Resume toggle button in the ReadyCard control row (near cancel/resume, ~874-907) that calls pause when `!paused_at` and unpause when `paused_at`, shown when the batch has non-terminal jobs. `batch-funnel.tsx`: make the amber badge (54-64) reason-aware — "Paused by operator" when `paused_reason === "manual"`, else the existing "budget cap reached (…)" copy.

**Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit` clean; `npm run build` succeeds. (FE has no unit harness here — typecheck+build is the gate, per repo convention.)

**Commit:** `c5: FE manual pause/resume button + reason-aware badge (fleet-ctrl-3)`

## Task 5 — `workers_repo.get_status` / `set_status`

**Files:** `app/repositories/workers.py`, workers test module.

`async def get_status(session, pc_id) -> str | None` (None if no row). `async def set_status(session, pc_id, status) -> bool` (UPDATE status WHERE pc_id; return `rowcount > 0` for the 404 signal). Do **not** touch `last_heartbeat`.

**Test (real DB):** upsert a worker → `get_status` returns `"online"`; `set_status(pc_id,"draining")` returns True and `get_status` now `"draining"`; `set_status("nope",…)` returns False; `get_status("nope")` None. RED-prove the rowcount return.

**Commit:** `c5: worker get_status/set_status registry helpers (fleet-ctrl-4)`

## Task 6 — worker self-drain on `status="draining"`

**Files:** `app/services/worker.py`, `tests/services/test_worker_drain.py`.

In the registry-heartbeat loop (`_registry_heartbeat`/`_registry_heartbeat_loop`, ~571-594), each tick: read own status via `get_status(session, self.id)`; if `"draining"` → `self.stop()` (sets `_stop_event`) and **return/skip the `upsert_heartbeat("online")`** for that tick (constraint #4); else upsert `"online"` as today. Extract the per-tick decision into a small awaitable (e.g. `_drain_check_and_beat(session) -> bool` returning whether it kept beating) so it's unit-testable without the loop's sleep.

**Test:** with a fake/real session, a row whose status is `"draining"` → the method calls `stop()` (asserts `_stop_event.is_set()`) and does NOT upsert `"online"`; an `"online"` row → keeps beating, `_stop_event` clear. RED-prove: removing the draining branch leaves `_stop_event` clear → test fails.

**Commit:** `c5: worker self-drains when registry status=draining (fleet-ctrl-4)`

## Task 7 — backend drain endpoint

**Files:** `app/api/v1/workers.py`, `tests/api/test_workers_drain.py`.

`POST /workers/{pc_id}/drain` → `set_status(session, pc_id, "draining")`; commit; 404 when `set_status` returns False; return `{"pc_id": pc_id, "status": "draining"}`. (Router already carries `get_current_user`.) Optionally `POST /workers/{pc_id}/undrain` → `set_status(...,"online")` for symmetry/recovery before the next beat.

**Test:** drain a registered worker → 200 + row status `"draining"`; unknown pc_id → 404.

**Commit:** `c5: POST /workers/{pc_id}/drain endpoint (fleet-ctrl-4)`

## Task 8 — FE drain button on worker cards

**Files:** `web/src/lib/api.ts`, `web/src/lib/types.ts`, `web/src/components/fleet/worker-cards.tsx` (+ `online-strip.tsx` if it shows status).

`api.ts`: `drainWorker(pcId)` (POST). Worker card: a Drain button per online worker that calls it + a `"draining"` status chip (distinct from online/offline) reading the `status` field already in the `/workers` payload.

**Verify:** `tsc -p tsconfig.app.json --noEmit` clean; `npm run build` succeeds.

**Commit:** `c5: FE drain button + draining chip on worker cards (fleet-ctrl-4)`

---

## Finish (after final whole-branch review)

Full suite green (`uv run python -m pytest tests/ -q`) + the real-DB integration tests (scratch DB). Rebase-check on `origin/Nggaev-v2` before PR. Then: (a) worklog **0081** in `MASTER_MEMORY.md` + INDEX row; (b) close `fleet-restart-reclaim-1` + the manual-pause/drain slices of `fleet-ctrl-3`/`fleet-ctrl-4` in WISHLIST/ROADMAP (note ctrl-3/4 remaining: none for P1 scope; token-bucket/SSE tracked under P2/P3); (c) `git mv` this plan to `plans/shipped/`; (d) de-stale `docs/HOW_IT_WORKS.md` (startup reclaim semantics; manual pause; PC drain), `docs/DEPLOY.md` (drain procedure for taking a PC offline), `docs/CODE_MAP.md` (new endpoints/helpers). **No `DATABASE.md` change** (no schema change). PR `[cluster-5]` → gatekeeper merges (no self-merge).
