# Phase 4 — CLI | API transport toggle: operator acceptance runbook

**Status:** the Phase-4 code shipped + merged (worklog 0053, 7 task commits). The
**free, in-band** acceptance gates were run during implementation and passed (see
worklog 0053 / below). This runbook documents the **billed, operator-run** gates
that the implementation deliberately did **not** execute — they require real API
keys, real spend, and a real worker PC with `~/.gemini/settings.json` changes.

> Spec: `docs/superpowers/specs/2026-06-10-fleet-phase4-transport-toggle-design.md`
> (§7 acceptance, §3 deployment ordering, §9b cache-row exemption).
> Plan: `docs/superpowers/plans/2026-06-11-fleet-phase4-transport-toggle.md` (Task 8).

---

## What was already proven for free (no action needed)

- **Full suite green** at baseline: `5 failed (pre-existing Notion-only) / 362 passed / 35 skipped`.
- **Mode-isolation** (`tests/services/test_auth_env.py`), **failover-restriction**
  (`test_failover_api.py`), **loud judge auth** (`test_judge_auth.py`),
  **execute-phase api auth** (`test_execute_phase_api_auth.py`), **pricing**
  (`test_pricing.py`) — all pass DB-free.
- **Real-DB** schema/claim-gate/validation: `tests/integration/test_transport_schema.py`,
  `tests/integration/test_claim_contention.py`, `tests/api/test_transport_validation.py`
  — all pass against a migrated Postgres.
- **Free invalid-key 401 proof** (spec §7.1 partial, zero cost): one in-process
  `agent.run_phase(provider="claude", transport="api", ...)` with a deliberately
  invalid `ANTHROPIC_API_KEY` returned `api_error_status: 401` /
  `"Invalid API key · Fix external API key"`, `total_cost_usd: 0`,
  `output_tokens: 0` — the call hit the API, **no subscription fallback, zero
  spend**, surfaced as a loud `RuntimeError`. Proves the claude api adapter
  injects the key directly (no OAuth fall-through) and a bad key fails closed.

---

## ⚠️ Pre-flight ordering (spec §3) — do this in order or you break gemini

The cli baseline injects `GOOGLE_GENAI_USE_GCA=true` into **every** gemini spawn
(job or not). Once `security.auth.selectedType` is removed from
`~/.gemini/settings.json`, headless gemini has **no persisted auth** and relies
entirely on that env var. Therefore, **on each worker PC:**

1. **Ship the Phase-4 code first** (this branch / image), so the GCA-injecting
   `_auth_env` is live.
2. **Only then** remove `security.auth.selectedType` from `~/.gemini/settings.json`
   on that PC. Also confirm `security.auth.enforcedType` is **unset** (it *throws*
   on mismatch).
3. **Removal-first is a footgun:** removing `selectedType` before the code is live
   instantly breaks *all* gemini calls on that PC ("Please set an Auth method…").
4. The worker logs a **loud startup warning** if `selectedType` is still present —
   an interactive `gemini` re-persists it and silently re-routes "api" calls to the
   subscription. Heed it.

## ⚠️ Worker env setup (the claim gate)

Credential BOTH provider sides on every worker, via the worker's compose/env file
(never committed):

```
ANTHROPIC_API_KEY=...          # claude side (judge + claude content)
GEMINI_API_KEY=...             # gemini side, AI Studio key…
# …OR the Vertex service-account pair instead (see the fleet-api-6 section):
# GOOGLE_APPLICATION_CREDENTIALS=/path/sa.json
# GOOGLE_CLOUD_PROJECT=<project-id>
```

**Since Phase 4.1, "both sides" is no longer a blanket rule** — the worker
computes per-side capabilities at startup (`_compute_capabilities`) and
`claim_next_job` routes per the job's *resolved role transports* (see the
"Phase 4.1 — per-role transports" section below). Rule of thumb: credential
every side whose role can resolve to `api` on jobs you want this worker to
claim. The gemini side is satisfied by EITHER form (an explicit
`GEMINI_API_KEY` wins when both are set); a worker missing a needed side
simply **never claims those jobs** and logs which side is absent. cli jobs
are unaffected.

