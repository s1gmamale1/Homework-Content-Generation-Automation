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
  Gemini API must be supported"). Reviewer-claimed mechanism for later pickup:
  `CODEX_API_KEY` (not `OPENAI_API_KEY`) — *unverified in this session; verify when
  picked up.* Tracked as `fleet-api-5` in WISHLIST.
- kimi / opencode: CLI-only, no API mode planned.

## 2. The toggle

- New job field **`transport`**: enum **`cli | api`**, default `cli` (today's
  behavior unchanged). Enum, not bool, so a future `batch` transport slots in
  without a migration.
- Batch launch (`POST /jobs/batch`) accepts `transport` and applies it to every
  job it creates.
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

**Outside scope:** book-level TOC extraction at upload/prepare time is not part
of a generation job and stays CLI/subscription, unchanged.

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
2. **Mode-isolation test:** a `cli` job spawned while both keys are present in
   `os.environ` must scrub them (assert child env); an `api` job must carry
   exactly its provider's key.
3. **End-to-end:** one real lesson generated `transport=api` → every
   `agent_usages` row for the job (extract + content + judge) has
   `auth_mode=api`, and the $ readout shows a nonzero figure.

## 8. Deferred (tracked in WISHLIST)

- `fleet-api-1`-batch: **batch transport** (Gemini/Anthropic Batch REST, ~50%
  off, async submit/poll, per-wave batching across lessons) — slots in as a
  third enum value.
- `fleet-api-2`: credential rotation pool.
- `fleet-api-3`: cost ledger + kill-switch.
- `fleet-api-4`: never-pay-twice idempotency.
- `fleet-api-5` (new): **codex API mode** — reviewer-claimed `CODEX_API_KEY`
  mechanism + observed default-model divergence between auth modes
  (gpt-5.5 vs gpt-5.4); *both claims unverified in this session — re-verify
  against the installed codex CLI when picked up.* The explicit-model rule
  (§2) already protects against the divergence class.
