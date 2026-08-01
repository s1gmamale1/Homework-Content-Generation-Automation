# Model config → 3.x flash family on the plain Gemini API key (rev 4)

## Approach & key decisions

**Goal:** move generation off the now-dead 2.5 family to the 3.x flash models the restored-$100k plain
Developer-API key serves — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without breaking cost attribution, the
CLI/API transport invariant (front AND back), any job-reactivation path, running tools, the self-grade
guard, or the fleet auth cutover.

**Facts verified against the live key + code (rev 4 folds in the second composition re-gate):**
- Target IDs callable on the key; **2.5 is DEAD** (real 404). Keep 2.5 `PRICE_MAP`/`_MODEL_TIER` rows
  (historical), REMOVE from `MODEL_MANIFEST`. Define an explicit `RETIRED_GEMINI_MODELS =
  {"gemini-2.5-pro","gemini-2.5-flash","gemini-2.5-flash-lite"}` — the reactivation guards key off THIS
  set, not "any off-manifest model" (an off-manifest string could be a typo/other cause; only these are
  proven-dead).
- The 3 new flash models **fail on the gemini CLI**; api-only is provider-level only today. Need a
  model-level api-only set + `validate_transport` reject (backend) AND all **four** FE control surfaces
  told, or the UI offers 3.5/3.6 under CLI and Launch fails.
- **THREE reactivation paths preserve the pinned model, not one:** `retry_job` (jobs.py), batch
  **relaunch resume** (`batch.py:364` adopts a saved failed/cancelled section), and batch **Resume** →
  `resume_failed_in_batch` → `reset_for_retry` (jobs.py:961, re-enqueues ALL failed/cancelled in a batch,
  keeping stamps). Live: **144 of the 157** 2.5 rows are batched across **6 batches**. A guard on only
  `retry_job` is insufficient. **Decision:** one shared evaluator `retired_models_in_job(job)` pairing
  EACH role model with ITS OWN provider (`content_provider`/`extract_provider`/`judge_provider`/
  `solver_provider` — not the main provider for all), used in all three paths with path-appropriate policy.
- **Four FE control surfaces** decide model/transport: `launcher.tsx:988`, `section.tsx:139` (content
  picker), `RoleAgentControls.tsx:62` (extract/judge), `settings.tsx:318` (global defaults + TOC selector).
  An api-only **extract** model additionally requires `toc_transport=api` (backend already 422s the
  incompatible combo, `settings.py:108`). One shared FE model/transport resolver + tests for all four.
- **Running tools default to dead 2.5:** `teaching_audit.py:805-806` + `:892-893` (examiner=2.5-pro,
  student=2.5-flash), `scripts/teaching_audit.py:50-51`, `scripts/golden_eval.py:244` (2.5-pro),
  `scripts/api_vs_cli_compare.py`, `scripts/toc_validate_smoke.py`, `scripts/stress_concurrency.py`
  (2.5-flash-lite). Runnable defaults must move to live models; pure historical-repro scripts may stay
  pinned IF a comment documents it.
- **Judge acceptance must prove judgment, not just parsing.** 20/20 valid JSON ≠ correct fidelity calls.
  Reuse the merged per-claim safety-probe harness (`scripts/experiments/rejudge_ab.py`) against
  `gemini-3.5-flash`, 3× each, raws retained.
- **Fleet auth cutover (both mechanism corrections):** `scrub()` sets `key_id=NULL, scrub_requested_at`
  but KEEPS the tombstone assignment row until `unassign()` — so verify **local SA residue cleared**
  (worker `active.json`/env) while the tombstone REMAINS, THEN Unassign to unpark. Head restart
  **auto-raises the version floor** (`main.py:76`, raise-only) — restart head and VERIFY the auto-stamped
  floor; `PUT /workers/version-floor` is only the escape hatch.
- `toc_validation_model` is process-loaded (needs head restart). `validate_toc` returns `skipped` on any
  failure (never raises). Live `launch_defaults` prod row all-`api`; fresh 0048 differs (2.5-pro, toc=cli).
- Pricing coverage scoped to `API_PROVIDERS`. 3.5-flash 5/5 on the real judge path. Cost ≈ **$1.43/hw**.

