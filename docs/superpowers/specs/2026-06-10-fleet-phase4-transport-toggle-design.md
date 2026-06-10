# Fleet Phase 4 — CLI | API transport toggle (design)

**Date:** 2026-06-10 · **Branch:** `feat/autonomous-fleet-engine` · **Status:** spec for user review

## 1. Goal

Let the operator choose, per job (and per batch launch), whether generation runs on
**CLI subscription auth** (today's behavior, $0 marginal, rate-capped) or **API-key
auth** (pay-per-token, uncapped). Purpose: empirically benchmark CLI-vs-API and
Claude-vs-Gemini for mass generation. This phase ships the **real-time toggle only**;
batch-discount transport is deferred.

**Scope decisions (locked with user 2026-06-10):**
- API mode supports **claude (Anthropic)** and **gemini (Google)** only.
- **codex is explicitly deferred** (user: "defer codex for now, only Anthropic and
  Gemini API must be supported"). Mechanism for later pickup tracked as
  `fleet-api-5` (§8).
- kimi / opencode: CLI-only, no API mode planned.

## 2. The toggle

- New job field **`transport`**: enum **`cli | api`**, default `cli` (today's
  behavior unchanged). Enum, not bool, so a future `batch` transport slots in
  without a migration.
- Batch launch (`POST /jobs/batch`) accepts `transport` and applies it to every
  job it creates. **Store `transport` on the `batches` row too** — the fleet
  drill-in/badge shouldn't have to infer it from member jobs.
- **Validation on `POST /generate` (and batch launch):**
  - `transport=api` rejected unless provider ∈ {claude, gemini}.
  - `transport=api` **requires an explicit manifest model** (no `model=None`).
    Verified rationale: `is_valid()` allows `model=None` (`agent_models.py:43-45`)
    and `_PROVIDER_DEFAULT_MODEL` has `gemini: None` (`agent.py:76-85`) → a
    model-less gemini job lets the CLI pick its own default, which may differ
    between OAuth and API-key auth — silently corrupting the CLI-vs-API
    comparison this feature exists to make.
- `MODEL_MANIFEST` gains an `api_supported` flag per provider; the frontend shows
  the toggle only when the picked provider supports it, and forces a concrete
  model selection when API is picked.

## 3. Transport scope — what the toggle covers

**`transport` applies to every CLI spawn belonging to the job:**
- the per-lesson **extract** phase (pinned to `settings.extract_provider`/`
  extract_model` at `pipeline.py:649-650` — the *provider/model* pin stays;
  only the auth mode follows the job),
- all **content phases**,
- the **LLM judge** (`phase_judge.py:115` → `model_tiers.judge_model_for`,
  `config.py:86-87`). The Opus judge is the largest Claude cost line (~$0.86/hw
  per the verified 2026-06-09 cost basis); if it stayed on subscription, the
  property "API jobs don't drain the Max pool" would be false.

**The cli-mode env is the unconditional baseline for EVERY spawn — job or not.**
Once `selectedType` is removed from `~/.gemini/settings.json` (the one-time
setup in §4), headless gemini has **no configured auth** and errors out
("Please set an Auth method…", bundle `:15422-15424`) unless an env var
supplies one. So book-level TOC extraction at upload — and any other gemini
spawn outside a job — would break outright if the cli env were only applied to
jobs. Therefore: the adapter applies the cli-mode env (`GOOGLE_GENAI_USE_GCA=
true` + key scrub) to **every** spawn by default; `transport=api` is the only
deviation. TOC at upload thus stays on subscription auth, but *via the env
var*, not via the (removed) persisted setting.

**Deployment ordering:** ship the GCA-injecting code to a worker **before**
removing `selectedType` on that PC — removal first instantly breaks all gemini
calls there.

### Required keys + fail-fast (don't let the judge eat a 401)

An api-mode job touches up to three providers: the content provider + the
gemini extract pin + the claude judge — so `transport=api` requires **both**
`GEMINI_API_KEY` and `ANTHROPIC_API_KEY` on the worker, regardless of which
provider the job names. The failure mode if one is missing is silent: the
judge **never raises** (`phase_judge.py:111-113`; any error degrades to
`judge-unavailable` with `passed=True`, `:134-139`) — a gemini api job on a
worker without the Anthropic key would ship **unjudged content with no error**,
evaporating the quality gate. Two defenses, both required:
1. **Fail fast at job claim:** a worker only claims a `transport=api` job if
   every required key is present in its env (covers the extract failover path
   too — `_run_with_failover`, `pipeline.py:648`, can switch providers
   mid-job). *Concrete mechanism (don't hand-wave in the plan):* the worker
   computes `has_api_keys` (both keys present) once at startup and the claim
   query gates on it — `WHERE transport = 'cli' OR :has_api_keys`.
2. **Loud judge auth failures:** an auth/401 error inside the judge on an
   api-mode job is a job-level failure, not `judge-unavailable`.

## 4. Per-provider auth adapters

Implemented as a small per-call child-env transform where the env is already
built: `agent.py:257` (`child_env = {**os.environ, ...}`, passed at `:271`).
The transform receives `(provider, transport)`.

### gemini (verified by source read of installed gemini-cli 0.45.2)
Auth resolution: `effectiveAuthType = configuredAuthType || getAuthTypeFromEnv()`
(`validateNonInterActiveAuth`, bundle `gemini-YZFND2X2.js:15416`; call site `:16280`
passes persisted `security.auth.selectedType`). `getAuthTypeFromEnv()`
(`chunk-L4G73C3Y.js:308177`): `GOOGLE_GENAI_USE_GCA="true"` → OAuth (checked
first, wins over key); `GEMINI_API_KEY` → API-key mode.

- **One-time per worker PC:** remove `security.auth.selectedType` from
  `~/.gemini/settings.json`; ensure `security.auth.enforcedType` is unset
  (it *throws* on mismatch, `:15417-15421`).
- **`transport=cli`:** set `GOOGLE_GENAI_USE_GCA=true`; scrub `GEMINI_API_KEY`
  from the child env (hygiene — GCA wins anyway, but don't rely on ordering).
- **`transport=api`:** inject `GEMINI_API_KEY`; leave GCA unset.
- **Startup guard:** worker warns loudly at startup if `selectedType` is present —
  an operator running interactive `gemini` re-persists it and silently breaks the
  toggle (every "api" call would draw subscription).

### claude (verified empirically 2026-06-10)
Headless `claude -p --output-format json` with `ANTHROPIC_API_KEY` set uses the
key **directly — no approval gate, no config edits needed**. Proof: an invalid
key produced an immediate `api_error_status: 401` ("Invalid API key · Fix
external API key"), zero cost, zero subscription fallback. (The
`customApiKeyResponses` approval strings in the binary are interactive-mode
only.)

- **`transport=cli`:** scrub `ANTHROPIC_API_KEY` from the child env.
- **`transport=api`:** inject `ANTHROPIC_API_KEY`. That's the whole adapter.

### Key storage
Plain env vars on each worker (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) set via the
worker's compose/env file — never committed, consistent with the no-`.env`-read
rule. **Single key per provider**; rotation pool deferred (`fleet-api-2`).

## 5. Usage attribution + $ readout

- **`agent_usages.auth_mode`** (`cli | api`) recorded on **every** row from day
  one (alembic migration; backfill existing rows to `cli`). Without it the cost
  ledger can never retroactively distinguish $0-subscription rows from billed
  rows.
- **Static price map** (per provider+model: $/Mtok input, output, cache-read)
  with a **date + source-URL comment** — this project already mis-priced Opus
  once ($15/$75 deprecated vs real $5/$25); stale prices silently corrupt the
  readout the CLI-vs-API decision rides on.
- `/usage` (and/or `/api/v1/agent/stats`) shows **$ per provider per transport**
  computed from `agent_usages` × price map. Full cost ledger + budget
  kill-switch stay deferred (`fleet-api-3`).

## 6. Frontend

- Generate form + fleet batch launcher: a `CLI | API` segmented toggle, visible
  only for claude/gemini; picking API forces an explicit model selection.
- Job/batch views: a small `api` badge on jobs so billed runs are visually
  distinct; `/usage` gains the per-transport $ rollup.

## 7. Acceptance gates (before/with implementation)

1. **Live valid-key smokes** (the pre-code gate): with a real key per provider,
   one headless call each proves (a) the call **bills the right account**
   (visible in AI Studio / Anthropic console), (b) the JSON envelope still
   carries token stats in API mode, (c) for gemini, `selectedType` removed +
   key set actually selects API auth at runtime (source-verified; must be
   run-verified).
2. **Post-removal upload smoke:** after `selectedType` is removed (with the
   GCA-injecting code live), a plain **book upload → TOC extraction** (no job
   context) must still succeed — proves the unconditional cli baseline (§3).
3. **Mode-isolation test:** a `cli` job spawned while both keys are present in
   `os.environ` must scrub them (assert child env); an `api` job must carry
   exactly its provider's key.
4. **Missing-key fail-fast:** an api job is not claimed by a worker missing
   either key, and a forced judge auth failure on an api job fails the job
   loudly (not `judge-unavailable`).
5. **End-to-end:** one real lesson generated `transport=api` → every
   `agent_usages` row for the job (extract + content + judge) has
   `auth_mode=api`, and the $ readout shows a nonzero figure.

## 8. Deferred (tracked in WISHLIST)

- `fleet-api-1`-batch: **batch transport** (Gemini/Anthropic Batch REST, ~50%
  off, async submit/poll, per-wave batching across lessons) — slots in as a
  third enum value.
- `fleet-api-2`: credential rotation pool.
- `fleet-api-3`: cost ledger + kill-switch.
- `fleet-api-4`: never-pay-twice idempotency.
- `fleet-api-5` (new): **codex API mode** — `CODEX_API_KEY` env (not
  `OPENAI_API_KEY`, which codex ignores) flips auth, and default models
  diverge between auth modes (gpt-5.5 subscription vs gpt-5.4 API). *Verified
  by the design reviewer via live 401 test on codex 0.137.0 (not re-run in
  this session) — re-confirm against the installed codex version on pickup.*
  The explicit-model rule (§2) already protects against the divergence class.
