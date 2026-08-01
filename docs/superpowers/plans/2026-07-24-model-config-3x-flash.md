# Model config → 3.x flash family on the plain Gemini API key (rev 3)

## Approach & key decisions

**Goal:** move generation off the now-dead 2.5 family to the 3.x flash models the restored-$100k plain
Developer-API key serves — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without breaking cost attribution, the
CLI/API transport invariant (front AND back), retry safety, the self-grade guard, or the fleet auth.

**Facts verified against the live key + code (rev 3 folds in the composition-level gate findings):**
- All four target IDs are **callable** on the plain key (real `generate_content`, 2026-07-24). **2.5 is
  DEAD** on this key — a real request 404s ("no longer available to new users"); `list_models` lists but
  can't call it (rev-1 error corrected). Keep 2.5 `PRICE_MAP`/`_MODEL_TIER` rows (historical attribution),
  REMOVE 2.5 from `MODEL_MANIFEST`.
- The three new flash models **fail on the gemini CLI**; the manifest is shared CLI+API and api-only is
  **provider-level only** today (`API_ONLY_PROVIDERS={clodex}`; `/agent/models` emits provider-level
  `api_only`; `launcher.tsx` gates transport per-provider). So (a) backend needs a model-level api-only
  set + `validate_transport` reject, AND (b) the FE must be told, or it offers 3.5/3.6 under CLI and
  Launch 500s. Both are in scope (Tasks 1 + 4).
- **Retry re-uses the pinned model** (`jobs.py::retry_job` — "reuses the same job row (and pinned
  provider/model)"). Production holds **157 retryable 2.5-stamped rows** (115 cancelled + 42 failed,
  queried). After cutover, retrying any calls a dead 2.5 model. **Decision:** add a retry-time guard that
  refuses when a stamped model is off-manifest (retired), returning a structured 409 pointing at
  force-regenerate (which re-stamps from `launch_defaults`). The 157 terminal rows then stay safe without
  a data migration (Task 3).
- **Fleet auth transition (corrected + extended):** an SA-key assignment `env.pop("GEMINI_API_KEY")`
  (SA wins), and a **Scrub sets a tombstone that PARKS the host until a separate Unassign** (`sa-keys-panel`
  has distinct `scrub`/`unassign` mutations; UI: "SCRUB REQUESTED · HOST PARKED"). Unassigning before SA
  residue clears cancels the revoke. Correct order is Scrub → wait idle + verify residue gone → Unassign
  (unpark) → install key → restart (Task 6 §Ops).
- **`toc_validation_model` is process-loaded config** (read from `settings` at import); the version gate
  (`worker.py` "version gate: STALE", `PUT /workers/version-floor`) fences stale workers. So the cutover
  needs a **coordinated pull + head restart + floor bump**, not just `alembic upgrade` (Task 6 §Ops).
- **`validate_toc` never raises — returns `status="skipped"` on ANY failure** (hard invariant). So a TOC
  acceptance that only checks "didn't raise" proves nothing; Task 5 asserts persisted nonempty rows +
  status ∈ {verified, mismatch} + a token-bearing `agent_usages` row for `gemini-3.5-flash-lite`.
- Live per-role config = `launch_defaults` singleton. Prod row (2026-07-24): content=`gemini-3-flash-preview`,
  extract/judge=`gemini-2.5-flash`, solver=`gemini-3.1-pro-preview` (already target), all transports `api`.
  Fresh 0048 DB differs (content=`gemini-2.5-pro`, `toc_transport='cli'`, extract/judge_transport=`inherit`).
  Migration 0049 sets the full target tuple **unconditionally** (once-only; fixes both states); downgrade
  restores the prod tuple.
- **Pricing coverage must be scoped to API-billable providers** — kimi/codex/opencode are intentionally
  absent from `PRICE_MAP` (cli/free), so an all-manifest coverage test fails; scope to `API_PROVIDERS`.
- 3.5-flash passed the real judge path 5/5 (Task 4 scales to 20/20). Self-grade safe (3.6≠3.5≠3.1).
  Projected cost ≈ **$1.43/hw** (content $0.427 + extract $0.034 + judge $0.847 + solver $0.121).

