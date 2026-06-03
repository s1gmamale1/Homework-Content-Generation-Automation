# Job Resilience — Phase Resume + Provider Failover — Design Spec

**Date:** 2026-06-03
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

Make a generation job **survive interruption** without redoing completed work or dying on a
single provider's failure. Two composing mechanisms over one shared foundation:

- **Resume** — on any recovery re-run, skip phases already `done`; re-run only the unfinished/failed ones.
- **Provider failover** — when a phase fails *because its provider failed*, re-run that phase on the next available provider (with identical context), instead of failing the whole job.

Both reconstruct a phase's execution context from persisted `phase_outputs` rows, so they are
two uses of the same capability.

## Why

Today an interruption is maximally costly. Verified behavior:
- A single phase exception fails the **whole job** (the content-phase loop cancels in-flight
  peers and marks the job failed).
- On retry/reclaim the scheduler rebuilds `pending = set(content_phases)` (`pipeline.py:351`)
  with **no filter for `done` phases**, and `create_or_reset` (`pipeline.py:468`) **wipes** each
  phase row — so all content phases regenerate from scratch.
- An orphaned `running` job is only reclaimed after `job_timeout_seconds × 2` = **1 hour**
  (`worker.py:235`, `config.py:40` = 1800), and the startup sweep (`main.py:35-47`) resets stuck
  **books/phase_outputs** rows but **deliberately leaves the job row alone**, so even a restart
  doesn't shortcut the hour.

Live incident (2026-06-03): a Kimyo §1 job completed 8/9 phases, the worker process died
(session limit killed the host server) mid-`reflection`, and the only recovery path is "wait ~1 h,
then re-run all 9 phases." That is the problem this spec removes.

## The two interruption cases (both real, both handled)

- **Case A — worker alive, a single phase's CLI call errors** (e.g. the observed boss-arena
  `"socket connection closed unexpectedly"`, a throttle, or `rc=1`). The process can react in
  real time → **failover** (classify, retry-same or switch provider) in-process; `prior_outputs`
  is already in memory, no reclaim needed.
- **Case B — the worker process dies, job orphaned `running`** (crash, OOM, deploy, kill, or the
  host session dying). Nothing in-process can react → **faster reclaim** flips it back to
  `pending`, then **resume** re-runs only the unfinished phases. If the unfinished phase's
  original provider is still down, the **same failover logic** completes it on a fallback provider.

## Shared foundation

A phase needs three inputs to run: the **lesson extract**, the **prior `done` phases' markdown**
(`prior_outputs`), and its **own prompt**. All persist in the DB (`phase_outputs.output_md`). The
core capability is **"rebuild a phase's context from persisted phase rows."** Resume uses it to
skip-and-re-inject; failover uses it to hand the same context to a different provider.

## Scope

**IN:** (1) phase-level resume, (2) per-phase provider failover with failure classification
(policy b), (3) faster orphan reclaim, (4) per-phase provider attribution.

**OUT (deferred to the autonomous-generation effort, see `docs/PRODUCTION_AUTONOMOUS_GENERATION.md`):**
budget-governor / weekly-cap pacing, per-provider circuit-breaker, observability/alerting. These
are *scale* concerns; this spec is *single-job survival* — the foundation the governor later sits on.

**Separate (not folded in):** the stuck-`pending`-with-attempts-exhausted sweep (WISHLIST,
e.g. `2848dbcb`) stays its own item.

## Design

### 1. Resume — skip `done` phases on recovery re-run

The wave scheduler (`_run_content_phases_parallel`) seeds `pending` from the **live phase rows**
instead of unconditionally from `content_phases`:

- At job start, load existing `phase_outputs` for this job.
- A phase already `status="done"` with non-empty `output_md` is **not** added to `pending`; its
  `output_md` is **pre-loaded into `prior_outputs`** so downstream phases see it as a dependency.
