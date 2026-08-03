# Model config → 3.x flash family on the plain Gemini API key (rev 7)

## Approach & key decisions

**Goal:** move generation off the dead 2.5 family to the 3.x flash models the restored-$100k plain
Developer-API key serves — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without breaking cost attribution, the
CLI/API transport invariant (front AND back), ANY job-reactivation path (incl. saved-work honesty),
running tools, the self-grade guard, or the fleet auth cutover.

**Facts verified against the live key + code:**
- 2.5 is DEAD on the key (real 404). Keep 2.5 priced/tiered (historical), remove from `MODEL_MANIFEST`.
  `RETIRED_GEMINI_MODELS = {"gemini-2.5-pro","gemini-2.5-flash","gemini-2.5-flash-lite"}`; retirement
  requires `provider=="gemini" AND model in RETIRED_GEMINI_MODELS`.
- `HomeworkJob` has `provider`+`model` (content role) + `extract_/judge_/solver_provider`+`model`; the
  evaluator iterates exactly these four pairs.
- New flash models fail on gemini CLI; api-only is provider-level only today → add a model-level api-only
  set + backend reject AND all four FE surfaces + the extract↔TOC-transport coupling.
- THREE reactivation paths keep the pinned model; 144/157 2.5 rows are batched (6 batches); preview
  reports `resumable` and the UI promises phase reuse → retired handling must be consent-based, not silent.
- Running tools default to dead 2.5 (`teaching_audit.py` + ~13 scripts) → repo-wide sweep + allowlist.
- Judge acceptance must prove judgment, not parsing → import the merged probe harness with judge=3.5-flash.
- `_SOLVER_PHASES = (memory-check, practice-error-detection, practice-rlc, boss-arena)` — FOUR phases;
  acceptance asserts the per-operation model for all four solve:* calls, not just boss-arena.
- `launch_defaults` = 4 provider/model pairs + 5 transports; TOC-validator MODEL is process config.
  `validate_toc` returns `skipped` on any failure (never raises). Pricing coverage scoped to `API_PROVIDERS`.