These are read from the **process environment** and `.env` works everywhere:
compose `env_file:`/`environment:` export it, and on bare metal the app loads
`.env` at startup (`config.py` `load_dotenv`; an exported variable always wins).

---

## Gate §7.1 — live valid-key smoke (claude + gemini) — BILLED

With real keys set on the worker, run one headless call per provider in `api` mode
and confirm all three properties:

- **claude:** one `transport=api` generation (or a one-phase smoke) →
  - the call **bills the Anthropic account** (visible in the Anthropic console
    usage), not the Max subscription;
  - the JSON envelope still carries token stats (`input_tokens`/`output_tokens`)
    in api mode;
  - the `agent_usages` row has `auth_mode=api` and a **nonzero `$`** in the
    `/agent/stats` per-transport rollup.
- **gemini:** one `transport=api` call →
  - it **bills the Google AI Studio key** (visible in AI Studio), not GCA OAuth;
  - confirm at runtime that **`selectedType` removed + `GEMINI_API_KEY` set
    actually selects API-key auth** (the source-verified resolution: GCA wins if
    `GOOGLE_GENAI_USE_GCA=true`, which `_auth_env` does **not** set in api mode);
  - envelope carries token stats; `auth_mode=api`; nonzero `$`.

## Gate §7.2 — post-removal upload smoke (cli baseline) — FREE-ish

After `selectedType` is removed and the code is live, do a **plain book upload →
TOC extraction** (no job context). It must **still succeed** — this is the
load-bearing proof of the unconditional cli baseline (§3): TOC at upload has no
job, so it relies entirely on the default-`cli` `_auth_env` injecting GCA. If TOC
extraction fails post-removal, the cli baseline is broken — stop and investigate
before running any api job.

## Gate §7.5 — end-to-end api attribution + `$` (spec §9b wording) — BILLED

Generate **one real lesson `transport=api`** end to end, then inspect
`agent_usages` for that job:

- **Every non-`<cache>` row** (extract + content phases + judge) has
  `auth_mode=api`.
- The `/usage` (and `/agent/stats`) per-transport `$` readout for the job is
  **nonzero**.
- **Cross-job extract-reuse `<cache>` rows correctly stay `auth_mode=cli`** — they
  are free `$0` reuse markers and are *exempt* from the "every row = api" check
  (spec §9b). Do **not** treat a `cli` `<cache>` row as a regression.
- **For a clean cost benchmark, use a fresh-extract book (or `force`).** If the api
  run reuses a prior cli extract, its extract cost reads `$0` / unmeasured (the
  `<cache>` row), under-reporting the true api cost of the lesson.

---

## ⚠️ Gemini prices are UNVERIFIED — confirm before trusting the gemini `$`

`app/services/pricing.py` carries a static `(provider, model) → $/Mtok` map.

- **Claude prices are VERIFIED** (e.g. Opus $5 in / $25 out — the *current* rate,
  not the deprecated $15/$75 the project mis-priced once).
- **Gemini prices are best-effort and tagged `VERIFY`** in the source. Before
  trusting the api `$` readout for gemini, **confirm each gemini entry against
  current Google pricing** and drop the `VERIFY` tag once checked. A stale price
  silently corrupts exactly the cost readout this feature exists to produce.

---

## Deferred (tracked in WISHLIST as `fleet-api-*`)

`fleet-api-1` batch-discount transport · `fleet-api-2` credential rotation pool ·
`fleet-api-3` cost ledger + budget kill-switch · `fleet-api-4` never-pay-twice
idempotency · `fleet-api-5` codex API mode. None are required for this phase's
real-time toggle.

## Gemini via Vertex AI service account (fleet-api-6, shipped 2026-06-11)