- Only phases that are not `done` (never ran, `pending`, `failed`, or wiped) are scheduled.
- `_execute_phase` keeps using `create_or_reset` **only for phases it actually runs** — done
  phases are left untouched (no wipe).

**Resume vs. force (trigger rule):**
- **Auto-recovery** (worker reclaim / automatic retry) → **resume** (skip done).
- **Explicit user "force / start fresh"** → **full regen** (current behavior; clears all phase
  rows first). The generate endpoint's `force=true` path signals clean-slate.
- Distinguish via a job-level signal (e.g. a `resume` flag derived from "was this a reclaim/retry
  vs. a fresh/forced create"). Exact carrier decided in the plan; semantics fixed here.

### 2. Provider failover — policy (b), classify then act

When a run phase raises, a **failure classifier** maps the CLI result to one of three classes,
and a **failover driver** acts:

| Class | Example signals | Action |
|---|---|---|
| **transient** | `socket connection closed unexpectedly`; `temporarily limiting requests … not your usage limit`; network timeout | **retry SAME provider**, exponential backoff — up to **2** retries (~5s, ~30s); **if both still fail → escalate to failover** (treat as hard) |
| **allocation wall** | 5-hour / weekly cap reached message | **fail over immediately** (retrying same is futile) |
| **hard / unknown** | any other nonzero exit, parse failure, unrecognized error | failover after **1** same-provider retry; **unknown defaults to failover** (safe) |

**Failover order & eligibility:**
- The job's **requested** provider runs the phase **first** (honored).
- On a failover decision, walk `FAILOVER_PROVIDER_ORDER` (config; default
  `[codex, gemini, kimi, opencode]`), **skipping the provider that just failed**.
- **claude is intentionally excluded** from the fallback list — reserved for the user's own
  Claude Max allocation (provider-isolation, per the autonomy doc). A claude job still *tries
  claude first*; it just never *falls back to* claude.
- Each provider gets **one** attempt per phase (no infinite loop). When the requested provider +
  every eligible fallback have each failed, the phase **genuinely fails** (→ job fails, recoverable
  later by resume).
- `FAILOVER_PROVIDER_ORDER` is config so the list can change without code (e.g. drop `opencode`
  once verified, or reorder when the budget-governor lands).

**Extract is special — NOT on the generic chain.** The `extract` phase is pinned to
`settings.extract_provider` (gemini) and has hard input constraints (kimi has **no** native PDF
support; gemini rejects PDFs **>20 MB** — both per CLAUDE.md). The generic
`[codex, gemini, kimi, opencode]` order would fail an extract over to a provider that *cannot read
the PDF* → garbage or hang. So **extract does NOT cross-provider failover**: transient-retry only on
its pinned provider; on a wall/hard failure the job fails (extract is cheap + cross-job-cached, so
re-doing it later is fine). A PDF-capable fallback, if ever wanted, needs its own PDF-capable list —
never the generic chain.

**Per-attempt timeout (bounds the `opencode` hang).** `opencode` is **unverified** (WISHLIST) and
its known failure mode is a **stdin/positional hang**, not a clean `rc=1`. A hang in the failover
loop would tie up the slot until `job_timeout` (~30 min) — worse than a quick fail. So **every
provider attempt inside the failover loop gets its own bounded timeout** (`per_attempt_timeout_seconds`,
e.g. 8–10 min): a hung/slow attempt is killed and the loop moves to the next provider, instead of
stalling the whole phase. opencode stays the **last** slot and is not a reliable rescue until
smoke-tested.

**Backoff must release the concurrency slot.** A transient retry's backoff sleep must **not** hold
the `gemini_max_concurrency` subprocess semaphore idle — sleep *outside* the held slot — so a burst
of concurrent throttles doesn't starve the subprocess pool with sleeping-but-slot-holding calls.

**Per-phase failover budget vs. job `attempts`:** failover retries happen **within a single phase
execution** and must **not** consume job-level `attempts` (which guards whole-job retries). The
phase-level attempt budget is bounded by (1 requested + same-provider transient retries +
one-per-eligible-fallback).

### 3. Faster orphan reclaim — REQUIRES a claim heartbeat

> **⚠ Corrected 2026-06-03 (review):** the first draft proposed `reclaim_stale_seconds=300`
> decoupled from `job_timeout`. **That is a duplicate-execution bug.** `claimed_at` is set once at
> claim (`jobs.py:231`) and **never refreshed** — there is no heartbeat. The current
> `job_timeout × 2` (= 3600s) window is safe *only because* it exceeds the max alive runtime: a
> worker times its own pipeline out at `job_timeout` (=1800s, `worker.py:177`), so a healthy job's
> `claimed_at` can never get older than 1800s. Any `reclaim_stale_seconds < job_timeout` would
> reclaim a **still-running** job mid-flight → two workers execute the same job, `create_or_reset`
> clobbers shared phase rows, and `attempts` burns on every false reclaim (the ~17-min Kimyo smoke
> would be falsely reclaimed ~3×).

Two independent recovery paths; only the periodic one needs the heartbeat.

- **(a) Startup sweep — safe with NO heartbeat.** The `main.py` boot sweep **also resets orphaned
  `running` jobs → `pending`** (today it only fails books/phase_outputs). On restart there are no
  live workers, so resetting jobs is unconditionally safe and recovers a restart-killed job
  **immediately** (resume then picks up where it left off). This alone covers the user's incident
  (host/session death → restart).

- **(b) Periodic short-window reclaim — ONLY safe with a claim heartbeat.** Add a heartbeat: the
  worker refreshes `claimed_at = now()` every `heartbeat_seconds` (~30s) while `pipeline.run`
  executes (a small async task running alongside the pipeline). `reclaim_stale_seconds` then becomes
  a true **lease TTL** — set it to a small multiple of the heartbeat (e.g. `heartbeat_seconds=30`,
  `reclaim_stale_seconds=120` = 4 missed beats). A live worker keeps its claim fresh; only a **dead**
  worker lets the lease lapse. `reclaim_stale_seconds` is tied to the **heartbeat interval, NOT** to
  `job_timeout`, so it can be minutes regardless of how long a healthy job legitimately runs.

- **Without the heartbeat, do NOT shorten the window.** If the heartbeat is descoped, keep periodic
  reclaim at `> job_timeout` (the current `× 2`) and ship only path (a). The heartbeat is the
  prerequisite that makes fast *periodic* reclaim correct.

### 4. Per-phase provider attribution

- Add `phase_outputs.provider` (today the row carries only `model_name`). Record the provider that
  **actually produced** each phase, so after failover the truth is auditable and the resume +
  UI/badge logic is clean.
- `agent_usages` already records provider per CLI call; `phase_outputs.provider` is the
  phase-level rollup of "who finally produced this."
- Job-level provider remains the **requested** provider (the badge); per-phase rows show any
  fallback. This relaxes the old "one provider per job" invariant deliberately — attribution stays
  accurate at the phase/call grain.

## Components touched (interfaces, not line edits)

- `app/services/pipeline.py` — scheduler seeds `pending`/`prior_outputs` from live phase rows
  (resume); per-phase failover loop wrapping the run call; record per-phase provider.
- `app/services/failure_classifier.py` (new) — pure `classify(cli_result) -> "transient" |
  "wall" | "hard"` from exit code + stderr/result snippet. Deterministic, unit-testable.
- `app/services/agent.py` — surface enough of the CLI failure (exit code + error snippet) for the
  classifier; `run_phase_prompt` accepts a per-call provider override (the failover driver picks it).
- `app/services/worker.py` + `app/config.py` — **claim heartbeat** (refresh `claimed_at` every
  `heartbeat_seconds` while `pipeline.run` runs) + `reclaim_stale_seconds` as a **lease TTL**
  (`> heartbeat_seconds`, NOT tied to `job_timeout`); reclaim uses it; `per_attempt_timeout_seconds`
  for the failover loop.
- `main.py` — startup sweep **also resets orphaned `running` jobs → `pending`**.
- `app/models/phase_output.py` + migration — `phase_outputs.provider` (additive).
- `app/repositories/phase_outputs.py` / `jobs.py` — load-for-resume query; provider write; a
  heartbeat `UPDATE claimed_at=now WHERE id=…` (touch-claim); reclaim query already exists.

## Data flow

**Case A (worker alive):**
1. Phase runs on requested provider → CLI errors → `classify()`.
2. transient → backoff + retry same; wall/hard → next eligible provider from order.
3. Phase succeeds on some provider → `phase_outputs` row `done` with `provider` recorded → job
   continues. All providers exhausted → phase fails → job fails (recoverable).

**Case B (worker died):**
1. Job orphaned `running`. Recovery: **immediately on restart** via the startup sweep, or — once the
   heartbeat lands — via the periodic **lease-TTL** reclaim (`reclaim_stale_seconds`, a small
   multiple of the heartbeat; safe because a live worker keeps `claimed_at` fresh) → `pending`.
2. Re-claimed → `pipeline.run()` → scheduler loads phase rows → done phases skipped + re-injected
   into `prior_outputs` → only unfinished phases scheduled.
3. The unfinished phase runs; if its original provider is still down, Case-A failover completes it
   on a fallback provider.

## Testing strategy

- **Failure classifier** — pure unit tests: each known signal → correct class; unknown → `hard`.
- **Failover driver** — unit test the order walk (requested-first, skip-failed, one-per-provider,
  exhaustion → raise) with a stubbed runner; assert claude never appears as a fallback.
- **Resume seeding** — unit/iso test that, given phase rows where some are `done`, the scheduler
  schedules only the not-done ones and pre-loads done `output_md` into `prior_outputs`.
- **Reclaim + heartbeat** — startup sweep flips orphaned `running` → `pending`; the heartbeat keeps a
  **live** job's `claimed_at` fresh so it is **never** reclaimed mid-run, while a job whose heartbeat
  lapsed (dead worker) **is** reclaimed after `reclaim_stale_seconds`. The failover loop honors
  `per_attempt_timeout_seconds`. (DB-free at signature/logic level; real round-trip for the migration.)
- **Attribution** — `phase_outputs.provider` persists the producing provider; additive migration
  up/down round-trip.
- **Acceptance (CLI smoke)** — (a) kill a job mid-run, confirm reclaim + resume re-runs only the
  unfinished phase; (b) force a provider failure on one phase (e.g. bad model/temporary block),
  confirm failover completes it on the next provider and `phase_outputs.provider` reflects it.
  Per CLAUDE.md, generation-affecting behavior is proven by a real run.

## Risks / open items

- **Classifier signal strings** are refined against real CLI stderr during the build (the table
  above is the starting set); unknown-defaults-to-failover keeps it safe meanwhile.
- **`force` vs `resume` carrier** — the exact job-level signal that distinguishes a fresh/forced
  generate from a reclaim/retry is finalized in the plan; the *semantics* are fixed here.
- **`opencode` unverified** — placeholder fallback slot until smoke-tested; bounded by
  `per_attempt_timeout_seconds` so its hang can't stall a phase (accepted).
- **Faster reclaim depends on the heartbeat** (review-corrected): without the claim heartbeat,
  `reclaim_stale_seconds` MUST stay `> job_timeout` or live jobs are reclaimed mid-run (duplicate
  execution). The heartbeat is the prerequisite; if descoped, ship only the startup-sweep path.
- **Multi-provider-per-job** is an accepted, intended consequence (attribution moves to the
  phase/call grain; job badge = requested provider).

## Out of scope (restated)

Budget-governor / weekly-cap pacing, per-provider circuit-breaker, observability/alerting (all →
autonomous-generation effort); stuck-`pending`-attempts-exhausted sweep (separate WISHLIST item).
