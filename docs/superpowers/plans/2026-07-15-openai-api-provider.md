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
- **Mis-billing guard (critical):** the codex CLI honors `OPENAI_API_KEY` — a lingering env
  key would silently flip codex-CLI spawns from subscription OAuth to API billing. Scrub
  `OPENAI_API_KEY` in `_auth_env`'s **cli baseline** (same class as the existing
  GEMINI/ANTHROPIC scrubs). The SDK path reads `os.environ` in-process (never a child env),
  so no api-branch injection is needed — the loud missing-key raise lives in the client
  builder, mirroring `_claude_client`.
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
- attachments → `NotImplementedError` (text-only, same contract as claude).
- no `OPENAI_API_KEY` → `RuntimeError` naming the var (loud, never "").
- `OPENAI_BASE_URL` set → client constructed with that base_url; unset → default.

**Code** (`app/services/api_transport.py`): `_openai_client()` (lazy import, key-raise,
base_url passthrough), `_openai_usage(u)`, `async def _openai(model, prompt)` via
`client.chat.completions.create`; dispatch branch in `generate`. `uv add openai`.
Commit: `feat(api): openai SDK branch in api_transport (openai-api task 1)`

### Task 2 — agent_models + tiers: manifest, API_PROVIDERS, api-only rule (RED → GREEN)

**Tests first** (`tests/services/test_agent_models.py` + `test_model_tiers.py`):
- `api_supported("openai") is True`; `validate_transport("openai", "<model>", "api") is None`.
- **RED:** `validate_transport("openai", None, "cli")` returns an error naming api-only.
- `validate_transport("openai", None, "api")` still errors (explicit-model rule holds).
- `default_model("openai")` = first manifest entry; completeness test forces a tier per model.
- `_resolve_model("openai", None) is None` (no-leak invariant extends — add to the existing test).

**Code**: `MODEL_MANIFEST["openai"] = [<Task-0 ids, flagship first>]`; `API_PROVIDERS` += openai;
new `API_ONLY_PROVIDERS` + `validate_transport` cli-rejection; `model_tiers._MODEL_TIER` entries
(tiering by OpenAI's published capability ladder); `agent._PROVIDER_DEFAULT_MODEL["openai"] = None`.
Commit: `feat(models): openai provider — manifest, api-only transport rule, tiers (task 2)`

### Task 3 — provider stub + dispatch generalization + auth scrub (RED → GREEN)

**Tests first** (`tests/services/test_agent.py` + `test_providers.py`):
- `_auth_env("codex", "cli", {"OPENAI_API_KEY": "sk-x"})` — **RED:** key must be scrubbed
  (today it leaks through). Also scrubbed for every other cli spawn.
- registry resolves `"openai"`; its `build_argv` raises RuntimeError.
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

**Tests first** (`tests/repositories/test_claim_gate.py` pattern, scratch `edu_scratch_oai`):
- `_api_capable`: openai = `bool(OPENAI_API_KEY)`; `_capability_blob["api"]["openai"]` present.
- **RED (bites-proof):** pending `provider=openai, transport=api` job + worker WITHOUT
  `can_openai_api` → `claim_next_job` returns None; WITH → claims.
- resolved-role: api-judge=openai job gated the same way (predicate `:367` branch).

**Code**: `worker._api_capable` + `_compute_capabilities` (`can_openai_api`) + `_capability_blob`;
`jobs.py:356` + `:367` openai branches.
Commit: `feat(worker): openai api capability + claim-gate branches (task 4)`

### Task 5 — pricing + judge auth signals (RED → GREEN)

**Tests first** (`tests/services/test_pricing.py`, `test_phase_judge.py`):
- openai row: `cost = (prompt−cached)·in + cached·cache_read + output·out` (prompt-includes-cached
  semantics — RED with a naive disjoint formula).
- unpriced openai model → $0 + one log line (existing behavior).
- `_AUTH_SIGNALS` matches OpenAI's 401 shapes (`invalid_api_key`, `Incorrect API key provided`);
  deliberately no bare `"401"` (same rationale as the existing no-bare-`"403"`).

**Code**: `PRICE_MAP` openai entries (rates verified from platform.openai.com/docs/pricing at
impl, source-commented like the gemini block); `_PROMPT_INCLUDES_CACHED` += `"openai"`;
`phase_judge._AUTH_SIGNALS` += openai markers.
Commit: `feat(pricing): openai price map + cached-inclusive semantics; judge auth signals (task 5)`

### Task 6 — FE: api-only provider UX

**Code** (`web/src/`): models endpoint already serves the manifest generically — verify
`api_supported` includes openai (it derives from `API_PROVIDERS`); add `api_only` map to the
response if absent. Launcher + section: when `api_only[provider]`, pin transport=api and hide
the cli option (inverse of the existing `!apiSupported → pin cli` effect at `launcher.tsx:839`);
`RoleAgentControls` same. Pure test for the pin rule if extracted (follow `launch-model.ts`
pattern); else covered by tsc + build. `scripts/probe_openai_models.py` committed here too.
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
   impossible for it (validated at launch + settings, loud stub behind that).
2. `OPENAI_API_KEY` scrub added to the cli baseline — changes every cli spawn's env (defensive;
   RED-tested; the codex-CLI subscription→API mis-billing guard).
3. Manifest ids + prices enter the repo only after the live probe + official price page — no
   trained-knowledge numbers.
4. SA-key-style distribution does NOT cover OpenAI keys (that system is GCP-specific) — fleet
   workers needing openai get the key via their `.env`, like `ANTHROPIC_API_KEY` today.
