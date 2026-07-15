# Clodex provider — OpenAI-compatible API transport

**Corrected with the user (2026-07-15):** the supplied credential is not an OpenAI
credential. Live requests established that it authenticates against
`https://clodex.xyz/v1`. Implement Clodex as its own API-only provider; keep `codex` as
the existing CLI/subscription provider.

## Locked decisions

- Provider id: `clodex`; transport: `api` only.
- Authentication: `CLODEX_API_KEY`. Never silently fall back to `OPENAI_API_KEY`; doing
  so could disclose a real OpenAI credential to a third party.
- Endpoint: `CLODEX_BASE_URL`, defaulting to `https://clodex.xyz/v1`. The override is for
  tests/staging, not arbitrary per-job routing.
- Protocol: OpenAI-compatible Chat Completions through the `openai` Python SDK.
- Text model catalog, live-probed on 2026-07-15:
  `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, and
  `codex-auto-review`. `gpt-image-2` is excluded from the text-agent manifest.
- Responses may report a different served model than requested (a live Luna request
  reported Terra). Preserve requested and served model metadata in raw usage records.
- Clodex is rejected for extract/vision roles because current image fallbacks force the
  CLI lane, which does not exist for this provider.
- No database migration. Work only on `feat/openai-api-provider`; never push directly to
  `Nggaev-v2`.

## Implementation tasks

### 1. Transport and provider

- Replace the provisional `openai` transport/provider with `clodex`.
- Build the SDK client lazily from `CLODEX_API_KEY` and `CLODEX_BASE_URL`.
- Send `chat.completions.create` with the configured output cap.
- Normalize prompt, completion, cached, total, and reasoning token details; fail loudly
  on output truncation or missing credentials.
- Keep attachments unsupported in this text transport.
- Register an API-only provider stub so generic provider resolution and prompt assembly
  work while any attempted CLI execution fails loudly.

### 2. Model and validation contract

- Add the five live text models to the manifest and tier map.
- Add `clodex` to `API_PROVIDERS` and introduce `API_ONLY_PROVIDERS`.
- Require an explicit model for API execution and reject Clodex+CLI everywhere:
  launch routes, batch routes, saved defaults, and resolved role settings.
- Reject Clodex for the extract role with an actionable vision-fallback error.
- Preserve the existing shared tiers for Clodex/Codex aliases; add a tier for
  `codex-auto-review`.

### 3. Worker capability and scheduling

- Advertise `can_clodex_api = bool(CLODEX_API_KEY)` in worker capability blobs.
- Teach the API capability resolver and both claim-gate predicates about Clodex.
- Update exact-shape capability tests and startup warnings.
- A job or resolved judge/solver role using Clodex must not be claimed by an unkeyed
  worker.

### 4. Usage and pricing

- Treat Clodex prompt tokens as inclusive of cached tokens, matching OpenAI-compatible
  usage semantics.
- Add source-commented rates from Clodex's public pricing payload and tests for the
  normalized cost formula. For floor-priced models, conservatively use the larger of
  the fixed and floor rates; an unknown served Clodex alias must also use a nonzero
  fallback so budget guards fail safe rather than silently bypassing paid usage.
- Add Clodex rolling-limit settings and auth-error signals.
- Record the requested and served model when the endpoint returns both, and attribute
  the ledger row to the served model so downstream pricing and budget guards use the
  tier Clodex reports actually serving.

### 5. Frontend API-only behavior

- Return `api_only` metadata from `/agent/models` and add it to frontend types.
- Pin Clodex to API in launcher, section, and role controls; never silently downgrade it
  to CLI.
- If the fleet has no keyed Clodex worker, disable launch with a useful hint.
- Extract the pin/downgrade rule into a pure tested helper.
- Add Clodex to usage labels/order/accent styling.

### 6. Documentation and verification

- Update `.env.example`, README, architecture/code-map documentation, and provider-count
  statements to document `CLODEX_API_KEY` and the API-only boundary.
- Add a credential-safe model probe script.
- Run focused backend tests, frontend type/build tests, then the complete suite.
- Run one bounded live request using the existing secret injected as
  `CLODEX_API_KEY`; report only status/model/token metadata, never the credential.
- Review the diff and branch status. Do not push unless the user explicitly asks, and
  never push directly to `Nggaev-v2`.

## Acceptance criteria

1. `clodex` can execute a small explicit-model API request through the normal agent path.
2. No Clodex route reads or forwards `OPENAI_API_KEY`.
3. CLI and extract-role combinations fail validation before scheduling.
4. Unkeyed workers cannot claim Clodex work; keyed workers can.
5. The UI cannot enter an API-only/CLI effect loop or submit a Clodex CLI job.
6. Usage tokens and requested/served model metadata are retained; the ledger uses the
   served model, and every selectable or unknown served Clodex model prices nonzero.
7. Tests and the bounded live smoke pass from the feature worktree.