- **Concurrency — MEASURED, not inherited:** the old "~8 ceiling" was the **Vertex per-project quota**;
  this plain Developer-API key is a different regime. Bounded ramps (2026-08-03), 0 × 429 everywhere:
  `gemini-3.5-flash` and `gemini-3.6-flash` → 8/16/32/64/**128** concurrent all clean; the SOLVER model
  `gemini-3.1-pro-preview` → 4/8/16/**32** concurrent all clean, all ≤60s, p50 ~5s (this **disproves** the
  "Pro borderline at 8" claim on this key). So the fleet must NOT be capped at 8. **But two real
  load-composition limits remain, so the cap follows the SLOWEST realistic path, not the burst ceiling:**
  (1) these ramps used *tiny* payloads — real content calls run p95 ~94s and real Pro solver calls
  p50 ~13s/p95 ~51s/max ~594s (production), so slow calls hold shared slots long; (2) with a shared cap +
  `CREDENTIAL_SLOT_WAIT_SECONDS=120`, total fleet demand must not so exceed the cap that queued calls
  can't drain within 120s (they'd park). Sizing rule: keep `hosts × AGENT_MAX_CONCURRENCY ≈
  CREDENTIAL_MAX_CONCURRENT_GEMINI`. **`AGENT_MAX_CONCURRENCY` footgun:** `_effective_concurrency()`
  (`agent.py:235`) treats the value **8** as "use legacy `GEMINI_MAX_CONCURRENCY`" — so `AMC=8` on a host
  with a stale `GEMINI_MAX_CONCURRENCY=1` stays serialized. Use a NON-8 explicit value (4) and REMOVE
  `GEMINI_MAX_CONCURRENCY` from every `.env`. Never set AMC=1 (serializes each job's DAG phase wave).
- Pricing (Google Standard Developer API, pinned in Task 1 RED): 3.6-flash `1.50/7.50/0.15`, 3.5-flash
  `1.50/9.00/0.15`, 3.5-flash-lite `0.30/2.50/0.03` (input/output/cache-read per 1M). Cost ≈ **$1.43/hw**.

**Collision:** branch plan-only at **4d875ac**; base `origin/Nggaev-v2` a80cac3; PR #108 overlaps only the
append-only memory docs (Task 9). Worklog **0161**; revision **0049**.

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2`.

---

## Task 1 — Register 3 API-only models; retire 2.5; scoped pricing; retired set

**Files:** `agent_models.py`, `pricing.py`, `model_tiers.py`, `tests/services/test_agent_models.py`,
`tests/services/test_pricing.py`, `tests/services/test_model_tiers.py`, `tests/api/test_agent_models_tiers.py`.
- RED (pin EXACT rates): `cost_usd` for 1M input/output/cache each → 3.6-flash **1.50/7.50/0.15**,
  3.5-flash **1.50/9.00/0.15**, 3.5-flash-lite **0.30/2.50/0.03**; 3 models `is_valid` True +
  cli-rejected/api-allowed; `is_valid("gemini","gemini-2.5-flash") is False`;
  `test_every_api_billable_manifest_model_is_priced` scoped to `API_PROVIDERS`; `tier_of` values.
- GREEN: manifest +3/−3; `GEMINI_API_ONLY_MODELS`+`validate_transport` reject; `RETIRED_GEMINI_MODELS`;
  price+tier rows w/ source+date comment (keep 2.5); update selection tests, preserve accounting tests.
- **Commit:** `feat(models): 3.x flash api-only, retire 2.5, retired-set, scoped pricing`.

## Task 2 — TOC-validator default off 2.5

`app/config.py:224` → `gemini-3.5-flash-lite`; RED in `tests/services/test_config_defaults.py`.
**Commit:** `chore(config): toc validator default → gemini-3.5-flash-lite`.

## Task 3 — Retirement guard across all 3 reactivation paths + honest responses + UI (F1/F2/F3)

**Files:** `app/services/job_reactivation.py` (new), `app/api/v1/jobs.py`, `app/api/v1/batch.py`,
`app/repositories/jobs.py`, `web/src/lib/types.ts`, `web/src/components/fleet/launcher.tsx`, tests
`tests/services/test_job_reactivation.py`, `tests/api/test_jobs_retry.py`,
`tests/api/test_batch_resume_retired.py`, `tests/api/test_batch_preview_retired.py`, FE dialog/toast tests.
1. **Evaluator** `retired_models_in_job(job)` over the 4 pairs; retired = `provider=="gemini" and model in
   RETIRED_GEMINI_MODELS`. Tests: each role trips; null skipped; non-gemini same-string does NOT trip.
2. `retry_job` → 409 structured.
3. `resume_failed_in_batch` → skip retired, re-enqueue rest, return `{resumed, skipped_retired:[ids]}`;
   endpoint `{jobs_resumed, jobs_skipped_retired:[str]}`; TS type + both toasts show skipped.
4. **Preview**: three DISJOINT counts — `resumable` (live-model saved work), `retired` (retired-model saved
   work), `empty`/`new` — plus mixed handling. **relaunch-resume** of retired saved work → **409**.
5. **UI (F2, explicit):** the resume/relaunch dialog renders the disjoint counts and states plainly
   "N saved lessons use a retired model and CANNOT be resumed; choosing **Discard & regenerate** will
   regenerate all selected saved jobs." FE tests for **retired-only** and **mixed live+retired** cases
   (assert the copy + that Resume is blocked / Discard regenerates) — a bare post-click 409 is not enough.
6. Green all + `npx tsc`.
7. **Commit:** `feat(jobs): retired-model guard across reactivation paths + consent UI`.

## Task 4 — Model-level api-only across all four FE surfaces + provider-aware resolver (F2-FE/F4)

**Files:** `app/api/v1/jobs.py` (`/agent/models`→`api_only_models`), `web/src/lib/types.ts`,
`web/src/lib/model-transport.ts` (new), `launcher.tsx`, `section.tsx`, `RoleAgentControls.tsx`,
`settings.tsx`, FE tests.
- Resolver `resolveTransport({provider, model, currentTransport, parentTransport, apiOnlyModels})
  -> {effective, forced}`. RED: backend field; content selection on launcher AND section; a role
  (`extract`/`judge`) `inherit` while parent CLI + api-only role model → forced api; an api-only
  **extract** in settings forces its role transport AND `toc_transport` to api. GREEN all four via the resolver.
- `pytest -k agent_models`; `npx tsc`; FE runner.
- **Commit:** `feat(models): expose api_only_models; all 4 FE pickers force api + couple TOC`.

## Task 5 — Move tools/scripts off 2.5 (repo-wide sweep + allowlist)

teaching_audit examiner→**`gemini-3.6-flash`** (exact), student→`gemini-3.5-flash`; runnable script
defaults → live models (`stress_concurrency`→`gemini-3.5-flash-lite`, `toc_validate_smoke`/`smoke_api_vision`→
`gemini-3.5-flash-lite`, `golden_eval`/`teaching_audit` scripts → live). `api_vs_cli_compare.py` = pinned
historical (cli retired; needs both-transport model) with a comment. Repo-wide `grep -rn 'gemini-2\.5'
app/ scripts/`: each hit → repoint / pin-historical-comment / leave-legit (pricing, tier, api_transport
location comment, docstring). RED: teaching_audit module defaults ∉ `RETIRED_GEMINI_MODELS`. Record the
allowlist in the commit body. **Commit:** `chore(tools): runnable defaults off retired 2.5 + historical allowlist`.

## Task 6 — Migration 0049: launch_defaults → target (4 pairs + 5 transports)

`upgrade` id=1 → content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`, judge=`gemini-3.5-flash`,
solver=`gemini-3.1-pro-preview` (all `gemini`), content/extract/judge/toc/solver `_transport='api'`.
Unconditional. **`downgrade` explicit tuple** (the current live row): content=`gemini-3-flash-preview`/api,
extract=`gemini-2.5-flash`/api, judge=`gemini-2.5-flash`/api, solver=`gemini-3.1-pro-preview`/api,
toc_transport=`api`. RED (scratch real DB): fresh-0048 AND prod tuples → upgrade==target; downgrade==prod.
**Commit:** `feat(launch-defaults): migration 0049 → 3.x flash target tuple`.

## Task 7 — Acceptance A: judge parsing (20/20) + judgment quality (imported probes) — scratch DB

1. Parsing: ~20 stored phases judged `gemini-3.5-flash`/api → **20/20** valid Verdicts (else Task 7b:
   string-aware `json.JSONDecoder().raw_decode`; RED brace-in-string+prose fixtures).
2. Quality: import `build_probes`/`run_probes` from `rejudge_ab.py`, call with `judge_model="gemini-3.5-flash"`,
   3× each, raws retained: planted contradiction major; absent-but-true never major; generated values not
   invented-flagged; constructed wrong fact major. Report pass/fail + cost.

## Task 8 — Acceptance B: full pipeline incl. TOC — scratch DB (F3: prove ROUTING)

1. TOC: ingest a REAL PDF; run `toc_extractor` (extract=3.5-flash-lite, toc_transport=api). Assert persisted
   nonempty `toc_entries`; `books.toc_validation` ∈ {verified,mismatch} (never skipped); TWO token-bearing
   `agent_usages` rows — `toc.extract`(summarize) AND `toc.validate` — both `gemini-3.5-flash-lite`, tokens>0.
2. Content: one homework end-to-end; assert **operation→model routing from `agent_usages`** (not just >$0):
   every `phase.run` content op → `gemini-3.6-flash`; the extract op → `gemini-3.5-flash-lite`; all 11
   `judge:*` ops → `gemini-3.5-flash`; **all four `solve:*` ops** (memory-check, practice-error-detection,
   practice-rlc, boss-arena) → `gemini-3.1-pro-preview`. Plus zero success=False rows for this job; report
   `cost_usd` scoped to the job/book (vs ~$1.43). No mass generation.

## Task 9 — Finish

1. Offline suite + `npx tsc` green.
2. Rebase gate (#108): `git fetch`; rebase if base moved; re-apply append-only memory edits; re-run.
3. Push, PR (base `Nggaev-v2`); user/GK gates; never self-merge.
4. Worklog **0161** + INDEX; de-stale CLAUDE.md (`settings.extract_*`; 2.5 retired; api-only; retry guard),
   `HOW_IT_WORKS.md`, `CODE_MAP.md`, `DATABASE.md`; `git mv` plan → `shipped/`.
5. **Ops (operator, user-owned) — coordinated cutover:**
   0. **Pre-flight assertion (F4):** immediately before migration/restart, query
      `SELECT count(*) FROM homework_jobs WHERE status IN ('pending','running','cancelling') AND (…2.5
      stamp on any role…)` and require **0** — so nothing launched after planning slips through with a
      retired stamp (currently 0 active; 115 cancelled / 42 failed are terminal + Task-3-guarded).
   1. Stop new launches + drain.
   2. Pull code on head AND every worker.
   3. **Per host, in order:** Scrub → verify local SA residue cleared (worker `active.json`/env; tombstone
      row still present) → **STOP the worker** → Unassign (unpark) → install plain `GEMINI_API_KEY` in `.env`
      → restart. (Stopping before Unassign closes the keyless-claim window.)
   4. Apply migration 0049 on head; **restart head** (auto-stamps version floor — verify; PUT only to override).
   5. **Concurrency — evidence-based provisional (NOT the stale cap-8; sized so demand ≈ cap so slow real
      calls can't overrun the 120s slot-wait). First REMOVE `GEMINI_MAX_CONCURRENCY` from every `.env`
      (else `AMC=8` silently falls back to it — `agent.py:235`), then set:**
      - Workers (×N): `WORKER_CONCURRENCY=2`, `AGENT_MAX_CONCURRENCY=4` (explicit non-8 → honored, avoids
        the sentinel; still parallelizes the DAG wave), `CREDENTIAL_SLOT_WAIT_SECONDS=120`.
      - Shared-key fleet cap: `CREDENTIAL_MAX_CONCURRENT_GEMINI=32` — the single binding fleet-wide limit.
        Sizing: `hosts × AMC ≈ cap` (e.g. 8 hosts × 4 = 32); for ~10 hosts this is a mild 40→32
        oversubscription that drains inside 120s.
      - Head: `WORKER_CONCURRENCY=0`, `AGENT_MAX_CONCURRENCY=4`, `CREDENTIAL_MAX_CONCURRENT_GEMINI=32`.
      - **After restart, VERIFY effective per-process concurrency is actually 4** (not silently 1 from a
        leftover legacy var).
      - **Before a large campaign, run a REAL-payload solver Pro ramp (4/8/16/32) + a mixed-model sustained
        run** (content+judge+solver together over several minutes) — the tiny-payload burst ramps proved the
        provider doesn't 429, but not sustained mixed-output slot-hold behavior. **Raise together** to
        `AGENT_MAX_CONCURRENCY=8` + `CREDENTIAL_MAX_CONCURRENT_GEMINI≈64` only once that run is clean (never
        raise AMC while the cap stays 32 — that just deepens the queue).
   6. One post-deploy smoke, then reopen launches.

## Explicitly out of scope

- Changing `_auth_env`/`_gemini_client` precedence. Tiered 3.1-pro pricing. Re-enabling 2.5.
- Data-migrating the 157 terminal 2.5 rows (Task-3 guards make them safe). The operator cutover itself.
