# New `openai` provider — OpenAI-compatible API transport (fleet-api-5 successor)

**Locked with user (2026-07-15):** NEW api-only provider `openai` (codex stays CLI-only,
subscription); first target **OpenAI official** (`OPENAI_API_KEY`, default base URL), with
`OPENAI_BASE_URL` env override so any OpenAI-compatible backend (DeepSeek/OpenRouter/vLLM)
is a config change later, not a code change.

## Approach & key decisions

- **Two honest lanes, no catalog conflation:** `codex` = CLI/subscription with codex-backend
  model names (`gpt-5.6-sol`…); `openai` = api/pay-per-token with the **API catalog** (ids
  pinned by a live `models.list()` probe at impl time — the two catalogs differ; do NOT copy
  codex names). Rejected: extending codex to api (one provider, two catalogs → transport-aware
  manifest mess); rejected: per-job base_url (config surface; env is enough for MVP).
- **api-only is a new validation class:** `validate_transport` today rejects api for
  non-API_PROVIDERS; it must ALSO reject **cli for openai** (no CLI exists). New
  `API_ONLY_PROVIDERS = frozenset({"openai"})` in `agent_models.py`, enforced in
  `validate_transport` (both launch routes + settings share it). FE pins transport=api when
  provider=openai (inverse of today's cli-pin for non-api providers, `launcher.tsx:839`).
- **Dispatch generalization:** `agent.py:505` `provider.name in ("gemini", "claude")` →
  membership in `agent_models.API_PROVIDERS` (single source of truth). `openai` still needs a
  registry Provider stub (name resolution happens before `_spawn`); its `build_argv` raises
  `RuntimeError("openai is api-only")` — unreachable behind validation, loud if reached.
- **Key scrub (defense-in-depth, rationale corrected by fresh-Fable review):** scrub
  `OPENAI_API_KEY` in `_auth_env`'s **cli baseline** (same class as the GEMINI/ANTHROPIC
  scrubs). NOTE: the repo's own records (WISHLIST.md:149 + phase4 spec:176) say codex-CLI
  auth flips via `CODEX_API_KEY`, NOT `OPENAI_API_KEY` — so this is defensive hygiene, not
  a verified mis-billing fix; do not claim otherwise. (`CODEX_API_KEY` scrubbing is the
  pre-existing WISHLIST item, out of scope here.) The SDK path reads `os.environ` in-process
  (never a child env), so no api-branch injection — the loud missing-key raise lives in the
  client builder, mirroring `_claude_client`. `_auth_env("openai", "api")` raising
  `AuthEnvError` today is CORRECT and stays: the api dispatch short-circuits at `agent.py:505`
  before the `child_env` build (`:530`), so it's unreachable for openai — don't "fix" it.
- **Cached-token semantics:** OpenAI's `prompt_tokens` INCLUDES `prompt_tokens_details.cached_tokens`
  (same family as gemini, disjoint from claude) → add `"openai"` to
  `pricing._PROMPT_INCLUDES_CACHED`. Reasoning tokens bill as output (already inside
  `completion_tokens`). Prices verified against platform.openai.com at impl time — no
  unverified numbers into `PRICE_MAP`.
- **Roles ride along free:** judge/solver/extract on openai validate via the same generic
  `is_valid` + `validate_transport(role)`; claim gate gains an openai branch in BOTH
  predicates (`jobs.py:356` content, `:367` resolved-role). Extract default stays pinned
  gemini (no settings change). Self-grade fallback peers unchanged (openai generator → claude-opus judge).
- **Env plumbing rule preserved:** `OPENAI_API_KEY`/`OPENAI_BASE_URL` read from `os.environ`
  (reached via `load_dotenv`), NEVER pydantic settings (CLAUDE.md invariant).
- Branch `feat/openai-api-provider` off `origin/Nggaev-v2`, worktree `../HCGA-openai-api`.
  Migration: **none**. Worklog: **0140** (re-verify INDEX tail at finish — numbers go stale).
  Suite baseline: 1544/214/0. Collision: BE-03 lane owns `batch.py`/`batches.py` — this plan
  touches neither; `jobs.py` claim-gate region is disjoint from BE-03. New dep: `openai` SDK (uv).