**Collision:** branch plan-only at 513e134; base `origin/Nggaev-v2` a80cac3. PR #108 overlaps only the
append-only memory docs (Task 9). Worklog **0161**; revision **0049**.

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2`.

---

## Task 1 — Register 3 API-only models; retire 2.5 from manifest; scoped pricing coverage

**Files:** `agent_models.py`, `pricing.py`, `model_tiers.py`, `tests/services/test_agent_models.py`,
`tests/services/test_pricing.py`, `tests/api/test_agent_models_tiers.py`.
- RED: `is_valid` True + `validate_transport(…,"cli")` rejected / `("…","api")` allowed for the 3;
  `is_valid("gemini","gemini-2.5-flash") is False`; exact-rate tests; `test_every_api_billable_manifest_model_is_priced`
  scoped to `provider in API_PROVIDERS`; `tier_of("gemini",m)` values (two-arg).
- GREEN: manifest add 3 / remove 3 2.5; `GEMINI_API_ONLY_MODELS` + `validate_transport` cli-reject;
  `RETIRED_GEMINI_MODELS`; add 3 price + 3 tier rows (keep 2.5 rows).
- Update selection tests; **preserve** any 2.5 accounting/historical-rate tests (comment each).
- **Commit:** `feat(models): 3.x flash api-only, retire 2.5 from manifest, retired-set, scoped pricing`.

## Task 2 — TOC-validator default off 2.5

**Files:** `app/config.py:224`, `tests/services/test_config_defaults.py`.
- RED `settings.toc_validation_model == "gemini-3.5-flash-lite"`; GREEN the one-line change; leave `gemini_model` (l28).
- **Commit:** `chore(config): toc validator default 2.5-flash → 3.5-flash-lite`.

## Task 3 — Shared retirement guard across ALL three reactivation paths (F1)

**Files:** `app/services/agent_models.py` (or a small `job_reactivation.py`), `app/api/v1/jobs.py`
(`retry_job`), `app/api/v1/batch.py` (relaunch-resume ~364), `app/repositories/jobs.py`
(`resume_failed_in_batch` ~961), tests `tests/api/test_jobs_retry.py`,
`tests/api/test_batch_resume_retired.py`, `tests/repositories/test_resume_retired.py`.
1. **Shared evaluator** `retired_models_in_job(job) -> list[tuple[role, provider, model]]`: for each role,
   pair its OWN provider+model, return those whose model ∈ `RETIRED_GEMINI_MODELS`.
2. **RED + policy per path:**
   - `retry_job`: any retired role → **409** structured body (roles+models, "force-regenerate to re-stamp").
   - `resume_failed_in_batch`: **skip** retired-stamped jobs (don't re-enqueue), re-enqueue the rest,
     return `{resumed: n, skipped_retired: [ids]}`; a repo test proves a mixed batch resumes clean jobs and skips retired.
   - batch relaunch-resume (batch.py:364): when the adopted section job is retired-stamped, **create fresh**
     (re-stamp from resolved launch_defaults) instead of adopting the dead stamp; an api test proves it.
3. Run the three test files green.
4. **Commit:** `feat(jobs): retired-model guard across retry / batch-resume / relaunch-resume`.

## Task 4 — Surface model-level api-only across all FOUR FE surfaces (F2/F3-FE)

**Files:** `app/api/v1/jobs.py` (`/agent/models` → add `api_only_models`), `web/src/lib/types.ts`,
a shared resolver e.g. `web/src/lib/model-transport.ts` (new), `launcher.tsx`, `section.tsx`,
`RoleAgentControls.tsx`, `settings.tsx`, FE tests under `web/src/**/__tests__/`.
1. RED: backend test — response carries `api_only_models`. FE unit tests (one shared resolver): (a) main
   content selection on launcher AND section picker disables/auto-pins api for an api-only model;
   (b) a role (extract/judge) with `inherit` transport while the parent is CLI is disallowed/forced when
   the role model is api-only; (c) global-defaults + TOC: choosing an api-only **extract** model forces
   `toc_transport=api` (mirrors the backend 422).
2. GREEN: add the endpoint field; one shared `resolveTransportFor(model, apiOnlyModels)` helper used by all
   four surfaces.
3. `pytest -k agent_models`; `cd web && npx tsc -p tsconfig.app.json --noEmit` + FE test runner.
4. **Commit:** `feat(models): expose api_only_models; all FE pickers disable CLI + couple TOC transport`.

## Task 5 — Move running tools off 2.5 defaults (F3-tools)

**Files:** `app/services/teaching_audit.py` (defaults l805-806/892-893), `scripts/teaching_audit.py`,
`scripts/golden_eval.py`, `scripts/api_vs_cli_compare.py`, `scripts/toc_validate_smoke.py`,
`scripts/stress_concurrency.py`, `tests/services/test_teaching_audit*.py` (defaults test).
- teaching_audit examiner→`gemini-3.6-flash` (or 3.1-pro if the audit wants frontier), student→`gemini-3.5-flash`;
  scripts' runnable `--model` defaults → live equivalents; `stress_concurrency` cheapest → `gemini-3.5-flash-lite`;
  `toc_validate_smoke` → `gemini-3.5-flash-lite`. Any script kept pinned for historical repro gets a
  `# historical repro — model intentionally pinned` comment.
