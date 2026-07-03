# Remediation Clusters — Implementer Brief (2026-06-18)

> **Purpose:** turn the open WISHLIST/ROADMAP backlog into ~6 coherent, plan-sized
> workstreams so the implementer can write each plan fast and start once approved.
> Verified against tip `d0306a2`. Each cluster = **one** plan (opens with `## Approach &
> key decisions`, TDD-per-task, commit-per-task) → user approves → subagent-driven exec →
> PR → gatekeeper verifies+merges. **Do NOT ship the whole backlog in one branch** (review
> becomes a rubber-stamp; the clusters collide on `pipeline.py`/`jobs.py`/`agent.py`).
>
> **Standing rules for every cluster** (CLAUDE.md): TDD per task; a NEW function MUST have a
> test that runs its REAL body (mock only the I/O boundary, never the function under test);
> stage only the files the task lists (other sessions commit to this branch); anything that
> touches generation needs a **real generation smoke over the production transport (api/SDK today)** at the acceptance gate; finish = worklog +
> INDEX row + plan `git mv` to `shipped/` + WISHLIST/ROADMAP closes + reference-doc de-stale;
> rebase-check on `origin/Nggaev-v2` before PR. Each cluster's worklog ID = next free at
> the time (verify against the live tip — parallel branches collide on IDs).

## Sequencing & parallelism

- **Wave 1 (now):** Cluster 1 (quick-win hardening) ‖ Cluster 2 (cancel/resume) ‖ Cluster 3 (judge quality) — mostly disjoint files, safe to run in parallel **via git worktrees**.
- **Wave 2:** Cluster 4 (cost safety) then Cluster 5 (fleet scale) — both layer on areas Waves 1 touch; do after.
- **Wave 3 / as-unblocked:** Cluster 6 (Notion + FE) — FE half blocked on a brainstorm.
- **Out of scope (not code / decision-blocked):** R19 (operator replaces PDFs — user), `fleet-api-1/2/5` (user scoped to Anthropic+Gemini), `opencode` flakiness (keep-as-fallback note), `fe-redesign` until its brainstorm resumes.

File-collision map (why ordering matters): `pipeline.py` is touched by clusters 2,3,4,5; `jobs.py`/`set_status` by 2,4; `agent.py` by 1,5; `worker.py` by 3,5; `phase_judge.py` by 3. Keep each cluster's edits to its lane; if two run in parallel, rebase the second before PR.

---

## Parallel-run conventions (so the 6 clusters never get confused)

**All 6 are being run at once in isolated git worktrees.** Use these fixed labels everywhere (branch, worktree dir, worklog, commits, PR title) so each cluster is unambiguous end-to-end. Do NOT improvise names or grab "next free" worklog IDs — that is the #1 thing that causes confusion and collisions.

| Cluster | Branch name | Worktree dir | **Pre-assigned worklog ID** | Commit prefix |
|---|---|---|---|---|
| 1 — Quick-win hardening | `cluster-1-hardening` | `../HCGA-c1-hardening` | **0077** | `c1:` |
| 2 — Cancel/resume correctness | `cluster-2-cancel-resume` | `../HCGA-c2-cancel-resume` | **0078** | `c2:` |
| 3 — Judge quality | `cluster-3-judge-quality` | `../HCGA-c3-judge-quality` | **0079** | `c3:` |
| 4 — Cost safety | `cluster-4-cost-safety` | `../HCGA-c4-cost-safety` | **0080** | `c4:` |
| 5 — Fleet scale | `cluster-5-fleet-scale` | `../HCGA-c5-fleet-scale` | **0081** | `c5:` |
| 6 — Notion + FE | `cluster-6-notion-fe` | `../HCGA-c6-notion-fe` | **0082** | `c6:` |

**Rules for parallel work:**
- **Worklog ID is reserved, not first-come.** Each cluster writes ONLY its pre-assigned `## [00NN]` block in `MASTER_MEMORY.md` + its INDEX row. Because all 6 touch those two shared files, **expect a merge conflict in `MASTER_MEMORY.md`/`INDEX.md` on every PR after the first** — it's trivial (append-only, keep both blocks), but rebase + re-add before each merge. Same for `WISHLIST.md`/`ROADMAP.md` close-out edits.
- **Cut each branch off the CURRENT `origin/Nggaev-v2` tip** (`git worktree add -b cluster-N-... ../HCGA-cN-... origin/Nggaev-v2`), not off another cluster's branch.
- **Plan file naming:** `docs/superpowers/plans/2026-06-18-cluster-N-<slug>.md` — cluster number in the filename so the 6 plans are distinguishable in one dir.
- **Stage only your cluster's lane files** (CLAUDE.md rule) — never `git add -A`; the worktrees are isolated but the shared backlog/worklog files are the trap.
- **PR title prefix:** `[cluster-N]` so the gate queue is self-labelling.