## Tasks

### Task 0 — live catalog probe (read-only, $0)

`scripts/probe_openai_models.py`: `client.models.list()` with the operator's `OPENAI_API_KEY`
(+ one `gpt-*-mini` 5-token completion, cost printed). Output pins the manifest ids and
confirms the key works. **Blocked until the operator puts `OPENAI_API_KEY` in the head `.env`**
(never committed). Paste output into the PR. No commit (script is committed in Task 6 with docs).

### Task 1 — `api_transport._openai` (RED → GREEN)

**Tests first** (`tests/services/test_api_transport.py`, mock the `openai` SDK — no network):
- `generate(provider="openai", ...)` returns `(rc=0, text, usage, err="")`; usage keys
  normalized `prompt_tokens/output_tokens/cached_tokens/total_tokens` with cached mapped from
  `prompt_tokens_details.cached_tokens` (absent → 0).
- attachments → `NotImplementedError` — contract PIN, not RED (the generic guard at
  `api_transport.py:37-40` already fires before the provider branch).
- no `OPENAI_API_KEY` → `RuntimeError` naming the var (loud, never "").
- `OPENAI_BASE_URL` set → client constructed with that base_url; unset → default.
- **output-cap parity with `_claude` (review fix #5):** request carries
  `max_completion_tokens = settings.api_max_output_tokens`; `finish_reason == "length"` →
  loud error, never silent truncation (mirror `test_claude_truncation_is_loud`/`test_claude_cap_passed`,
  `api_transport.py:164-177`).

**Code** (`app/services/api_transport.py`): `_openai_client()` (lazy import, key-raise,
base_url passthrough), `_openai_usage(u)`, `async def _openai(model, prompt)` via
`client.chat.completions.create` with the output cap + length-stop raise; dispatch branch
in `generate`. `uv add openai`.
Commit: `feat(api): openai SDK branch in api_transport (openai-api task 1)`

### Task 2 — agent_models + tiers: manifest, API_PROVIDERS, api-only rule (RED → GREEN)

**Tests first** (`tests/services/test_agent_models.py` + `test_model_tiers.py`):
- `api_supported("openai") is True`; `validate_transport("openai", "<model>", "api") is None`.
- **RED:** `validate_transport("openai", None, "cli")` returns an error naming api-only.
- `validate_transport("openai", None, "api")` still errors (explicit-model rule holds).
- `default_model("openai")` = first manifest entry; completeness test forces a tier per model.
- `_resolve_model("openai", None) is None` (no-leak invariant extends — add to the existing test).

**Code**: `MODEL_MANIFEST["openai"] = [<Task-0 ids, flagship first>]` — **verify no id collides
with a codex manifest entry** (`_MODEL_TIER` is keyed by bare model name; a collision silently
shares a tier — review fix #7); `API_PROVIDERS` += openai; new `API_ONLY_PROVIDERS` +
`validate_transport` cli-rejection; `model_tiers._MODEL_TIER` entries (tiering by OpenAI's
published capability ladder); `agent._PROVIDER_DEFAULT_MODEL["openai"] = None`.

**Also in this task — close the two validation holes the review found (fixes #3, #6):**
- **settings.py inverse guard (RED test in `tests/api/test_settings_launch_defaults.py`):**
  today `settings.py:103/:114` only reject api-with-non-api-provider; an operator could save
  `content_provider=openai, content_transport=cli` (or a role resolving to cli) and BRICK every
  subsequent Auto launch (400 at `jobs.py:283`/`batch.py:272`). Reject api-only providers with a
  cli/inherit-resolving transport at defaults save — same error contract as the launch routes.
- **extract-role rejection for api-only providers (RED test):** the vision fallbacks force
  `transport="cli"` (`pipeline.py:1103-1107`, `agent.py:1562`, `:1786`) — structurally impossible
  for openai (`binary_names=()` → `FileNotFoundError("install one of []")`). Reject openai as
  `extract_provider` at launch + settings with an error naming the vision-fallback reason.
  Extract default stays gemini; content/judge/solver roles remain fully open.
Commit: `feat(models): openai provider — manifest, api-only transport rule, tiers, guards (task 2)`

### Task 3 — provider stub + dispatch generalization + auth scrub (RED → GREEN)

**Tests first** (`tests/services/test_agent.py` + `test_providers.py`):
- `_auth_env("codex", "cli", {"OPENAI_API_KEY": "sk-x"})` — **RED:** key must be scrubbed
  (verified: today it leaks through — `agent.py:287-288` pops only GEMINI/ANTHROPIC).
- registry resolves `"openai"`; its `build_argv` raises RuntimeError (tested DIRECTLY on the
  stub — on a real cli path the earlier `_resolve_binary` `FileNotFoundError` at `agent.py:256-259`
  fires first since `binary_names=()`; don't claim the RuntimeError is the runtime backstop).
- `_spawn` api dispatch uses `API_PROVIDERS` membership (test via monkeypatched
  `api_transport.generate` seeing provider="openai"; codex/kimi still fall through to CLI).

**Code**: `app/services/providers/openai_api.py` (`class OpenAIApi(Provider)`, name `"openai"`,
`binary_names=()`; `build_argv`/`parse_envelope` raise RuntimeError — CLI-only methods, unreachable
behind validation; `format_attachments`/`prompt_suffix` return `""` like claude/gemini, because
`run_phase` calls them during prompt composition BEFORE the transport dispatch) + registry entry;
`agent.py:505` membership swap; `_auth_env` baseline `env.pop("OPENAI_API_KEY", None)`.
Add a test that prompt composition for an api openai call raises nothing and appends no suffix.
Commit: `feat(agent): openai provider stub, API_PROVIDERS dispatch, cli key scrub (task 3)`

### Task 4 — worker capabilities + claim gate (RED → GREEN, scratch DB)

**Tests first** (pattern: `tests/integration/test_claim_gate_self_grade.py` — the plan's earlier
`tests/repositories/test_claim_gate.py` reference was stale; scratch `edu_scratch_oai`):
- `_api_capable`: openai = `bool(OPENAI_API_KEY)`; `_capability_blob["api"]["openai"]` present.
- **RED (bites-proof):** pending `provider=openai, transport=api` job + worker WITHOUT
  `can_openai_api` → `claim_next_job` returns None; WITH → claims.
- resolved-role: api-judge=openai job gated the same way (`_provider_api_ok` at
  `repositories/jobs.py:367` is shared by judge/extract/solver — one branch covers all three).
- **Update the three shape-pinning tests IN THIS TASK (review fix #1)** — they assert the exact
  capability dict/blob shapes and go red the moment `can_openai_api` exists:
  `tests/services/test_auth_env.py:150`, `:296-310` ("adding any extra key breaks this
  assertion" is their stated purpose), `tests/services/test_worker_capabilities.py:56-66`.
  Without these updates this task's own green-suite commit bar is unachievable.

**Code**: `worker._api_capable` + `_compute_capabilities` (`can_openai_api`) + `_capability_blob`;
`repositories/jobs.py:356` + `:367` openai branches.
Commit: `feat(worker): openai api capability + claim-gate branches (task 4)`

### Task 5 — pricing + judge auth signals (RED → GREEN)

**Tests first** (`tests/services/test_pricing.py`, `test_phase_judge.py`):
- openai row: `cost = (prompt−cached)·in + cached·cache_read + output·out` (prompt-includes-cached
  semantics — RED with a naive disjoint formula).
- unpriced openai model → $0 + one log line (existing behavior).
- auth-signal coverage: contract PIN, likely not RED — `_AUTH_SIGNALS` already carries `"401"`
  (`phase_judge.py:143`) and OpenAI SDK errors stringify as `"Error code: 401 - …"`. Prove with
  the RAW SDK message shapes; add `invalid_api_key` marker for the unwrapped form.
- `_openai_client` raises an `agent.AuthEnvError`-compatible error on missing key so
  `phase_judge._is_auth_error` isinstance/signal classification works for judge AND solver
  (solver imports the same helper, `solver.py:21`).

**Code**: `PRICE_MAP` openai entries (rates verified from platform.openai.com/docs/pricing at
impl, source-commented like the gemini block); `_PROMPT_INCLUDES_CACHED` += `"openai"`;
`phase_judge._AUTH_SIGNALS` += openai markers; `config.py` gains `agent_limit_openai_1h/24h/7d`
fields (else `/usage` getattr-defaults openai to unmetered, `api/v1/jobs.py:649-652`).
Commit: `feat(pricing): openai price map + cached-inclusive semantics; judge auth signals (task 5)`

### Task 6 — FE: api-only provider UX

**Code** (`web/src/`): models endpoint already serves the manifest generically — verify
`api_supported` includes openai (it derives from `API_PROVIDERS`); add `api_only` map to the
response. Launcher + section: when `api_only[provider]`, pin transport=api and hide the cli
option (inverse of the existing `!apiSupported → pin cli` effect at `launcher.tsx:839`);
`RoleAgentControls` same.
**Critical (review fix #2): the fleet-downgrade effects fight the pin.** `launcher.tsx:849-851`,
`section.tsx:159-161`, `RoleAgentControls.tsx:173-177` all `setTransport("cli")`/disable when
`!fleet.api[provider]` — and `fleet.api["openai"]` stays falsy until every worker is updated AND
keyed (old worker blobs lack the key; `repositories/workers.py:147-152` unions present keys only).
Unguarded, that's an effect ping-pong (cli↔api) or a cli-openai POST that 400s. The downgrade
branch must SKIP api_only providers and disable the launch button with a "fleet has no openai
key" hint instead. Extract the pin/downgrade rule into a pure `lib/` module with an npx-tsx test
(the `launch-model.ts` pattern) — this interaction is too subtle for tsc-only coverage.
Cosmetic: add openai to `usage.tsx:24` PROVIDER_ORDER + accent map.
`scripts/probe_openai_models.py` committed here too.
Run: `npx tsx` pure tests + `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`.
Commit: `feat(fe): api-only provider pin (openai) (task 6)`

### Task 7 — docs + acceptance + finish

- **Acceptance gate (real generation, bounded, cost-logged):** ONE single-lesson job,
  `provider=openai, transport=api`, explicit cheap model (judge+extract on their defaults) —
  in-process smoke per the standing recipe, NOT a mass launch. Paste phase outputs summary +
  `agent_usages` rows (auth_mode=api, normalized tokens, `cost_usd`) into the PR. Expected
  cost: cents. Blocked on the operator's key (Task 0).
- Docs de-stale: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, CLAUDE.md transport
  bullets ("api is claude+gemini only" → "+openai (api-only)"), `.env.example` if present.
- Worklog **0140** + INDEX row (re-check tail); full suite; `git fetch` + rebase check; push;
  PR → **GK2 gates + merges**; `git mv` plan → `shipped/`.

## Flagged for the gate

1. `openai` is the first **api-only** provider — a new validation class; cli stays structurally
   impossible for it (validated at launch AND settings-save AND extract-role, loud stub behind).
2. `OPENAI_API_KEY` scrub added to the cli baseline — changes every cli spawn's env. Defensive
   hygiene only: the repo's records say codex ignores this var (`CODEX_API_KEY` is the codex
   flip, pre-existing WISHLIST item, out of scope).
3. Manifest ids + prices enter the repo only after the live probe + official price page — no
   trained-knowledge numbers; verify no codex↔openai bare-model-id collision (shared tier table).
4. SA-key-style distribution does NOT cover OpenAI keys (that system is GCP-specific) — fleet
   workers needing openai get the key via their `.env`, like `ANTHROPIC_API_KEY` today.
5. openai is REJECTED as extract provider (vision fallbacks force cli — structurally impossible;
   the first api provider with no cli lane). Content/judge/solver fully supported.
6. Until the fleet is updated + keyed, `fleet.api["openai"]` is false → FE disables openai
   launches with a hint (never silently downgrades to cli).

## Review record

Fresh-Fable adversarial review 2026-07-15 (two-pass, ~48 tool reads): verdict
**APPROVE-WITH-FIXES**; all 7 fixes folded into the tasks above (settings-save guard,
shape-pinning test updates, FE downgrade/pin conflict, output-cap parity, corrected codex
rationale, extract-role rejection, anchor/label corrections).