- RED: a test asserts `teaching_audit` module defaults are not in `RETIRED_GEMINI_MODELS`.
- **Commit:** `chore(tools): move teaching-audit + operator-script defaults off retired 2.5`.

## Task 6 — Migration 0049: launch_defaults → target tuple (unconditional)

**Files:** `alembic/versions/0049_launch_defaults_3x.py`, `tests/repositories/test_launch_defaults_migration.py`.
- `upgrade` unconditional target tuple (5 models gemini + 5 `_transport='api'`); `downgrade` → prod tuple.
- RED (scratch, real DB): from fresh-0048 tuple AND prod tuple → upgrade both == target; downgrade == prod.
- **Commit:** `feat(launch-defaults): migration 0049 → 3.x flash target tuple`.

## Task 7 — Acceptance A: judge parsing (20/20) AND judgment quality (per-claim probes) — scratch DB

Scratchpad (uncommitted), **scratch DB**:
1. **Parsing:** ~20 stored phases (every type, ≥4 subjects) judged with `gemini-3.5-flash`/api → **20/20**
   valid Verdicts; record retries. If <20/20 → **Task 7b** (string-aware `json.JSONDecoder().raw_decode`
   from each `{` — a hand brace-counter breaks on `{"a":"}"}`; RED with brace-in-string + prose fixtures).
2. **Quality (F4):** reuse `scripts/experiments/rejudge_ab.py` per-claim probes with judge=`gemini-3.5-flash`,
   **3× each, raws retained**: planted contradiction stays major; absent-but-true never major; generated
   exercise values not invented-flagged; constructed wrong fact stays major. Report pass/fail + cost.

## Task 8 — Acceptance B: full pipeline incl. TOC over the plain key — scratch DB

Scratchpad (uncommitted), **scratch DB**, fleet untouched:
1. **TOC (F5):** ingest a REAL PDF; run `toc_extractor` (extract=`gemini-3.5-flash-lite`, toc_transport=api).
   Assert: persisted nonempty `toc_entries`; `books.toc_validation` ∈ {`verified`,`mismatch`} (**never
   `skipped`**); **TWO** token-bearing `agent_usages` rows — a `toc.extract` (or summarize) AND a
   `toc.validate` — both `gemini-3.5-flash-lite`, tokens>0, `success=True`.
2. **Content:** one homework end-to-end (`pipeline.run`) on the target roles; all 11 phases produced+judged,
   solver on boss-arena, **zero success=False `agent_usages` for this job**, every priced row for this
   job/book > $0; report `cost_usd` scoped to the acceptance book/job (vs ~$1.43). No mass generation.

## Task 9 — Finish

1. Offline suite green + `npx tsc -p tsconfig.app.json --noEmit`.
2. **Rebase gate (#108):** `git fetch origin`; rebase if base moved; re-apply append-only memory edits; re-run.
3. Push, open PR (base `Nggaev-v2`); user/GK gates; never self-merge.
4. Worklog **0161** + INDEX; de-stale CLAUDE.md (`settings.extract_*` stale; 2.5 retired; api-only; retry
   guard), `HOW_IT_WORKS.md`, `CODE_MAP.md`, `DATABASE.md`; `git mv` plan → `shipped/`.
5. **Ops (operator, user-owned) — coordinated cutover:**
   1. Stop new launches + drain.
   2. Pull code on head AND every worker.
   3. Per host: **Scrub** → wait idle + **verify local SA residue cleared** (worker `active.json`/env has no
      SA creds) **while the tombstone row remains** → **Unassign** to unpark.
   4. Install plain `GEMINI_API_KEY` in every `.env` (head + workers).
   5. Apply migration 0049 on the head DB.
   6. **Restart head, then fleet**; head restart **auto-stamps the version floor** — VERIFY it and that
      workers pass (stale fenced); `PUT /workers/version-floor` only if a manual override is needed.
   7. One post-deploy smoke before reopening launches.
   8. Note: fleet now shares ONE credential → one concurrency/rate lane; watch 429s.

## Explicitly out of scope

- Changing `_auth_env`/`_gemini_client` precedence. Tiered 3.1-pro pricing. Re-enabling 2.5.
- Data-migrating the 157 terminal 2.5 rows (Task-3 guards make them safe). The operator cutover itself.