**Collision / integration order:** `feat/gemini-global-default` merged/stale. **PR #108 touches
`docs/memory/INDEX.md`+`MASTER_MEMORY.md`** (Task 8's worklog files) — land those edits last, `git fetch`
right before push, rebase onto #108 if it merged first (append-only). Worklog **0161**; revision **0049**.

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2`.

---

## Task 1 — Register 3 API-only models; retire 2.5 from manifest; scoped pricing-coverage test

**Files:** `app/services/agent_models.py`, `app/services/pricing.py`, `app/services/model_tiers.py`,
`tests/services/test_agent_models.py`, `tests/services/test_pricing.py`, `tests/api/test_agent_models_tiers.py`.

1. **RED** —
   - `test_agent_models.py`: replace `test_phantom_gemini_3_5_flash_removed` with
     `test_gemini_flash_3x_api_only`: for each of `gemini-3.6-flash`/`gemini-3.5-flash`/`gemini-3.5-flash-lite`
     → `is_valid("gemini", m)` True, `validate_transport("gemini", m, "cli")` non-None (rejected),
     `validate_transport("gemini", m, "api")` None. Change `test_real_gemini_models_still_valid` to drop
     2.5 and add `test_gemini_2_5_retired_from_manifest`: `is_valid("gemini","gemini-2.5-flash") is False`.
   - `test_pricing.py`: exact-rate tests for the 3 new models; `test_every_api_billable_manifest_model_is_priced`
     iterating **only `provider in agent_models.API_PROVIDERS`** manifest pairs, asserting a `PRICE_MAP`
     entry each. (Do NOT touch historical-accounting tests that assert the OLD 2.5 rates — those stay.)
   - `test_model_tiers.py`: `tier_of("gemini","gemini-3.6-flash")==2`, `…"gemini-3.5-flash")==2`,
     `…"gemini-3.5-flash-lite")==4` (two-arg signature).
2. **GREEN** — manifest: add 3, remove the 3 2.5 IDs. Add
   `GEMINI_API_ONLY_MODELS = frozenset({"gemini-3.6-flash","gemini-3.5-flash","gemini-3.5-flash-lite"})`;
   in `validate_transport`, reject `transport=="cli"` when `model in GEMINI_API_ONLY_MODELS` with a reason.
   `PRICE_MAP`: add 3 rows (source+date comment), keep 2.5. `_MODEL_TIER`: add 3, keep 2.5.
3. `uv run python -m pytest tests/services/test_agent_models.py tests/services/test_pricing.py tests/services/test_model_tiers.py tests/api/test_agent_models_tiers.py tests/services/test_judge_resolution.py tests/integration/test_claim_gate_self_grade.py -q`. Grep for any other test asserting 2.5 selectability vs 2.5 accounting; update selection ones, preserve accounting ones (comment each).
4. **Commit:** `feat(models): 3.x flash api-only, retire 2.5 from manifest, scoped pricing coverage`.

## Task 2 — TOC-validator default off 2.5

**Files:** `app/config.py:224`, `tests/services/test_config_defaults.py`.
1. **RED** — `settings.toc_validation_model == "gemini-3.5-flash-lite"` in `test_config_defaults.py`.
2. **GREEN** — line 224 `"gemini-2.5-flash"` → `"gemini-3.5-flash-lite"`. Leave `gemini_model` (line 28) untouched (report confirms no runtime read).
3. `uv run python -m pytest tests/services/test_config_defaults.py -q`.
4. **Commit:** `chore(config): toc validator default 2.5-flash → 3.5-flash-lite`.

## Task 3 — Retry guard: refuse retired-model retries (F2)

**Files:** `app/api/v1/jobs.py` (`retry_job`), `tests/api/test_jobs_retry.py`.
1. **RED** — a job row stamped `model="gemini-2.5-flash"` (off-manifest after Task 1) → `POST /jobs/{id}/retry`
   returns **409** with a structured body naming the retired model(s) + pointing at force-regenerate; a job
   stamped with a current model retries normally (200).
2. **GREEN** — at the top of `retry_job`, collect the job's stamped `model`/`extract_model`/`judge_model`/
   `solver_model`; if any is non-null and `not is_valid(provider, model)`, raise `HTTPException(409, {...})`.
   Force-regenerate (`/generate force=True`) is unaffected (it re-stamps from `launch_defaults`).
3. `uv run python -m pytest tests/api/test_jobs_retry.py -q`.
4. **Commit:** `feat(jobs): refuse retry of jobs stamped with a retired (off-manifest) model`.

## Task 4 — Surface model-level api-only to the frontend (F3)

**Files:** `app/api/v1/jobs.py` (`/agent/models`), `web/src/lib/types.ts`, `web/src/lib/agent-models.ts`
(or wherever the models response is typed/consumed), `web/src/components/fleet/launcher.tsx`,
`web/src/components/fleet/*settings*` if it also picks transport, `web/src/**/__tests__/` FE tests.
1. **RED** — (backend) `/agent/models` response includes `api_only_models: {gemini: ["gemini-3.6-flash",
   "gemini-3.5-flash","gemini-3.5-flash-lite"]}` (derived from `GEMINI_API_ONLY_MODELS`); a backend test asserts it.
   (frontend) a launcher unit test: when an api-only model is selected, the CLI transport option is
   disabled/hidden and transport auto-pins to `api`; selecting CLI + such a model is not offerable.
2. **GREEN** — add the field to the endpoint; thread it into the FE model type; in `launcher.tsx` (and any
   settings transport picker) filter/disable CLI for `api_only_models` and auto-pin api. Follow existing
   provider-level `api_only` handling as the pattern.
3. `uv run python -m pytest tests/ -k agent_models -q`; `cd web && npx tsc -p tsconfig.app.json --noEmit`
   and the FE test runner for the launcher spec.
4. **Commit:** `feat(models): expose api_only_models; FE disables CLI + auto-pins api for them`.

## Task 5 — Migration 0049: launch_defaults → target tuple (unconditional)

**Files:** `alembic/versions/0049_launch_defaults_3x.py`, `tests/repositories/test_launch_defaults_migration.py`.
1. **`upgrade()`** — one UPDATE of id=1: content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
   judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview`, all providers `gemini`, and
   content/extract/judge/toc/solver `_transport='api'`. Unconditional (docstring says so). `downgrade()`
   restores the prod tuple (content=`gemini-3-flash-preview`, extract/judge=`gemini-2.5-flash`, solver
   unchanged, transports `api`).