The gemini side of `transport=api` accepts EITHER `GEMINI_API_KEY` (AI Studio) OR a Vertex service account:
`GOOGLE_APPLICATION_CREDENTIALS=/path/sa.json` + `GOOGLE_CLOUD_PROJECT=<project>` (+ optional `GOOGLE_CLOUD_LOCATION`, defaults to `global` — regional endpoints may 404). An explicit `GEMINI_API_KEY` wins when both are present. The worker claim gate accepts either form. Requirements: the persisted `security.auth.selectedType` must be removed from `~/.gemini/settings.json` (same §3 rule as everything else), and the SA's GCP project must have Vertex AI enabled with the SA holding Vertex AI User. Verified live 2026-06-11 (gemini-cli 0.46.0): in-process `run_phase(transport="api")` with only SA creds → Vertex-billed generation, `agent_usages.auth_mode=api`.

---

## Phase 4.1 — per-role transports (extract / judge), shipped 2026-06-11

Phase 4's single `transport` switched the **whole job**. Phase 4.1 adds two
per-role overrides on every job (and batch): `extract_transport` and
`judge_transport`, each `inherit | cli | api` (default `inherit`). Resolution is
`resolve_role_transport` (`app/services/agent_models.py`): **`inherit` follows
the job's `transport`; an explicit value wins.** Content phases always follow
the job-level `transport`. The FE exposes the two overrides as
Extract/Judge "billing" selects (Auto / CLI / API) next to the existing
transport toggle.

The motivating split: run content on the gemini API (metered, parallel-safe)
while keeping the expensive claude judge + extract on the subscription — or the
inverse. Every `agent_usages` row carries the `auth_mode` the spawn actually
used, so the `$` rollups stay per-role-accurate.

### Capability routing (claim gate v2)

The Phase-4 all-or-nothing `has_api_keys` gate is gone. At startup the worker
computes per-side capabilities (`worker._compute_capabilities` →
`worker.CAPABILITIES`): `can_claude_api` (`ANTHROPIC_API_KEY`),
`can_gemini_api` (`GEMINI_API_KEY` OR the Vertex SA pair), plus role-level
flags derived from the configured judge/extract providers (`judge_api_ok`,
`judge_fallback_api_ok`, `extract_api_ok`). `claim_next_job` ANDs each job's
**resolved** role transports against those flags, so:

- a worker with **only gemini** creds can claim an api-content gemini job with
  cli judge/extract — no Anthropic key needed;
- a worker missing a needed side **never claims the job** (fail-fast at claim
  time, before any spawn — this also covers extract failover);
- a half-configured worker logs a startup warning naming the missing side
  (`api capability: claude=… gemini=…`) instead of refusing all api jobs.

