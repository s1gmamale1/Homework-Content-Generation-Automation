# Model config → 3.x flash family on the plain Gemini API key (rev 5)

## Approach & key decisions

**Goal:** move generation off the dead 2.5 family to the 3.x flash models the restored-$100k plain
Developer-API key serves — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without breaking cost attribution, the
CLI/API transport invariant (front AND back), ANY job-reactivation path (incl. saved-work honesty),
running tools, the self-grade guard, or the fleet auth cutover.

**Facts verified against the live key + code (rev 5 folds in the second FE/reactivation re-gate):**
- 2.5 is DEAD on the key (real 404). Keep 2.5 priced/tiered (historical), remove from `MODEL_MANIFEST`.
  `RETIRED_GEMINI_MODELS = {"gemini-2.5-pro","gemini-2.5-flash","gemini-2.5-flash-lite"}`; retirement
  requires **`provider=="gemini"` AND `model in RETIRED_GEMINI_MODELS`** (a non-gemini job reusing the
  same model string must NOT trip).
- **`HomeworkJob` has `provider`+`model` for the CONTENT role (NOT `content_provider`)** plus
  `extract_provider/model`, `judge_provider/model`, `solver_provider/model`. The shared evaluator
  iterates exactly these four (provider, model) pairs.
- New flash models fail on the gemini CLI; api-only is provider-level only today. Need a model-level
  api-only set + backend `validate_transport` reject AND all **four** FE surfaces (`launcher.tsx:988`,
  `section.tsx:139`, `RoleAgentControls.tsx:62`, `settings.tsx:318`), plus the extract↔TOC-transport
  coupling (`settings.py:108` 422s the bad combo).
- **THREE reactivation paths keep the pinned model** (retry_job; batch relaunch-resume `batch.py:364`;
  batch Resume → `resume_failed_in_batch`→`reset_for_retry` `jobs.py:961`). 144/157 2.5 rows are batched
  (6 batches). **Preview reports a `resumable` count (`batch.py:285`) and the UI promises completed
  phases are reused (`launcher.tsx:1548`)** — so silently recreating a retired saved job would discard
  done work without consent. **Decisions:** (a) preview adds a `retired` count; (b) relaunch-resume of a
  retired saved section that HAS saved work → **409** (needs explicit discard/regenerate), never silent
  recreate; (c) batch Resume **skips** retired jobs and the endpoint returns `{jobs_resumed,
  jobs_skipped_retired:[str ids]}` threaded through the TS type + BOTH toasts (surface skipped honestly);
  (d) single `retry_job` → 409.
- **Running tools default to dead 2.5** — repo-wide `grep gemini-2\.5` hits `teaching_audit.py`,
  ~13 scripts. Runnable defaults move to live models; legit references (pricing/tier rows, the
  api_transport location comment, homework_job docstring example) stay; pure historical-repro scripts get
  an explicit `# historical repro — pinned` allowlist comment. `api_vs_cli_compare.py` needs a
  BOTH-transport model — since cli is retired and live gemini models are api-only/untested-on-cli, it is
  pinned historical (documented), not repointed at an api-only model.
- **Judge acceptance must prove judgment, not parsing.** `rejudge_ab.py` has NO judge-model CLI arg
  (`JUDGE_MODEL_STAMP="gemini-2.5-flash"` is a module constant; `--probes-only` patches an existing
  artifact), BUT its `run_probes(...)`/`build_probes(...)` take `judge_provider/judge_model` params — so
  Task 7 **imports the harness directly** and calls it with `judge_model="gemini-3.5-flash"` into a fresh
  artifact.
- **Cutover has a keyless-claim window:** after Unassign the host can claim immediately, but the plain key
  isn't process-loaded until restart. Correct per-host order: Scrub → verify local residue cleared (while
  the tombstone row remains — `scrub()` keeps it until `unassign()`) → **STOP worker** → Unassign →
  install key → restart. Head restart **auto-raises the version floor** (`main.py:76`); verify it, don't
  PUT. Fleet now shares ONE credential → one rate lane; conservative start (below) + a cheap ramp on the
  exact 3.5/3.6 IDs (the prior saturation test covered 3-flash/3.1-lite, not these).
- `launch_defaults` = **4 provider/model pairs + 5 transports** (content/extract/judge/solver models;
  content/extract/judge/toc/solver transports); the TOC-validator MODEL is process config (`config.py`),
  not a DB column. `toc_validation_model` process-loaded (head restart). `validate_toc` returns `skipped`
  on any failure. Pricing coverage scoped to `API_PROVIDERS`. Cost ≈ **$1.43/hw**.