2. **RED/verify** (real DB, scratch): seed to the fresh-0048 tuple AND the prod tuple; after `upgrade`
   both == target; after `downgrade` both == prod tuple (assert all 5 model + 5 transport columns).
3. `alembic upgrade head` + `downgrade -1` clean on scratch; offline suite green.
4. **Commit:** `feat(launch-defaults): migration 0049 → 3.x flash target tuple`.

## Task 6 — Acceptance A: judge 20/20 (scratch DB)

Scratchpad script (uncommitted) against a **scratch DB** (judge writes `agent_usages`): copy ~20 stored
phases + extracts (every phase type, ≥4 subjects); judge each with `judge_model="gemini-3.5-flash"`,
api. **Bar: 20/20 valid parsed Verdicts**; record retry count; report cost. If < 20/20 → Task 6b.

## Task 6b — (CONDITIONAL) robust Verdict extraction

**Files:** `app/services/agent.py` (before `model_validate_json`), `tests/services/test_agent_schema_parse.py`.
Use `json.JSONDecoder().raw_decode(candidate, i)` scanning from each `{` position (string-aware — a hand
brace-counter breaks on `{"a":"}"}`) to recover the first valid object; fall back to the fence-stripped
candidate; keep the one-retry. RED with prose-wrapped + brace-in-string fixtures. Re-run Task 6 → 20/20.
**Commit:** `fix(agent): string-aware JSON recovery for structured verdicts`.

## Task 7 — Acceptance B: full pipeline incl. TOC over the plain key (scratch DB)

Scratchpad script (uncommitted) on a **scratch DB**, fleet untouched:
1. **TOC (F5-strengthened):** ingest a REAL PDF (copy a book row + `source.pdf`, clear its toc rows); run
   `toc_extractor` (extract=`gemini-3.5-flash-lite`, toc_transport=api). Assert: **persisted nonempty
   `toc_entries` rows**; `books.toc_validation` status ∈ {`verified`,`mismatch`} (**never `skipped`** —
   skipped means the validator silently failed); a `agent_usages` row for `gemini-3.5-flash-lite` with
   **prompt+output tokens > 0** and `success=True`.
2. **Content:** generate ONE homework end-to-end (`pipeline.run` in-process) on the target roles. Assert
   all 11 phases produced + judged, solver ran on boss-arena, **zero `agent_usages` rows with success=False**
   for this job, and every priced row for this job/book costs > $0 (scope cost to the acceptance book/job,
   not "every conceivable row"). Report total `cost_usd` (sanity vs ~$1.43).
No mass generation. Report cost.

## Task 8 — Finish

1. Full offline suite green; `cd web && npx tsc -p tsconfig.app.json --noEmit`.
2. **Rebase gate (#108):** `git fetch origin`; if `origin/Nggaev-v2` moved, rebase and re-apply the
   worklog/INDEX appends (append-only); re-run suite.
3. Push, open PR (base `Nggaev-v2`) — user/GK gates; never self-merge.
4. Worklog **0161** + INDEX row; de-stale **CLAUDE.md** (stale `settings.extract_*` line; note 2.5 retired,
   defaults 3.x, model-level api-only), `HOW_IT_WORKS.md` + `CODE_MAP.md` (models/pricing/api-only/retry
   guard), `DATABASE.md` (launch_defaults defaults); `git mv` plan → `shipped/`.
5. **Ops (operator, user-owned) — coordinated cutover order (NOT "just edit .env"):**
   1. **Stop new launches + drain** in-flight jobs.
   2. **Pull new code on head AND every worker.**
   3. Per host: **Scrub** SA assignment → wait until idle + **verify SA residue cleared** (no `active.json`
      SA creds, assignment gone) → **Unassign** to **unpark** the host.
   4. Install the plain `GEMINI_API_KEY` in every `.env` (head + workers).
   5. **Apply migration 0049** on the head DB.
   6. **Restart head, then the fleet**; **bump the version floor** (`PUT /workers/version-floor`) and verify
      every worker passes it (stale workers fenced).
   7. One post-deploy smoke (a single lesson) before **reopening launches**.
   8. Note: fleet now shares ONE credential → one concurrency/rate lane (was N SA lanes); watch 429s.

## Explicitly out of scope

- Changing `_auth_env`/`_gemini_client` credential precedence (fights the SA limiter design).
- Tiered-pricing for 3.1-pro (<200k; flat entry correct). Re-enabling 2.5 (dead on key).
- Data-migrating the 157 terminal 2.5 rows (the Task-3 retry guard makes them safe as-is).
- The operator cutover itself (documented, user-owned).
