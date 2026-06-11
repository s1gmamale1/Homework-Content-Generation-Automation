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

Set **both** keys on every worker, via the worker's compose/env file (never
committed):

```
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
```

**Both are required for any `transport=api` job**, regardless of which provider the
job names — an api job touches the content provider + the gemini extract pin + the
claude judge. The worker computes `has_api_keys = bool(both present)` once at
startup; `claim_next_job` gates `WHERE transport='cli' OR :has_api_keys`, so a
half-configured worker (exactly one key) **will not claim api jobs** and logs a
warning. cli jobs are unaffected.

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