**Collision:** branch plan-only at **5d82a0f**; base `origin/Nggaev-v2` a80cac3; PR #108 overlaps only the
append-only memory docs (Task 9). Worklog **0161**; revision **0049**.

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2`.

---

## Task 1 — Register 3 API-only models; retire 2.5; scoped pricing coverage; retired set

**Files:** `app/services/agent_models.py`, `app/services/pricing.py`, `app/services/model_tiers.py`,
`tests/services/test_agent_models.py`, `tests/services/test_pricing.py`,
`tests/services/test_model_tiers.py`, `tests/api/test_agent_models_tiers.py`.
- RED: 3 models `is_valid` True + cli-rejected/api-allowed; `is_valid("gemini","gemini-2.5-flash") False`;
  exact rates; `test_every_api_billable_manifest_model_is_priced` scoped to `API_PROVIDERS`;
  `tier_of("gemini",m)` for the 3.
- GREEN: manifest +3/−3(2.5); `GEMINI_API_ONLY_MODELS` + `validate_transport` cli-reject;
  `RETIRED_GEMINI_MODELS`; price+tier rows (keep 2.5); update selection tests, preserve accounting tests.
- **Commit:** `feat(models): 3.x flash api-only, retire 2.5, retired-set, scoped pricing`.

## Task 2 — TOC-validator default off 2.5

**Files:** `app/config.py:224`, `tests/services/test_config_defaults.py`. RED
`settings.toc_validation_model=="gemini-3.5-flash-lite"`; GREEN one-liner; leave `gemini_model`.
**Commit:** `chore(config): toc validator default → gemini-3.5-flash-lite`.

## Task 3 — Shared retirement guard across all 3 reactivation paths + honest responses (F1/F2/F3)

**Files:** `app/services/job_reactivation.py` (new — `RETIRED_GEMINI_MODELS` lives in agent_models;
evaluator here), `app/api/v1/jobs.py` (`retry_job`), `app/api/v1/batch.py` (relaunch-resume ~364, preview
~285, resume endpoint ~504), `app/repositories/jobs.py` (`resume_failed_in_batch`), `web/src/lib/types.ts`
(resume response + preview), `web/src/components/fleet/launcher.tsx` (toasts ~1141, preview ~1548),
tests: `tests/services/test_job_reactivation.py`, `tests/api/test_jobs_retry.py`,
`tests/api/test_batch_resume_retired.py`, `tests/api/test_batch_preview_retired.py`, FE toast test.
1. **Evaluator** `retired_models_in_job(job)`: over the FOUR pairs `(provider,model)`,
   `(extract_provider,extract_model)`, `(judge_provider,judge_model)`, `(solver_provider,solver_model)`
   return those with `provider=="gemini" and model in RETIRED_GEMINI_MODELS`. **Tests:** each role trips;
   null fields skipped; a non-gemini provider with a 2.5-looking model string does NOT trip.
2. **retry_job** → 409 structured (roles+models, "force-regenerate").
3. **resume_failed_in_batch** → skip retired jobs, re-enqueue rest, return `{resumed, skipped_retired:[ids]}`;
   endpoint returns `{jobs_resumed, jobs_skipped_retired:[str]}`; TS type + both toasts updated to show skipped.
4. **preview** adds a `retired` count; **relaunch-resume** of a retired saved section WITH saved work → 409
   (explicit discard/regenerate), never silent recreate.
5. Green all test files + `npx tsc`.
6. **Commit:** `feat(jobs): retired-model guard across retry/resume/relaunch with honest responses`.

## Task 4 — Model-level api-only across all FOUR FE surfaces + shared resolver (F2-FE/F4)

**Files:** `app/api/v1/jobs.py` (`/agent/models` → `api_only_models`), `web/src/lib/types.ts`,
`web/src/lib/model-transport.ts` (new shared resolver), `launcher.tsx`, `section.tsx`,
`RoleAgentControls.tsx`, `settings.tsx`, FE tests.
1. Resolver **`resolveTransport({provider, model, currentTransport, parentTransport, apiOnlyModels})
   -> {effective, forced}`** — provider-aware; api-only model forces `api` (and a role's `inherit` over a
   CLI parent is disallowed/forced).
2. RED: backend carries `api_only_models`; FE tests — content selection on launcher AND section; a role
   (extract/judge) `inherit` while parent transport CLI + api-only role model → forced api; an api-only
   **extract** in settings forces both its role transport AND `toc_transport` to api.
3. GREEN wiring all four surfaces through the resolver.
4. `pytest -k agent_models`; `npx tsc`; FE runner.
5. **Commit:** `feat(models): expose api_only_models; all 4 FE pickers force api + couple TOC transport`.

## Task 5 — Move running tools/scripts off 2.5 (repo-wide sweep + allowlist) (F3-tools/F5)

**Files:** `app/services/teaching_audit.py` (examiner default → **`gemini-3.6-flash`** exactly; student →
`gemini-3.5-flash`), `scripts/teaching_audit.py`, `scripts/golden_eval.py`, `scripts/stress_concurrency.py`
(→`gemini-3.5-flash-lite`), `scripts/toc_validate_smoke.py`, `scripts/smoke_api_vision.py`, and every
other `grep gemini-2\.5` script with a RUNNABLE default; `tests/services/test_teaching_audit*.py`.
- Repo-wide `grep -rn 'gemini-2\.5' app/ scripts/`: for EACH hit classify — (a) runnable default → repoint
  to a live model; (b) historical-repro → add `# historical repro — model intentionally pinned`;
  (c) legit (pricing/tier rows, api_transport location comment, docstring example) → leave. `api_vs_cli_compare.py`
  = (b) pinned historical (cli retired; needs a both-transport model). Record the final allowlist in the commit body.
