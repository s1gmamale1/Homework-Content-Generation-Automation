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
> touches generation needs a **real CLI smoke** at the acceptance gate; finish = worklog +
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

## Cluster 1 — Quick-win hardening (START HERE; low-risk, mostly mechanical)

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

## Cluster 2 — Cancel / resume correctness (one plan, sequential; all touch jobs+pipeline+launcher)

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

## Cluster 3 — Judge quality (campaign-critical; one plan, may split claimgate out)

**Why:** without source-fidelity + a regression signal, mass-gen ships unverified content. Highest campaign value.

**Items & exact work:**

1. **`judge-fidelity-1`** — `phase_judge._build_judge_prompt(*, contract, output_md)` (`phase_judge.py:75`) never includes the source text; `lesson_context` is a `judge()` param (`phase_judge.py` `judge(..., lesson_context=...)`) but isn't threaded into the prompt. **Fix:** feed `lesson_context` into the judge prompt (grade fidelity-to-source, not just format) + a deterministic key-fact/number cross-check. **Generation-affecting → real CLI smoke required:** prove (a) a phase with an invented fact now gets flagged, (b) a faithful phase still passes. Watch token cost — `lesson_context` can be large; consider a bounded excerpt.
2. **`judge-softfail-1`** — on a judge error or failed regen the phase still completes `done` keeping the rejected output + warnings (`pipeline.py:918-922`); regen triggers only on MAJOR (`pipeline.py:869`) and commits on *generation* success without re-checking the post-regen verdict. **Fix:** add a queryable `phase_outputs.judge_status` (`ok`/`major_shipped`/`unavailable`) — new column + migration; retry the judge once on transient error; make the regen cap configurable; re-check the post-regen verdict. Test: each judge_status path; post-regen still-MAJOR is recorded not silently accepted.
3. **`judge-claimgate-1` (separable — could be its own small plan)** — per-job judge provider ignored by the claim gate. The worker computes `CAPABILITIES` once at startup from `settings.judge_provider`/`judge_model`/`extract_provider` (`worker.py:56` `_compute_capabilities`, flags `:74`/`:77`, `CAPABILITIES` global); `jobs.claim_next_job` (`jobs.py:230`) gates `judge_api_ok`/`judge_pair` (`jobs.py:269`) on those settings-derived caps — never reading the job's `judge_provider` column, though execution honors it (`pipeline.py:104`→`resolve_judge`,`:858`). So a `judge_transport=api` gemini-judge job is gated as needing a *claude* key → strands. **Fix:** thread the job's `judge_provider`/`judge_model` (+ by symmetry `extract_provider`/`extract_model`) into the claim gate; evaluate per-role caps against the JOB's role when set, fall back to settings only when null; **audit every `settings.judge_*`/`settings.extract_*` read for the same drift** (the user's standing principle: a per-job pick overrides `.env` on EVERY path). Touches `worker.py` cap compute + `jobs.claim_next_job` SQL + the existing null-model claim tests. Test: an api-gemini-judge job is claimed by a Vertex-only worker with no ANTHROPIC key.
4. **`R20` golden-eval harness (DESIGN-FIRST — biggest piece)** — frozen `(subject, lesson)` set with known source text → generate → judge/rubric-score (incl. the fidelity cross-check from item 1) → diff vs committed baselines → gate prompt/model-change PRs. This needs a **design** in the plan's approach header (where the set lives, how baselines are stored/diffed, CI vs manual). Consider making R20 its own follow-on plan after 1–3 land, since it consumes their outputs.

**Approach header seed:** "Source-fidelity is the core fix; judge_status makes grading observable; claimgate enforces per-job overrides at the one gate that strands jobs; golden-eval is the regression harness built on top. Claimgate and golden-eval are separable into their own plans if scope is too big."

**Open decision to lock first:** (a) is R20 in-scope for this plan or a follow-on? (b) golden-set size + where baselines live (repo vs DB) — needs your call.

---

## Cluster 4 — Cost safety (campaign-critical; DESIGN-FIRST; Wave 2)

**Why:** mandatory before any paid mass-gen — today nothing reads `cost_usd` to gate spend, and a re-run silently re-bills.

**Items & exact work:**

1. **`fleet-api-3` Cost ledger + kill-switch** — today `cost_usd` (`pricing.py:63`) is computed only for the read-only `/agent/stats` (`jobs.py:449`). **Fix:** a per-batch (and/or per-provider) $ ledger + a configurable cap that **halts claiming** when exceeded — teach `claim_next_job` (`jobs.py:230`) to skip batches/over-budget work (ties to the `fleet-ctrl-3` pause gate). Design needed: where the running total lives (a `batches` rollup column vs computed-on-read), and the halt mechanism.
2. **`fleet-api-4` Never-pay-twice** — idempotency so a re-run lesson doesn't re-bill. Note this overlaps the resume work (Cluster 2) and the `find_active_for_section`/`done`-reuse path; design must reconcile "resume reuses done phases" (already free) with "don't re-bill a fresh re-run."
3. **`pricing-1` claude cache-write billing** — `agent_usages` has no `cache_creation_tokens` column (claude provider parses `cache_creation_input_tokens` into `raw_envelope` only), so the api `$` under-reports the 1.25× cache-write premium. Add column + migration + backfill from `raw_envelope` + price it in `cost_usd`. Known-bias note already in `pricing.py:55`. **Do NOT collapse the per-provider cached-token semantics** (gemini `prompt` INCLUDES cached; claude disjoint) — CLAUDE.md hard rule.

