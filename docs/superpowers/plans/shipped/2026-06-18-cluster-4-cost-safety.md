# Cluster 4 — Cost safety (worklog 0080)

**Branch:** `cluster-4-cost-safety` (off `origin/Nggaev-v2` tip `d0cd306`, post-Cluster-1, migration head `0028`).
**Closes:** `fleet-api-3` (cost ledger + kill-switch), `fleet-api-4` (never-pay-twice), `pricing-1` (claude cache-write billing).
**Commit prefix:** `c4:` · **PR title:** `[cluster-4] Cost safety — ledger + pause-claim kill-switch + cache-write billing`

---

## Approach & key decisions

**Goal:** make paid (`transport=api`) generation safe to scale — nothing today reads `cost_usd` to gate spend (`pricing.cost_usd` is used only by the read-only `/agent/stats`), a re-run silently re-bills, and claude cache-writes are unpriced.

**Locked decisions (user, 2026-06-18):**
- **Cap granularity = BOTH.** A job is blocked if **either** cap is hit: (a) **per-batch $** (computed-on-read by joining `agent_usages → homework_jobs.batch_id`, api rows only — `agent_usages` has no `batch_id`), and (b) **per-day fleet $** (rolling 24h, reusing the `/agent/stats` window machinery). The fleet cap is the essential circuit breaker.
- **Halt = pause-claim.** When over budget we **stop claiming new jobs**; in-flight jobs run to completion (never hard-cancel paid-for work). Resume = raise the cap / the 24h window rolls. Marginal overspend is bounded by what's already running.

**Architecture (load-bearing, baked in):**
1. **One batch-pause primitive, built here, reused by Cluster 5.** Per-batch cap trips `batches.paused_at`/`paused_reason`; `claim_next_job` skips jobs whose batch is paused. C5's `fleet-ctrl-3` (manual pause/resume) reuses the SAME `pause_batch`/`unpause_batch` repo functions — the kill-switch trips them automatically, the operator trips them manually. **Do not invent a second pause mechanism in C5.**
2. **A separate fleet-global gate** (`budget_state` singleton) because batchless `/generate` api jobs (`batch_id IS NULL`) would slip a per-batch-only pause. The fleet cap must stop **all** api claims.
3. **Cheap hot path.** `claim_next_job` reads only cheap booleans (batch paused? fleet paused?) — it never computes cost. A periodic **budget monitor** (alongside `worker._sweep_stuck_jobs`, `worker.py:423`) recomputes the ledgers every `cost_check_interval_seconds` and trips/clears the flags. Pause-claim ⇒ a slightly stale flag only ever lets a few extra in-flight jobs through, never a runaway.
4. **Claim-gate coupling with C3 is signature-level, not just the WHERE list.** New `.where(batch_not_paused).where(fleet_not_paused_for_api)` are added to the existing `content_ok`/`judge_ok`/`extract_ok` chain (merged C3 code at `jobs.py:350-352`, inside the `pick_stmt` at `:345-355`; re-verify before editing — C3 refactored role-resolution via a `_provider_api_ok` helper), AND `claim_next_job` gains a new `fleet_api_paused` parameter (read once at the top of the worker claim loop, like `capabilities`). C3 ALSO changes this function's signature (per-job role resolution). At rebase, reconcile **both the params and the WHERE composition** — additive, never replace. Merge order: C2 → C3 → C4.
5. **Pricing semantics are NOT collapsed.** `cost_usd`'s per-provider cached branch (`_PROMPT_INCLUDES_CACHED`: gemini `prompt - cached`; claude disjoint, `pricing.py:88-97`) stays exactly as-is. `pricing-1` only **adds** a `cache_write` term for claude — a regression test pins both existing branches.