- RED: a test asserts `teaching_audit` module examiner/student defaults ∉ `RETIRED_GEMINI_MODELS`.
- **Commit:** `chore(tools): move runnable defaults off retired 2.5; pin historical-repro allowlist`.

## Task 6 — Migration 0049: launch_defaults → target (4 model pairs + 5 transports, unconditional)

**Files:** `alembic/versions/0049_launch_defaults_3x.py`, `tests/repositories/test_launch_defaults_migration.py`.
- `upgrade`: set id=1 — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`, judge=`gemini-3.5-flash`,
  solver=`gemini-3.1-pro-preview` (all `gemini`), and content/extract/judge/toc/solver `_transport='api'`
  (**4 model pairs + 5 transports**; there is no toc MODEL column). Unconditional. `downgrade` → prod tuple.
- RED (scratch, real DB): from fresh-0048 AND prod tuples → upgrade both == target; downgrade == prod.
- **Commit:** `feat(launch-defaults): migration 0049 → 3.x flash target tuple`.

## Task 7 — Acceptance A: judge parsing (20/20) + judgment quality (imported probes) — scratch DB

Scratchpad (uncommitted), **scratch DB**:
1. Parsing: ~20 stored phases judged with `gemini-3.5-flash`/api → **20/20** valid Verdicts (else Task 7b:
   string-aware `json.JSONDecoder().raw_decode` from each `{`; RED brace-in-string + prose fixtures).
2. Quality: **import** `build_probes`/`run_probes` from `scripts/experiments/rejudge_ab.py` and call with
   `judge_provider="gemini", judge_model="gemini-3.5-flash"` (bypassing its 2.5 constant), 3× each, raws
   retained: planted contradiction major; absent-but-true never major; generated values not invented-flagged;
   constructed wrong fact major. Report pass/fail + cost.

## Task 8 — Acceptance B: full pipeline incl. TOC over the plain key — scratch DB

Scratchpad (uncommitted), **scratch DB**, fleet untouched:
1. TOC: ingest a REAL PDF; run `toc_extractor` (extract=`gemini-3.5-flash-lite`, toc_transport=api). Assert
   persisted nonempty `toc_entries`; `books.toc_validation` ∈ {`verified`,`mismatch`} (never `skipped`);
   **TWO** token-bearing `agent_usages` rows — `toc.extract`(summarize) AND `toc.validate` — both
   `gemini-3.5-flash-lite`, tokens>0, success=True.
2. Content: one homework end-to-end on the target roles; 11 phases produced+judged, solver on boss-arena,
   **zero success=False rows for this job**, every priced row for this job/book > $0; cost scoped to the
   acceptance book/job (vs ~$1.43). No mass generation.

## Task 9 — Finish

1. Offline suite + `npx tsc` green.
2. Rebase gate (#108): `git fetch`; rebase if base moved; re-apply append-only memory edits; re-run.
3. Push, PR (base `Nggaev-v2`); user/GK gates; never self-merge.
4. Worklog **0161** + INDEX; de-stale CLAUDE.md (`settings.extract_*`; 2.5 retired; api-only; retry guard),
   `HOW_IT_WORKS.md`, `CODE_MAP.md`, `DATABASE.md`; `git mv` plan → `shipped/`.
5. **Ops (operator, user-owned) — coordinated cutover:**
   1. Stop new launches + drain.
   2. Pull code on head AND every worker.
   3. **Per host, in order:** Scrub → verify local SA residue cleared (worker `active.json`/env; tombstone
      row still present) → **STOP the worker** → Unassign (unpark) → install plain `GEMINI_API_KEY` in `.env`
      → restart. (Stopping before Unassign closes the keyless-claim window.)
   4. Apply migration 0049 on the head DB; **restart head** (auto-stamps the version floor — verify it;
      `PUT /workers/version-floor` only as an override).
   5. Conservative start before reopening: aggregate `WORKER_CONCURRENCY≈3` fleet-wide (per-host 1 given
      ~5.6 job fan-out), `AGENT_MAX_CONCURRENCY=8`/process, shared-key `CREDENTIAL_MAX_CONCURRENT_GEMINI=8`.
      Run a **cheap bounded ramp on the exact 3.5/3.6 IDs** to find this key's ceiling (prior test used
      3-flash/3.1-lite) — or accept the limitation explicitly and watch 429s.
   6. One post-deploy smoke, then reopen launches.

## Explicitly out of scope

- Changing `_auth_env`/`_gemini_client` precedence. Tiered 3.1-pro pricing. Re-enabling 2.5.
- Data-migrating the 157 terminal 2.5 rows (Task-3 guards make them safe). The operator cutover itself.