**⚠️ Migration coordination (prevents alembic multi-head — the #1 merge-back breakage).** Clusters 1, 3, 4 EACH add a migration off the same head `0027`; if they all use `0028`/`down_revision="0027"` you get multiple heads and `alembic upgrade head` fails. Rules:
- **Number + `down_revision` are finalized at rebase-before-merge time = (current alembic head + 1), NOT at branch time.** Pre-assignment is a hint; the live head is the truth. Whoever merges second/third rebases their migration onto the new head and renumbers.
- **Descriptive filenames so files never clash even if numbers shift:** C1 `*_check_constraints.py`, C3 `*_judge_status.py`, C4 `*_cache_creation_tokens.py`. Suggested order C1=0028 → C3=0029 → C4=0030.
- C2 has no migration; C5/C6 (when run) add their own — same rule applies.
- The gatekeeper verifies `alembic heads` shows a SINGLE head after each migration-bearing merge.

**Claim-gate composition:** C3 (`judge-claimgate-1`) and C4 (cost kill-switch) BOTH modify `jobs.claim_next_job` — the second to merge must COMPOSE its WHERE clause with the first's, not replace it. Gatekeeper checks both gates are present post-merge.

**Merge-ORDER the gatekeeper will enforce (dependencies, not just per-PR gating):**
1. **Cluster 2 before Cluster 4** — c4's kill-switch halt rests on c2's `cancel-race-1` `set_status` guard; merging 4 first builds on the unguarded version.
2. **Cluster 1's dead-knob fix before Cluster 5's token-bucket** — c5 layers fleet-wide concurrency on top of c1's "read the right knob"; merging 5 first re-tangles the knob.
3. **Cluster 3's judge work before Cluster 3's R20** if R20 is split into its own branch (R20 consumes the fidelity check).
Everything else can merge in any order (rebase-on-tip each time). If a dependency PR isn't ready, hold the dependent one at the gate rather than merge out of order.

**Decision-blocked clusters (will stall mid-plan until you answer — see each cluster's "Open decision to lock first"):** Cluster 4 (cost-cap granularity + halt behavior), Cluster 5 (Redis-vs-Postgres for bucket+bus), Cluster 6 (resume the fe-redesign brainstorm; its FE half can't be planned yet — do the Notion half R15/R16 only until then).

---

## Cluster 1 — Quick-win hardening — ✅ SHIPPED [0077] (low-risk, mostly mechanical)

**Why:** small, high-value, mostly independent fixes; clears noise so the bigger clusters stand alone. Can be one plan with ~8 tasks. No generation impact except where noted → unit tests suffice (no CLI smoke).

**Items & exact work:**

1. **`dep-cve-1`** — bump `pypdf` (6.10.2 → ≥6.13.3), `python-multipart` (0.0.27 → ≥0.0.31), `starlette` (1.0.0 → ≥1.3.1) in `pyproject.toml`; `uv lock`; `cd web && npm audit fix` (react-router). **Verify:** `uv run pip-audit` clean on those three; full `pytest` green (starlette bump can shift behavior — run the whole suite); `tsc` + `npm run build`. Watch the starlette bump for FastAPI-compat breakage (check the installed FastAPI's pin).
2. **`judge-timeout-1`** — `phase_judge.judge()` (called `pipeline.py:861`,`:896`) has no timeout; generation uses `asyncio.wait_for(..., timeout=settings.per_attempt_timeout_seconds)` (`pipeline.py:601-603`). Wrap the judge call the same way; on `TimeoutError` treat as `judge-unavailable` (same path as a judge CLI error), **not** a job kill. Test: a judge stub that sleeps → asserts unavailable, job still completes.
3. **`concurrency-knob-1` (read-the-right-knob half only)** — `agent.py:203` builds the semaphore from `settings.gemini_max_concurrency`; `agent_max_concurrency` (`config.py:62`) has zero readers. Switch the semaphore to read `agent_max_concurrency`, make `gemini_max_concurrency` an alias/deprecated-fallback (read `agent_max_concurrency` if set else the old name, so existing `.env`s don't break), update the config comments. **De-stale `docs/DEPLOY.md`** (it currently documents `GEMINI_MAX_CONCURRENCY` as live — flip once code flips). The token-bucket/fleet-wide cap is **Cluster 5**, NOT here. Test: monkeypatch settings → assert semaphore size.
4. **`db-check-constraints-1`** — add `CheckConstraint`s (or PG enums) for `homework_jobs.status`, `transport`, `extract_transport`, `judge_transport` (and `batches` equivalents). New Alembic migration (next number after `0027`; verify head with `alembic heads`). **Online-safe:** add as `NOT VALID` then `VALIDATE` if you want zero-lock, or accept a brief lock on these small tables. Test: insert a bad status → `IntegrityError`.
5. **`test-hygiene-1`** — 4 modules (`tests/api/test_cancel_endpoint.py`, `test_retry_cancelled.py`, `test_notion_router.py`, `test_from_notion.py`) set module-level `app.dependency_overrides[get_current_user]` and never clear it → global auth bypass leaks across the suite (order-dependent). Convert each to a fixture with teardown (or an autouse `conftest` that clears overrides). Test: the existing `test_401_without_token` passes without the local monkeypatch workaround.
6. **`extract-2`** — blank `EXTRACT_PROVIDER=` in `.env` → `get_provider("")` KeyError (pydantic reads `""` as a value, not absence). Add a config validator mapping blank→default. Test: settings with `extract_provider=""` resolves to the default.
7. **`flow-1`** — `pipeline.py:461` "Phase scheduler stuck" diagnostic uses literal double-brace `{{p: ...}}` in an f-string → renders static text. Fix the interpolation. Cosmetic; unit-test the message format if cheap, else leave covered by review.
8. **Stale-pending sweep** — a job left `pending` with `attempts >= max_attempts` is never claimed (claim WHERE skips it) and never failed (`mark_failed_with_retry` only runs for *claimed* jobs). Add a startup/periodic sweep (extend `main.lifespan` orphan sweep or `reclaim_stuck_jobs`) that marks `pending` + attempts-exhausted rows `failed`. Test: seed such a row → sweep → status `failed`.

**Approach header seed:** "Batch of independent hardening fixes; each its own task+commit+test; the only schema change is the CHECK-constraint migration (online-safe); DEPLOY.md de-staled with the knob fix; no generation path changes so unit tests are the proof."

**Open decision to lock first:** none — all mechanical.

---

## Cluster 2 — Cancel / resume correctness — ✅ SHIPPED [0078] (all touch jobs+pipeline+launcher)

**Why:** a real data-loss bug + an unreliable cancel + the genuinely-missing batch resume — one coherent lane. Found live cancelling the algebra-g9 batch.

**Items & exact work:**

1. **`cancel-race-1` (do FIRST — others depend on reliable cancel)** — `jobs.set_status` assigns `job.status = status` with **no terminal guard**; the pipeline rewrites `status='running'` on every phase transition (`pipeline.py:676`, `current_phase=phase_name`); the worker only samples for `cancelling` every `settings.heartbeat_seconds` (30s, `worker.py:321`). So a one-shot `running→cancelling` (`request_cancel`) gets clobbered back to `running` before the heartbeat catches it → CLIs keep running. **Fix (pick one, recommend the guard):** make `set_status` (and `phase_repo.set_status`) refuse to overwrite a terminal/cancelling status — e.g. SQL `UPDATE ... WHERE status NOT IN ('cancelling','cancelled','done','failed')`, or guard in the ORM path. **Caution:** `set_status` has many callers (`pipeline.py:141,239,268,...`) — enumerate them; the guard must not break legitimate `pending→running→done`. Test: write `cancelling`, then call the pipeline's `running` set → assert it stays `cancelling`.
2. **`fleet-relaunch-dataloss-1`** — a no-force batch launch calls `find_active_for_section` which matches only `pending`/`running`/`done` (`jobs.py:92`); a `failed`/`cancelled` section falls through to `jobs_repo.create` (`batch.py:158`) → brand-new job, **saved phases discarded + re-billed**, silently. **Interim fix (this cluster):** when a launch would recreate any failed/cancelled section that has persisted phase output, return a count so the FE can show a **2-step confirmation** ("this discards N partially-generated lessons and re-bills them" → confirm again) before proceeding. (The *real* fix is resume — item 3.) Test: batch launch over a cancelled-with-phases section → response flags it; FE shows confirm.
3. **`fleet-ctrl-1`** — Batch **Cancel-all**: `POST /jobs/batch/{id}/cancel` that, in one call, `pending→cancelled` (atomic, `cancel_if_pending` semantics) + `running→cancelling` (`request_cancel`) for every non-terminal job in the batch. Depends on item 1 (else the running ones bounce). Test: mixed-state batch → all non-terminal transition correctly.
4. **`fleet-ctrl-2`** — Batch **Resume failed/cancelled**: `POST /jobs/batch/{id}/resume` that loops the existing per-job retry over every `failed`+`cancelled` job. **The resume mechanism already exists** — per-job `retry` reuses `done` phase rows (`pipeline.py:149-152,175`); only the batch trigger is missing. FE: a "Resume failed/cancelled" button that appears when the batch has any failed/cancelled jobs, **distinct** from "Launch remaining" (creates fresh) and "Re-run all" (force). **Also:** relocate "Re-run all" (`web/src/components/fleet/launcher.tsx:841`, `force:true`) behind a danger affordance so it can't be misclicked next to Launch. Test: batch with failed+cancelled+done → resume re-enqueues only the non-done, reuses done phases.

**Approach header seed:** "Cancel reliability is the keystone (guard `set_status`); on top of it, batch cancel-all and batch resume reuse the existing per-job primitives; the relaunch data-loss bug gets an interim 2-step confirm until resume supersedes it. Order: guard → cancel-all → resume → relaunch-confirm."

**Acceptance gate:** a real end-to-end live run — launch a small real batch, cancel-all mid-flight (assert CLIs die + done phases preserved), then resume (assert only unfinished phases re-run). This also closes `cancel-1`(a) and `fleet-test-4`.

**Open decision to lock first:** confirm the 2-step-confirm UX copy + whether "Re-run all" moves to a kebab menu vs a confirm-only gate.

---

## Cluster 3 — Judge quality — ✅ SHIPPED [0079] (campaign-critical)

**Why:** without source-fidelity + a regression signal, mass-gen ships unverified content. Highest campaign value.

**Items & exact work:**

1. **`judge-fidelity-1`** — `phase_judge._build_judge_prompt(*, contract, output_md)` (`phase_judge.py:75`) never includes the source text; `lesson_context` is a `judge()` param (`phase_judge.py` `judge(..., lesson_context=...)`) but isn't threaded into the prompt. **Fix:** feed `lesson_context` into the judge prompt (grade fidelity-to-source, not just format) + a deterministic key-fact/number cross-check. **Generation-affecting → real CLI smoke required:** prove (a) a phase with an invented fact now gets flagged, (b) a faithful phase still passes. Watch token cost — `lesson_context` can be large; consider a bounded excerpt.
2. **`judge-softfail-1`** — on a judge error or failed regen the phase still completes `done` keeping the rejected output + warnings (`pipeline.py:918-922`); regen triggers only on MAJOR (`pipeline.py:869`) and commits on *generation* success without re-checking the post-regen verdict. **Fix:** add a queryable `phase_outputs.judge_status` (`ok`/`major_shipped`/`unavailable`) — new column + migration; retry the judge once on transient error; make the regen cap configurable; re-check the post-regen verdict. Test: each judge_status path; post-regen still-MAJOR is recorded not silently accepted.
3. **`judge-claimgate-1` (separable — could be its own small plan)** — per-job judge provider ignored by the claim gate. The worker computes `CAPABILITIES` once at startup from `settings.judge_provider`/`judge_model`/`extract_provider` (`worker.py:56` `_compute_capabilities`, flags `:74`/`:77`, `CAPABILITIES` global); `jobs.claim_next_job` (`jobs.py:230`) gates `judge_api_ok`/`judge_pair` (`jobs.py:269`) on those settings-derived caps — never reading the job's `judge_provider` column, though execution honors it (`pipeline.py:104`→`resolve_judge`,`:858`). So a `judge_transport=api` gemini-judge job is gated as needing a *claude* key → strands. **Fix:** thread the job's `judge_provider`/`judge_model` (+ by symmetry `extract_provider`/`extract_model`) into the claim gate; evaluate per-role caps against the JOB's role when set, fall back to settings only when null; **audit every `settings.judge_*`/`settings.extract_*` read for the same drift** (the user's standing principle: a per-job pick overrides `.env` on EVERY path). Touches `worker.py` cap compute + `jobs.claim_next_job` SQL + the existing null-model claim tests. Test: an api-gemini-judge job is claimed by a Vertex-only worker with no ANTHROPIC key.
4. **`R20` golden-eval harness (DESIGN-FIRST — biggest piece)** — frozen `(subject, lesson)` set with known source text → generate → judge/rubric-score (incl. the fidelity cross-check from item 1) → diff vs committed baselines → gate prompt/model-change PRs. This needs a **design** in the plan's approach header (where the set lives, how baselines are stored/diffed, CI vs manual). Consider making R20 its own follow-on plan after 1–3 land, since it consumes their outputs.

**Approach header seed:** "Source-fidelity is the core fix; judge_status makes grading observable; claimgate enforces per-job overrides at the one gate that strands jobs; golden-eval is the regression harness built on top. Claimgate and golden-eval are separable into their own plans if scope is too big."

**Open decision to lock first:** (a) is R20 in-scope for this plan or a follow-on? (b) golden-set size + where baselines live (repo vs DB) — needs your call.

---

## Cluster 4 — Cost safety — ✅ SHIPPED [0080] (campaign-critical)

**Why:** mandatory before any paid mass-gen — today nothing reads `cost_usd` to gate spend, and a re-run silently re-bills.

**Items & exact work:**

1. **`fleet-api-3` Cost ledger + kill-switch** — today `cost_usd` (`pricing.py:63`) is computed only for the read-only `/agent/stats` (`jobs.py:449`). **Fix:** a per-batch (and/or per-provider) $ ledger + a configurable cap that **halts claiming** when exceeded — teach `claim_next_job` (`jobs.py:230`) to skip batches/over-budget work (ties to the `fleet-ctrl-3` pause gate). Design needed: where the running total lives (a `batches` rollup column vs computed-on-read), and the halt mechanism.
2. **`fleet-api-4` Never-pay-twice** — idempotency so a re-run lesson doesn't re-bill. Note this overlaps the resume work (Cluster 2) and the `find_active_for_section`/`done`-reuse path; design must reconcile "resume reuses done phases" (already free) with "don't re-bill a fresh re-run."
3. **`pricing-1` claude cache-write billing** — `agent_usages` has no `cache_creation_tokens` column (claude provider parses `cache_creation_input_tokens` into `raw_envelope` only), so the api `$` under-reports the 1.25× cache-write premium. Add column + migration + backfill from `raw_envelope` + price it in `cost_usd`. Known-bias note already in `pricing.py:55`. **Do NOT collapse the per-provider cached-token semantics** (gemini `prompt` INCLUDES cached; claude disjoint) — CLAUDE.md hard rule.

**Open decisions to lock first:** cap granularity (per-batch $ vs per-day fleet $), and halt behavior (pause-claim vs hard-cancel). These are product calls — get them from the user before planning.

---

## Cluster 5 — Fleet scale — 🟡 PARTIAL (P1 [0081] + capability-gate [0085] + resilience trio + fleet-net code-half [0089] SHIPPED; OPEN: token-bucket Phase 2 [blocked on Vertex quota], sse-multipod-1, ops-half configs)

> **Campaign-readiness ordering note:** C5 (throughput/safety) + C7 (judge) come **first**, then C6/C8 for the all-subjects run, C9 last.

**Why:** real fleet-wide throughput + observability; not blocking single-PC use today.

**Items & exact work:**

1. **`concurrency-knob-1`** — Phase 1 (reactive 429 backoff) **✅ SHIPPED 2026-06-25 (`90b714d`/`c064040`)**: `agent._spawn` now retries a transient 429 with exponential backoff+jitter, no slot held. **Phase 2 (proactive token-bucket) STILL OPEN:** the cap is still per-process (module-global semaphore, `agent.py`), so N workers = N×cap → Vertex over-quota when both PCs share one project. **Fix:** a shared token-bucket per `(provider, model)` (Postgres-native per resolved decision below). **Blocker: needs the real Vertex quota numbers** (per-model concurrent-call limits) to size the bucket.
2. **`sse-multipod-1`** (OPEN) — `events_bus._subscribers` is an in-process dict (`events_bus.py:5`); separate worker pods publish to their own process → browser on the API pod sees only the initial DB replay then a frozen stream. **Fix:** back the bus with Postgres `LISTEN/NOTIFY` (no new infra).
3. **`fleet-ctrl-3` Pause/Resume a batch** — **✅ SHIPPED 2026-06-19 (Cluster 5 / P1, worklog [0081], PR#40):** `POST /jobs/batch/{id}/pause|unpause` (reason "manual") + FE, layered on C4's batch-pause primitive.
4. **`fleet-ctrl-4` PC-level drain** — **✅ SHIPPED 2026-06-19 (Cluster 5 / P1, worklog [0081], PR#40):** `POST /workers/{pc_id}/drain`, worker self-drains on its registry beat.
5. **`fleet-restart-reclaim-1` peer-aware reclaim** — **✅ SHIPPED 2026-06-19 (Cluster 5 / P1, worklog [0081], PR#40):** lease-aware `reclaim_orphans_on_startup` (reset-all only when no live peer registered, else lease window) so a head restart no longer double-runs a peer's jobs.
6. **`fleet-infra-1/2`** (OPEN) — PgBouncer + hardened head; worker discovery file / movable head (≤60s reconnect). Mostly ops/deploy, not app code.

**Open decisions to lock first:** ~~Redis-or-Postgres for both the token-bucket and the event bus~~ **RESOLVED 2026-06-19 (user): Postgres-native — no Redis.** Build the token-bucket as a DB table (row-lock / atomic per-`(provider, model)`-per-window check in the spawn path) and the event bus on Postgres `LISTEN/NOTIFY`. Rationale: the fleet is 2–3 PCs (nowhere near a throughput regime that needs Redis), and the system's design invariant is "Postgres is the only moving part" — adding Redis means a new service to install/secure/keep-alive on every machine (and there's already an un-reverted LAN-exposed PG to clean up — don't add a second exposed service). Revisit only if a measured Postgres bottleneck appears at dozens of workers.

### Campaign-readiness additions (2026-06-26)

> New open items surfaced by the live grade-8 / gemini-API runs (all logged in `WISHLIST.md` — codes only here, no re-paste).

- **`launcher-capability-gate-1`** — **✅ SHIPPED 2026-06-26 (worklog [0085], PR #41).** Workers publish a `{cli,api}` capability blob on heartbeat; head unions over online workers; `/agent/models` `fleet` block; FE greys unservable `(provider × transport)` picks + offline banner.
- **`fleet-net-1`** — **CODE HALF ✅ SHIPPED 2026-06-26 (worklog [0089], cluster-5-fleet-resilience):** `agent._spawn` retries transient connection/DNS errors (DNS/`getaddrinfo`/`HTTPSConnectionPool`/`WinError`/timeout) with the existing 429 backoff. **OPS HALF STILL OPEN:** wired Ethernet + stable resolver on Oliver + NTP-align both clocks (ties `fleet-ops-2`) — host-side, not app code.
- **`pg-hba-ipv6-1`** — IPv6 link-local DB connections rejected (`no pg_hba.conf entry for host "fe80::…"`). Pin Oliver's `DATABASE_URL` to literal IPv4 `192.168.1.15`, or add an IPv6 `host` line to `pg_hba.conf`.
- **`fleet-limited-worker-hogs-1`** — **✅ SHIPPED 2026-06-26 (worklog [0089], cluster-5-fleet-resilience).** Vacuum killed both ways: switch → job no longer fails; pause → limited worker self-cooldowns (skips claiming until reset) + requeued job is claimable-now for a healthy peer.
- **`fleet-session-limit-autopause-1`** — **✅ SHIPPED 2026-06-26 (worklog [0089], cluster-5-fleet-resilience): BOTH strategies behind the per-batch `session_limit_strategy` flag (env default + override).** ~~classify the Claude session-limit error + parse its stated reset time, return the job to `pending` (don't terminally fail), stop claiming until reset, auto-resume. **DECISION LOCKED (user, 2026-06-26): ship BOTH behaviors behind a per-batch launch/config flag — operator chooses per batch:**~~ (a) *pause-and-wait* — parse the reset time → return the job to `pending` → stop claiming on that worker until reset → auto-resume; (b) *switch-model* — swap the limited role to a non-limited model (e.g. extract `claude-opus`→`gemini-2.5-flash`) and keep generating (no stall, mixes models within the batch). Builds on the C5/P1 pause primitive + Phase-2 backoff.

> **Design-together note (verified 2026-06-26):** `fleet-limited-worker-hogs-1` and `fleet-session-limit-autopause-1` overlap heavily — both are "host is rate/session-limited → that worker must STOP claiming (not vacuum + insta-fail the queue)." Plan them as **one** workstream: hog-prevention is the general case (back off claiming when limited), session-limit auto-pause is the specific Claude case (classify + parse reset + auto-resume). Together with `fleet-net-1` (extend the 429 backoff to transient connection/DNS errors) these three are the tightest independently-shippable, campaign-protecting C5 sub-cluster — and the genuinely campaign-critical app code in C5 (the rest of C5 is the shipped P1 control plane or pure ops).

**Blockers:** Phase-2 token-bucket (`concurrency-knob-1`) needs the **Vertex quota numbers**; `fleet-session-limit-autopause-1` only bites the campaign **if claude-CLI extract is used** (gemini-flash extract sidesteps it).

---

## Cluster 6 — Notion + Frontend — 🟡 PARTIAL (R15/notion-archive-1 [0086] SHIPPED; OPEN: R16, crawl-resolve, validator, fe-redesign, fleet-ui-2/3/4)

**Items & exact work:**

1. **`notion-archive-1` (R15)** — `archive_job`'s catch-all (`notion_archive.py:230`) only `log.warning`s on a push exception — doesn't set `notion_skip_reason` (unlike the explicit skip paths) and never retries → a transient failure leaves `notion_archived_at` AND `notion_skip_reason` both NULL (invisible). **Fix:** persist `notion_skip_reason="push error: <type>"` on failure + bounded retry (2–3, backoff) + a re-archive affordance for `done` jobs with `notion_archived_at IS NULL`. Test: push raises → skip_reason recorded; retry path.
2. **`notion-archive-2` (R16)** — subject-page resolution is filename-substring-fragile: `_resolve_subject_page_id` (`notion_archive.py:43`) tests `_fold(keyword) in folded` at `:65`. NOTE (verified 2026-06-26): a `_fold` normalizer already exists (`notion_archive.py:37-40`, strips apostrophes/diacritics `'‘’ʻ\``), so the apostrophe/diacritic case is HANDLED — the residual gap is **abbreviated / non-substring filenames** (e.g. a book named "Tarix Ozb" still won't contain the keyword `"ozbekiston"`). **Fix:** keyword **aliases** (`"ozb"`, …) for the object-form mapping, guarding against loose-match collisions — NOT a normalizer (that's done). Latent (no O'zb-history book uploaded yet).
3. **Notion anchor auto-resolve** (WISHLIST, 2026-06-02) — resolve the subject-page ID by crawling (grade → `{N}-sinf` → child matching the subject label) instead of the hand-maintained `NOTION_SUBJECT_PAGES` dict. Eliminates the silent per-subject skip (Kimyo incident); surface unmapped skips in the UI regardless.
4. **Notion archive validator** (WISHLIST, spec'd + TDD-planned but parked) — auto, best-effort structural check that the live Notion tree matches `_HOMEWORK_LAYOUT`/`PHASE_TITLES` after `archive_job`, recording `homework_jobs.notion_validation` (verified/mismatch/archive-incomplete/skipped) + surfacing it in the console. Revisit only if archive correctness becomes a real pain.
5. **`fe-redesign` (BRAINSTORM-BLOCKED)** — operator console redesign is deferred mid-brainstorm (light/dark not chosen; mockup at `docs/design/2026-06-05-console-redesign-quiet-precision.html`). **Do not plan until the brainstorm resumes and the user picks the direction.**
6. **`fleet-ui-2/3/4`** — live SSE dashboard (`fleet-ui-2` **depends on `sse-multipod-1`** in C5), historical-batches view, richer PC cards. Lower priority.

**Open decision to lock first:** resume the fe-redesign brainstorm (user picks light/dark + confirms Slice 1 scope) before any FE-redesign plan.

---

## Cluster 7 — Judge quality (campaign-readiness; do alongside C5) — ✅ SHIPPED [0087] (2026-06-26)

> **✅ ALL THREE ITEMS SHIPPED — worklog [0087], branch `cluster-7-judge-quality`.** (1) `judge-self-fallback-1` fixed via **Option B** — a generator-aware `_self_fallback` (peers claude-opus-4-7 / gemini-3.1-pro-preview, returns whichever ≠ generator) that is provably non-self for ANY generator, NOT a fixed constant; also fixed `worker._compute_capabilities` (which read the removed `_SELF_FALLBACK` at import) so `judge_fallback_api_ok` tracks `_self_fallback(judge_pair)`. (2) `judge-refusal-1` — `_is_refusal` + `JudgeOutcome.refused`; pipeline records `judge_status="refused"` and skips the retry-once. (3) FE rendering — `judge_status` serialized on `PhaseOut` + distinct preview-console chip; infra warnings no longer co-mingle into `validation_warnings`. The original items/decisions are kept below for history.

**Why:** mass-gen ships unverified content when the judge can't grade; these are the residuals after the cluster-3 judge work ([0079]) landed.

> **Priority correction (verified 2026-06-26): C7 is NOT campaign-blocking under the locked config** (generator gemini-2.5-pro/flash, judge gemini-3.1-pro-preview). The self-grade guard's `_SELF_FALLBACK` path is only reached when judge == generator, which never happens with a 2.5 generator + 3.1 judge — so `judge-self-fallback-1` is **latent**, not a blocker (it only bites if a phase is generated by gemini-3.1-pro-preview under the same 3.1 judge). Items 2 (`judge-refusal-1`) and 3 (FE rendering) are observability improvements. All three are worth doing (cheap), but the genuinely campaign-critical work is the **C5 host-health trio** (`fleet-net-1` + the two limited-worker items), not C7. Treat C7 as fast-follow, not a gate.

**Items & exact work:**

**⚠️ HISTORICAL (all three SHIPPED [0087], see banner above). Kept for context only — DO NOT re-pick:**

1. ~~**`judge-self-fallback-1`**~~ ✅ shipped — `model_tiers._SELF_FALLBACK = ("gemini","gemini-3.1-pro-preview")` defeated the self-grade guard for a gemini-3.1-pro-preview generator. Shipped via **Option B** — generator-aware `_self_fallback` (non-self for ANY generator, no fixed constant) + `worker._compute_capabilities` fix.
2. ~~**`judge-refusal-1`**~~ ✅ shipped — a claude judge content-policy refusal parsed as no `Verdict` → degraded to `judge_status="unavailable"` AND burned the [0079] retry-once. Shipped: `_is_refusal` + `JudgeOutcome.refused`; pipeline records `judge_status="refused"` and skips the retry-once.
3. ~~**`judge-unavailable` FE rendering**~~ ✅ shipped — `judge_status` now serialized on `PhaseOut` + distinct preview-console chip; infra warnings no longer co-mingle into `validation_warnings`.

**Open decision:** ~~the `_SELF_FALLBACK` retarget peer (which non-gemini-3.1 model).~~ **RESOLVED — Option B (generator-aware peer, no fixed constant). Cluster CLOSED.**

---

## Cluster 8 — Book ingestion / coverage (for the all-subjects run)

**Why:** the Oct/Mar campaign needs every subject's textbook to ingest cleanly; these are the remaining fetch/extract walls.

**Items & exact work:**

> **Status 2026-06-26 (worklog [0088]):** the two clean fetch-lane items (2, 3) **SHIPPED** (Cluster 8 slice, branch `cluster-8-book-ingestion`). Items 1, 4, 5 **DEFERRED to the operator escape hatch by user decision** (not-clean drop-ins, each one rare known book, working workarounds) — revisit only when the definitive campaign subject list proves a *required* textbook hits one of them.
> **Update 2026-06-27 (worklog [0095]):** item 1 (`fetch-1`) **SHIPPED** — the campaign needed a 67.5 MB textbook in, so the >50 MB wall was reopened. It turned out NOT to need shrink/subset (Explore audit: every downstream read is already bounded) — just the ingest cap (50→250 MB) + de-conflated reject messages. **Cluster 8 now: 1✅ 2✅ 3✅, only 4/5 (glyph-loss) deferred.**

1. **`fetch-1`** — ✅ **SHIPPED [0095]:** raised `max_file_mb` 50→250 (ingest/RAM guard, not an LLM limit — all downstream PDF reads are bounded, Explore-audited) + de-conflated the upload-413/notion-422 messages (name `MAX_FILE_MB`, drop the stale "shrink and upload manually"). Shrink rejected (only `pypdf`; lossy for scanned). Real smoke `scripts/smoke_fetch1_giant.py` (54.4 MB synthetic: accepted, all reads bounded, no OOM). Streaming-to-disk + incremental hash (300 MB+/high-concurrency) → WISHLIST. Operator: head `.env` pins `MAX_FILE_MB=50` → raise to 250.
2. **`fetch-2`** — ✅ **SHIPPED [0088]:** `_first_pdf_block` now prefers `darslik` over `ish daftari` (`_pdf_rank` textbook 0 ▸ neutral 1 ▸ workbook 2, `min((rank,page_order))`, preference-not-exclusion). Raised above its "low priority" tag — a workbook-first page silently became the whole subject batch's textbook.
3. **`r13-fetch-1`** — ✅ **SHIPPED [0088]:** per-`book_id` `threading.Lock` in `ensure_book_pdf_sync` so the first fetches and the rest wait → cached fast path (RED-proved 5→1; per-process by design).
4. **`extract-1`** — TOC extraction `/Gxx` glyph-loss case poisons the TOC (no-ToUnicode → real-looking WRONG letters injected as authoritative TOC). **NOT a clean drop-in** — needs a short brainstorm on two decisions: glyph-loss detection without false-positiving normal TOCs, and loud-fail vs try-anyway. **⏸ DEFERRED (operator re-source cleaner-font PDF = R19).**
5. **R10** (broken-font PDF → near-empty TOC) — the ROADMAP twin of `extract-1`'s open glyph-ID manifestation; resolve them together. **⏸ DEFERRED with item 4.**

**Open decision to lock first:** `extract-1`/R10 glyph-loss detection + fail-mode (the brainstorm above) before planning.

---

## Cluster 9 — Infra / ops / cleanup (defer; do last)

**Why:** real but low-priority/independent; pick up à la carte or batch after the campaign-critical clusters. (Note `fleet-infra-1/2` lives in C5, not here.)

**Items & exact work:**

1. **`fleet-ops-1`** — pin gemini-cli versions across the fleet (mixed 0.45.2 / 0.46.0 today; auth + envelope shape are version-sensitive).
2. **`fleet-ops-2`** — make all completion timestamps DB-clock (`func.now()` through `set_status`/`phase_repo.set_status`) so cross-row/duration analytics survive multi-PC clock skew. Touches the `set_status` signature + many call sites → not a small fix.
3. **`deadcode-1`** — rip the dead `source_map_*` params/threading (~10 fns) + the `difficulty=None` threading (~20 sites) together, once R13 has landed.
4. **`json-preamble-1`** — strip a prose preamble before `model_validate_json` (the two parse sites are `agent.py:902-904` and `:1437-1439` — each `_strip_code_fences(text).strip()` → `model_validate_json`; `_strip_code_fences` only unwraps ``` fences, so an un-fenced prose preamble still fails the parse → wasted retry. Add a balanced-brace extract from the first `{`.) so claude-CLI's conversational narration doesn't cost a wasted retry. Small gated plan (shared parse path). (Refs corrected 2026-06-26; the old `:793-795`/`:1330` are now unrelated lines.)
5. **`opencode-flaky`** — only the shorter-per-attempt-timeout-for-flaky-providers half remains open (`per_attempt_timeout_seconds` is a uniform 600s; opencode hangs the full budget on every failover wave). The "keep last-resort" half is already true.

**Open decision to lock first:** none individually; batch them or pick à la carte.

---

## Cluster 10 — Content Quality (CQ) — filed 2026-07-01 from the 5-packet audit (ROADMAP R21)

> **Source:** first deep content audit — 5/5 packets FLAG, judge passed all of them
> (`judge_status=ok`). Evidence + job IDs: `docs/research/2026-07-01-content-quality-audit-g8-math.md`.
> Sequencing: **CQ-A ‖ CQ-B first** (cheap, immediate), then **CQ-C** (the core, own plan),
> **CQ-D** upstream hygiene, **CQ-E (R20)** last — it freezes baselines over the fixed system.
> CQ-A and CQ-C both touch prompt-assembly (`pipeline.py`/`agent._build_master_prompt`) — **serialize A before C**. CQ-B is disjoint (new module) — safe in parallel with anything.

### CQ-A — Prompt-layer fixes — ✅ **SHIPPED [0109]** (`cq-a-prompt-boundary`)
Surface: `prompts/_general/reflection.md` + the prompt-assembly path (`pipeline.py` context build) + `prompts.py`. Real api smoke on book `860e86aa` §17 Pythagoras PASSED (boss-arena no converse reach; reflection conditional; ru bridge). Suite 1275 passed. No migration.
1. **R21.1 lesson-boundary rule** — ✅ `pipeline._inject_lesson_boundary` injects the boundary note into `lesson_context` (all content phases; extract untouched); next lesson via `toc_entries.get_next_in_book` (skips NULL-section end-matter). Chose all-content-phases over the two-worst-offenders-only variant (systemic defect; near-zero cost).
2. **R21.5 reflection fix** — ✅ `reflection.md` §2/§4 no longer pre-assert outcomes; app owns pass/redo.
3. **`l2-bridge-follows-medium`** — ✅ `prompts._l2_rule` makes the L2 scaffolding bridge follow the medium (uz byte-identical via frozen-literal test); `_resolve_language_rule` threads `output_language`.

### CQ-B — Deterministic validators — **ONE implementer, ONE plan/PR, ships 2 items** (no LLM, no cost)
Surface: one new module (e.g. `app/services/content_lint.py`) + post-phase wiring + tests. Disjoint from CQ-A/C.
1. **R21.3 error-detection format validator** — count factually-false blocks vs the Reveal's single broken block; enforce the prompt's own EXACTLY-ONE rule (`practice-error-detection.md:50-54`).
2. **R21.4 language lint** — mixed-script regex (Cyrillic-in-Latin word), English-template blacklist (`Mode:`, `Needs Retry`, `Scenario`…), calque list ("qizil seld"), missing `source`/`inferred` misconception tags. Warning-or-regen policy is a plan decision.

### CQ-C — Answer-key solver pass — ✅ **SHIPPED [0112]** (`cq-c-key-solver`, R21.2)
Independent `solver.solve()` re-solves **memory-check / practice-error-detection / practice-rlc** (boss-arena dropped at the gate — no diffable written key; → R21.9 residue), diffs the key, regens once on a HIGH-confidence mismatch. Clone of the judge (per-role `solver_*` columns + claim-gate, mig 0043, seeded `gemini-3.1-pro-preview`, ~$0.12/job). **Locked decisions:** 3 key-bearing phases; conservative high-conf-only regen → **zero false positives**; frontier solver + self-grade guard. **Characterized recall = 1 of 3** audited defects on gemini-3.1-pro (catches objective sign/arithmetic; misses conceptual + equivalence — model-capability, not design; the misses → CQ-E's rubric). Follow-ups spun to ROADMAP R21.7 (recall gap, accepted), R21.8 (solver-config editing, deferred), R21.9 (boss-arena feedback residue).

### CQ-D — Source integrity — **code pair ✅ SHIPPED (worklog [0111], PR cq-d-extract-guards); rest is operator/data**
1. ~~**R21.6 extract-example fidelity**~~ ✅ **SHIPPED [0111]:** hybrid guard on the local-text extract branch — free `extract_fidelity_candidates` (fraction/equation exprs absent from source; `/`-or-`=` AND digit-or-paren so digitless invented examples surface) → gemini-flash `verify_extract_fidelity` (fail-open, **lesson-scoped source**) → regen `summarize_lesson(correction_hint=)` once on confirmed drift. The judge could never catch this (grades vs the extract, exempts worked-example arithmetic). Extract is cached cross-job → amortized.
2. ~~**R10/`extract-1` glyph-loss detection**~~ ✅ **SHIPPED [0111]:** `_alpha_plausibility_ratio` (Latin∪Cyrillic∪Uzbek alphabet ratio, floor 0.70) in Gate A + `_toc_text_is_usable` → garbled text routes to vision. **Re-scoped by fact:** the `/Gxx` case already recovers via `_decode_glyph_text` [0035]/[0036]; the live gap was cp1251 mojibake (`f20db30c`: letter-density 0.88 passes, plausibility 0.082). Corpus sweep: 25/26 books accept, only the garbage one rejects. *(Paid vision-recovery + verify-discrimination smokes owed at the PR gate — CLI env had stale Vertex creds.)*
3. *(operator/data, not implementer):* R19 stale textbooks (C3 sweep), stub-PDF `9e7833bc` cleanup. *(shelved: `toc-reextract-override-1` — only if verified-but-incomplete TOCs are observed.)*

### CQ-E — R20 golden-eval harness — **own plan, LAST**
Frozen `(subject, lesson)` set + rubric scoring + baseline diffs + PR gate. The 5 audited packets are the first golden entries; the audit method (read source pages → trace taught-before-asked → re-solve keys) is the rubric prototype. Do after CQ-A/B/C land so baselines freeze the *fixed* behavior.

**TL;DR for pickup:** CQ-A ✅ [0109], CQ-B ✅ [0110], CQ-D ✅ [0111], CQ-C ✅ [0112]. **Only CQ-E (R20 golden-eval) remains** — it freezes baselines over the now-fixed system AND is the primary coverage for the two answer-key defect classes the CQ-C solver misses (R21.7).

---

## Items deliberately NOT in any cluster

- **R19** (stale/missing textbooks) — operator task, user sources + re-uploads PDFs; no code.
- **`fleet-api-1/2/5`** (batch transport, credential rotation, codex API mode) — user scoped to Anthropic+Gemini; revisit on request.

> (The ingestion/cleanup items formerly parked here — `fetch-1`/`fetch-2`/`extract-1`/R10/`r13-fetch-1`/`deadcode-1`/`json-preamble-1`/`opencode` flakiness — are now formalized into **Cluster 8** and **Cluster 9**.)