Two subtleties baked into the gate (so operators don't rediscover them):
the **§4a judge self-fallback** — jobs generating ON the configured judge pair
are judged by `model_tiers._SELF_FALLBACK`, so the gate checks *that*
provider's capability for exactly those jobs; and the **NULL-model coalesce** —
`coalesce(model, '')` in the judge-pair comparison, because bare SQL
`NULL = 'x'` is NULL and silently excluded model-NULL jobs whose provider
matched the judge provider (empirically proven by
`test_null_model_job_claims_via_not_pair_branch`). The worker also warns at
startup if `JUDGE_MODEL == default_model(JUDGE_PROVIDER)`, which would open the
NULL-model edge.

Credential mispredictions downstream of the gate raise a **typed
`AuthEnvError`** (loud, classified by `isinstance`, never a silent OAuth
fallback). The pipeline's regen/judge guard fails the phase loudly when the
resolved content **or** judge transport is api and the error classifies as
auth (`pipeline.py` — `transport == "api" or judge_transport == "api"`).

### ⚠️ Reviewer note — re-billing the same lesson needs `force=true`

Role transports are **NOT part of the dedup natural key** (spec §8). Submitting
the same section again with different Extract/Judge billing (e.g. flipping the
judge from cli to api to benchmark cost) returns the **existing** job — your new
billing choice is silently ignored. Pass `force=true` on `POST /generate` (or
the batch endpoint) to actually re-run with the new role transports. Operators
WILL hit this and wonder why the `$` didn't move.

### ⚠️ gemini-cli dotenv self-poisoning — REQUIRED worker setting (live incident, 2026-06-12)

gemini-cli **dotenv-loads the nearest project `.env` inside every spawn**
(walking up from the cwd), and `GOOGLE_CLOUD_PROJECT` + `GEMINI_API_KEY` are on
its auth-var whitelist (imported even in untrusted folders). This happens
*inside the child process*, AFTER `_auth_env` scrubbed the parent env — so a
worker whose repo `.env` carries Vertex creds for api mode has every **cli**
gemini spawn re-scoped to that GCP project → `403 Cloud Code Private API has
not been used in project …` (the 2026-06-12 2-PC batch failure on the head PC;
proven by: same call, `.env` renamed away → success). It is also a latent
ambient-credential hole (a cli spawn could silently pick up `GEMINI_API_KEY`).

**Required on every worker PC (gemini-cli ≥ 0.46 — pin versions, fleet-ops-1):**
in `~/.gemini/settings.json` add:

```json
{ "advanced": { "ignoreLocalEnv": true } }
```

`_auth_env` injects everything a spawn needs explicitly, so the CLI has no
business reading `.env`. Do NOT try `--ignore-env` as an argv flag — it appears
in the CLI source (`loadEnvironment`) but is **not a registered option**; yargs
hard-fails with "Unknown arguments". The worker logs a startup warning
(`_warn_if_gemini_reads_local_env`) when the setting is absent. Also beware on
Windows: PowerShell 5.1 `Out-File -Encoding utf8` writes a **BOM** and
gemini-cli rejects BOM'd `settings.json` (exit 52) — write the file BOM-less.

### ⚠️ Vertex + persisted gemini OAuth (live finding, 2026-06-11)

On a host whose `~/.gemini/settings.json` carries
`security.auth.selectedType: "oauth-personal"`, gemini-cli 0.46.0 lets the
persisted selection **override** the `GOOGLE_GENAI_USE_VERTEXAI=true` env signal
`_auth_env` injects — the api-transport spawn routes to Cloud Code OAuth and
fails loudly (`Cloud Code Private API has not been used in project …`), recorded
as `success=False` with `auth_mode=api` (it does NOT silently bill the wrong
account). Same §3 rule as Phase 4: remove `selectedType` on worker hosts before
running api jobs; the startup warning (`_warn_if_gemini_selected_type`) flags
it.

### In-band acceptance (run 2026-06-11, this Mac)

- DB-free suite: `394 passed, 46 skipped`.
- Real-DB (`edu_copy`): `test_transport_schema.py` + `test_claim_contention.py`
  + `test_transport_validation.py` → `34 passed`.
- **Live mixed-billing smoke (§9.3, the user's case — job api + roles cli):**
  one real gemini `run_phase_prompt(transport="api")` (Vertex SA, no
  ANTHROPIC_API_KEY in env) + one real claude judge-shaped
  `run_phase(transport="cli")` (subscription) → `agent_usages`: gemini row
  `auth_mode=api, success=True`, claude row `auth_mode=cli, success=True`.
  Mixed per-row attribution proven end-to-end on real spawns.

### Operator leg (§9.4) — BILLED, not yet run

The remaining acceptance leg needs an `ANTHROPIC_API_KEY` and real spend:
submit **one opus-content job with `judge_transport=api`** end-to-end on a
worker credentialed for **both** sides (or gemini-only, to watch the gate
refuse it first), and confirm: the content rows bill the chosen content
transport, the judge rows show `auth_mode=api` with nonzero `$` in
`/agent/stats`, and a gemini-only worker never claims it. Gate-refusal and
loud-AuthEnvError behavior are already covered in-band (real-DB claim matrix +
unit tests); this leg is the billed end-to-end confirmation.