**Open decisions to lock first:** cap granularity (per-batch $ vs per-day fleet $), and halt behavior (pause-claim vs hard-cancel). These are product calls — get them from the user before planning.

---

## Cluster 5 — Fleet scale (DESIGN-FIRST; Wave 2; needs infra decisions)

**Why:** real fleet-wide throughput + observability; not blocking single-PC use today.

**Items & exact work:**

1. **`concurrency-knob-1` (fleet-wide half)** — after Cluster 1 fixes the dead knob, the cap is still **per-process** (module-global semaphore, `agent.py:203`), so N workers = N×cap, and there's **no 429/`Retry-After` backoff** → Vertex over-quota mass-fails. **Fix:** a shared token-bucket per `(provider, model)` (DB or Redis) + Retry-After-aware backoff in the spawn path. Design: where the bucket lives, how workers coordinate.
2. **`sse-multipod-1`** — `events_bus._subscribers` is an in-process dict (`events_bus.py:5`); separate worker pods publish to their own process → browser on the API pod sees only the initial DB replay then a frozen stream. **Fix:** back the bus with Postgres `LISTEN/NOTIFY` (no new infra) or Redis pub/sub.
3. **`fleet-ctrl-3` Pause/Resume a batch** — batch-status gate on the hot `claim_next_job` path (skip paused batches). Pairs with Cluster 4's kill-switch (same gate).
4. **`fleet-ctrl-4` PC-level drain** — gracefully stop one worker claiming new jobs + let its in-flight finish before offline.
5. **`fleet-infra-1/2`** — PgBouncer + hardened head; worker discovery file / movable head (≤60s reconnect). Mostly ops/deploy, not app code.

**Open decisions to lock first:** Redis-or-Postgres for both the token-bucket and the event bus (infra appetite). Get this from the user — it shapes 1, 2, and the deploy.

---

## Cluster 6 — Notion + Frontend (Wave 3 / as-unblocked)

**Items & exact work:**

1. **`notion-archive-1` (R15)** — `archive_job`'s catch-all (`notion_archive.py:230`) only `log.warning`s on a push exception — doesn't set `notion_skip_reason` (unlike the explicit skip paths) and never retries → a transient failure leaves `notion_archived_at` AND `notion_skip_reason` both NULL (invisible). **Fix:** persist `notion_skip_reason="push error: <type>"` on failure + bounded retry (2–3, backoff) + a re-archive affordance for `done` jobs with `notion_archived_at IS NULL`. Test: push raises → skip_reason recorded; retry path.
2. **`notion-archive-2` (R16)** — subject-page resolution is filename-substring-fragile: `_resolve_subject_page_id` (`notion_archive.py:43`) tests `_fold(keyword) in folded`; an O'zbekiston book named "Tarix Ozb" won't contain `"ozbekiston"` → silent "no Notion page" skip. **Fix:** aliases (`"ozb"`, `"o'zbekiston"`) or a normalizer, guarding against loose-match collisions. Latent (no O'zb-history book uploaded yet).
3. **`fe-redesign` (BRAINSTORM-BLOCKED)** — operator console redesign is deferred mid-brainstorm (light/dark not chosen; mockup at `docs/design/2026-06-05-console-redesign-quiet-precision.html`). **Do not plan until the brainstorm resumes and the user picks the direction.**
4. **`fleet-ui-2/3/4`** — live SSE dashboard (depends on Cluster 5 item 2), historical-batches view, richer PC cards. Lower priority.

**Open decision to lock first:** resume the fe-redesign brainstorm (user picks light/dark + confirms Slice 1 scope) before any FE-redesign plan.

---

## Items deliberately NOT in any cluster

- **R19** (stale/missing textbooks) — operator task, user sources + re-uploads PDFs; no code.
- **`fleet-api-1/2/5`** (batch transport, credential rotation, codex API mode) — user scoped to Anthropic+Gemini; revisit on request.
- **`opencode` flakiness** — "keep as last-resort fallback" note, not a fix.
- **`fetch-1` (>50MB giants), `fetch-2` (darslik-vs-workbook), `extract-1`/R10 (`/Gxx` glyph-loss), `r13-fetch-1` (per-book fetch lock), `deadcode-1` (source_map_*/difficulty=None threading), `json-preamble-1` (JSON preamble strip)** — real but low-priority/independent; fold into Cluster 1 as extra tasks if there's appetite, or pick up à la carte. `json-preamble-1` is a nice small win (balanced-brace extract before `model_validate_json` at `agent.py:793-795` + `:1330`).