**Verified facts (against code on this tip):**
- `pricing.cost_usd(provider, model, usage)` (`pricing.py:63-97`) — Python (not SQL); the ledger fetches api `agent_usages` rows and sums `cost_usd` in Python.
- `claim_next_job` (`jobs.py:~255-355`, post-C3) ends in `.where(content_ok).where(judge_ok).where(extract_ok).order_by(...).with_for_update(skip_locked=True)` (chain at `:350-352`) — the AND-compose point.
- `agent_usages` (`agent_usage.py:36-55`): `prompt/output/cached/total_tokens` + `raw_envelope` JSONB, `book_id`/`homework_job_id`/`phase_output_id`, `auth_mode`. **No** `cache_creation_tokens`, **no** `batch_id`.
- `batches` (`batch.py:18-38`): no status/pause column today; `CheckConstraint` already imported (C1's 0028).
- claude cache-write lands in `raw_envelope.cache_creation_input_tokens` only; `cost_usd` KNOWN-BIAS comment at `pricing.py:54-58`.
- Migration head `0028_enum_check_constraints`. **C3 takes `0029_judge_status`; C4 merges after C3, so C4's chain starts at `0030`.** C4 adds **three** schema changes, all additive-nullable / data-safe, linearly chained: `0030` cache_creation (Task 1), `0031` batch pause cols (Task 4), `0032` budget_state table (Task 5). **At rebase, run `alembic heads`, re-point each `down_revision` onto the live head, renumber if taken, and verify a single head.** (May be squashed into one migration if the gate prefers — all three are additive.)

**Merge order:** C4 merges **after C3** (HARD — shares `claim_next_job` *signature + WHERE* and the migration chain) **and after C2** (SOFTER — Task 7 extends C2's relaunch-confirm response shape + shared `batch.py`/`jobs.py` rebase conflicts). **NB:** pause-claim does **not** need C2's `set_status` guard — it writes `batches.paused_at`/`budget_state`, never a job status (that was the *hard-cancel* dependency, which we rejected). Rebase onto the live tip before PR; expect trivial `MASTER_MEMORY.md`/`INDEX.md`/`WISHLIST.md` append conflicts.

**Acceptance gate (NO mass-gen — hard money rule):** prove the math + the trip with **cheap, synthetic, or single-call** evidence: (a) one minimal-token claude api call that fires `cache_creation` → assert the column captures it and `cost_usd` adds the 1.25× premium; (b) seed synthetic `agent_usages` rows over a $0.01 test cap → assert the monitor pauses the batch + trips the fleet flag and that `claim_next_job` then skips those jobs while still claiming a cli job. Never ramp real homework generation to "test billing."

---

## Tasks (TDD per task, commit per task — `c4:` prefix)

### Task 1 — `pricing-1a`: capture claude cache-write tokens
- **Migration `0030_agent_usages_cache_creation`** (`down_revision="0029_judge_status"` — confirm at rebase; additive, data-safe): `agent_usages.cache_creation_tokens Integer NOT NULL server_default '0'`. Backfill in the same migration: `UPDATE agent_usages SET cache_creation_tokens = COALESCE((raw_envelope->>'cache_creation_input_tokens')::int, 0) WHERE raw_envelope ? 'cache_creation_input_tokens'`. Downgrade drops the column.
- **Model:** add the column to `app/models/agent_usage.py` (mirror `cached_tokens`).
- **Capture path:** in `app/services/api_transport.py` claude usage mapping (`_claude_usage`, ~`:115-126`) and `app/services/providers/claude.py` `parse_envelope` (~`:88`), surface `cache_creation` into the normalized usage dict; thread it through `agent._record_usage` (`agent.py` `_record_usage`, the `cached_tokens=` neighbour) into the new column.
- **Tests** (`tests/services/test_pricing.py` / `test_agent.py`): (1) migration up→down roundtrip via `alembic` offline check; (2) a claude usage dict carrying `cache_creation` persists to the column through `_record_usage` (mock the DB write boundary, run the real mapping); (3) gemini/cli rows record `cache_creation_tokens=0`.
- **Commit:** `c4: capture claude cache-write tokens into agent_usages (pricing-1a)`

### Task 2 — `pricing-1b`: price the cache-write premium (do NOT collapse semantics)
- **`pricing.py`:** add a `cache_write` rate (= 1.25 × `input`) to each **claude** `PRICE_MAP` entry; in `cost_usd`, after the existing per-provider branch, add `cache_write_cost = int(usage.get("cache_creation_tokens") or 0) * rates.get("cache_write", 0.0) / 1_000_000` and include it in the return. Gemini entries get **no** `cache_write` key → `.get(...,0)` ⇒ zero, untouched.
- **Tests** (`test_pricing.py`): (1) **regression** — gemini `prompt-includes-cached` row bills exactly `(prompt-cached)*input + output + cached*cache_read` (unchanged); (2) **regression** — claude disjoint row with no cache_creation unchanged; (3) **new** — claude row with `cache_creation_tokens=N` adds `N*1.25*input/1e6`; (4) a model with no `cache_write` key never errors.
- **Commit:** `c4: price claude cache-write at 1.25x input (pricing-1b)`

### Task 3 — cost ledger (read side)
- **New `app/repositories/cost.py`:**
  - `async batch_api_cost_usd(session, batch_id) -> float` — `SELECT au.* FROM agent_usages au JOIN homework_jobs j ON j.id=au.homework_job_id WHERE j.batch_id=:b AND au.auth_mode='api'`; sum `pricing.cost_usd(provider, model_name, row_usage)` in Python.
  - `async fleet_api_cost_usd(session, since) -> float` — same over `au.auth_mode='api' AND au.started_at >= :since` (reuses the stats window column).
  - `async section_prior_api_cost(session, book_id, toc_entry_id, transport) -> tuple[float, bool]` — cost + `had_done_job` for the latest done api job of that section (for Task 7).
  - Each builds the usage dict from the row's `prompt/output/cached/cache_creation/total_tokens`.
- **Tests** (`tests/repositories/test_cost.py`, `RUN_DB_INTEGRATION` or a fake-session fixture): seed mixed cli/api usage across two batches → assert per-batch sum counts api only, fleet sum windows correctly, section prior-cost returns the done-job total.
- **Commit:** `c4: cost ledger read functions (per-batch / fleet-day / per-section)`

### Task 4 — batch-pause primitive + claim-gate skip (the shared primitive)
- **Migration `0031_batch_pause_columns`** (additive nullable, data-safe): `batches.paused_at TIMESTAMPTZ NULL`, `batches.paused_reason String(64) NULL`.
- **`app/repositories/batches.py`:** `pause_batch(session, batch_id, reason)`, `unpause_batch(session, batch_id)`, `unpause_by_reason(session, reason)`, `active_batch_ids(session)`.
- **`claim_next_job` (`jobs.py`):** add
  `.where(or_(HomeworkJob.batch_id.is_(None), HomeworkJob.batch_id.not_in(select(Batch.id).where(Batch.paused_at.is_not(None)))))`.
  🔴 **The explicit `IS NULL` arm is REQUIRED:** `NULL NOT IN (non-empty set)` evaluates to SQL `NULL` → the row is excluded, so without it **every** batchless `/generate` job (`batch_id IS NULL`) stops being claimable the instant ANY batch is paused. Batchless api jobs are governed by the **fleet** gate (Task 5), never this one. AND-composes after `extract_ok`.
- **Tests:** (1) a pending job in a paused batch is NOT claimed; after `unpause_batch`, it IS; (2) 🔴 **NULL-arm regression** — a batchless `/generate` job stays claimable while a *different* batch is paused; (3) a job in a non-paused batch claims normally; (4) the existing claimgate tests (content/judge/extract) still pass (AND-composition intact); (5) 🔴 **pause-claim guarantee** — an *in-flight* job whose batch gets paused mid-run completes normally (pause gates claiming only, never cancels — asserts "never hard-cancel paid work", not just implied).
- **Commit:** `c4: batch-pause primitive + claim-gate skip (reused by C5 fleet-ctrl-3)`

### Task 5 — fleet-daily global pause gate (covers batchless api jobs)
- **Migration `0032_budget_state`:** singleton `budget_state` table — `id Integer PK CHECK (id=1)`, `api_paused_at TIMESTAMPTZ NULL`, `api_paused_reason String(64) NULL`; seed the single row in the migration.
- **`app/repositories/budget.py`:** `get_state(session)`, `set_api_paused(session, reason)`, `clear_api_paused(session)`.
- **`claim_next_job`:** compute `job_resolved_api` (reuse the same api-resolution already expressed by `content_ok`/`judge_needs_api`/`extract_needs_api` — a job "spends api" if `transport='api'` OR any resolved role is api). Add `.where(or_(~job_resolved_api, literal(not fleet_api_paused)))`. ⚠️ **`fleet_api_paused` enters `claim_next_job` as a NEW parameter** (read once at the top of the worker claim loop, like `capabilities`) — coordinate this signature change with C3's param change at rebase (see Approach §4). cli-only jobs are never blocked by the fleet cap.
- **Tests:** (1) fleet paused → a batchless api `/generate` job AND a batched api job are both skipped; (2) a cli job still claims while fleet is paused; (3) clear → api jobs resume.
- **Commit:** `c4: fleet-daily global pause gate (batchless-safe circuit breaker)`

### Task 6 — the budget monitor (the kill-switch trip/clear)
- **Config (`app/config.py`):** `cost_cap_batch_usd: float = 0.0` (0 = disabled), `cost_cap_fleet_daily_usd: float = 0.0`, `cost_check_interval_seconds: int = 60`.
- **`worker._budget_monitor_loop`** (new, scheduled next to `_sweep_stuck_jobs` at `worker.py:423` / its scheduler): every `cost_check_interval_seconds`, in one session — for each `active_batch_ids`: `batch_api_cost_usd` > `cost_cap_batch_usd` (and cap>0) ⇒ `pause_batch(reason="batch-cap")`, else `unpause_by_reason` for that batch if it was batch-cap-paused and now under; then `fleet_api_cost_usd(now-24h)` > `cost_cap_fleet_daily_usd` (and cap>0) ⇒ `set_api_paused("fleet-daily-cap")`, else `clear_api_paused()`. Idempotent; never touches manually-paused (C5) batches — only acts on its own `reason` values.
- **Tests:** (1) seed a batch's api usage over a $0.01 cap → monitor pauses it with `reason="batch-cap"`; under → unpauses (only its own reason); (2) seed fleet api usage over the daily cap → `budget_state.api_paused_at` set; under → cleared; (3) caps=0 → no-op.
- **Commit:** `c4: budget monitor trips/clears pauses (pause-claim kill-switch)`

### Task 7 — `fleet-api-4`: never-pay-twice (surface prior spend before a force re-run)
- **Reconcile (design):** resume/retry already reuses `done` phase rows for free (`pipeline.py:149-152`, cross-job extract reuse) — leave it. The gap is a **fresh re-run / `force=True`** over a section that already completed on paid api, which re-bills every phase. Scope here = **detect + warn** (not a hard block; force still works).
- **`batch.launch_batch` (`batch.py:142`) + `/generate`:** when `force` (or a relaunch that would recreate a section) and `section_prior_api_cost(... )` reports a prior done api job, include `prior_api_cost_usd` + `would_rebill: true` per section in the launch **preview/response** so the FE shows "re-running re-bills ~$X" before the user confirms force. Wire alongside Cluster 2's relaunch-confirm (don't duplicate — extend its response shape if C2 landed it).
- **Tests:** (1) force re-launch over a section with a prior done api job → response carries `prior_api_cost_usd>0, would_rebill=true`; (2) a never-generated section → no flag; (3) a cli-prior section → `would_rebill` reflects $0 (cli is free).
- **Commit:** `c4: never-pay-twice — surface prior api spend before force re-run (fleet-api-4)`

### Task 8 — observability + reference-doc de-stale
- **`/agent/stats`** (or new `GET /jobs/batch/{id}/cost`): expose per-batch api $ (via `batch_api_cost_usd`) + `paused_at`/`paused_reason`, and the fleet `budget_state`. Minimal so the operator can answer "what did this batch cost / why is it paused."
- **FE — minimal paused badge, SHIP IN C4 (visibility is part of the safety feature):** a `Paused — budget cap reached (<paused_reason>)` badge/banner on the affected batch, reusing the existing `web/src/lib/ui.ts` badge kit to render the backend `paused_reason` field already shipped (Task 4), plus the Task 7 prior-cost warning copy. **Rationale:** an invisible kill-switch reads as a bug — the operator sees lessons stop with no reason and may force-relaunch (more spend, the opposite of the goal). Only the polished cost-$ dashboard visual defers to C6 (low collision: this is one text badge).
- **De-stale:** `DEPLOY.md` (new `COST_CAP_*` / `COST_CHECK_INTERVAL_SECONDS` env), `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` (budget monitor, cost ledger, the shared batch-pause primitive), `docs/DATABASE.md` (`agent_usages.cache_creation_tokens`, `batches.paused_*`, `budget_state`).
- **Commit:** `c4: cost-safety observability + reference-doc de-stale`

---

## Finish (after suite green + acceptance gate)
- Rebase-check onto `origin/Nggaev-v2` (must be ≥ C2, C3) — `git fetch` + `git log HEAD..origin/Nggaev-v2`; rebase, **renumber migrations to the live `alembic heads`**, re-run suite.
- Worklog **`[0080]`** in `MASTER_MEMORY.md` + INDEX row; `git mv` this plan into `docs/superpowers/plans/shipped/`; close `fleet-api-3`, `fleet-api-4`, `pricing-1` in `WISHLIST.md` (note C5 reuses the batch-pause primitive); reference docs de-staled (Task 8).
- Open PR `[cluster-4] ...`; **do not self-merge** — gatekeeper verifies + merges after C2/C3.
