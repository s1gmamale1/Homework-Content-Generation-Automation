# Per-Role Provider/Model Selection + API-Default Design

**Date:** 2026-06-17
**Status:** Design (awaiting user review → writing-plans)
**Branch:** `feat/per-role-provider-model`

## Goal

Let the user choose **provider + model + transport independently for each of the
three pipeline roles** — main generator, extract, judge — and make **API the
default transport for the generator** so an all-API workflow no longer requires
toggling CLI→API on every launch. Today only the generator's provider/model are
selectable; extract is pinned to `settings.extract_provider/model` and judge is
auto-derived by `model_tiers.judge_model_for`.

## Motivation

The user runs an all-API benchmarking workflow and wants genuine per-role mixing
(e.g. generate on claude, extract on cheap gemini-flash, judge on a third model),
chosen per run. The current pins force a single global extract model and an
auto-tiered judge, and the generator defaults to CLI — so every API launch needs
a manual toggle.

## Scope

In scope:
- New per-role `provider`/`model` overrides for **extract** and **judge** on both
  `homework_jobs` and `batches` (generator already has them).
- Generator transport **default → API** in the two launch UIs.
- Extract/judge transport selects **default → CLI** (explicit), with Auto + API
  as the other options.
- Pipeline + judge wiring to honor the overrides with today's behavior as the
  `NULL`/Auto fallback.
- Cross-job extract-reuse correctness fix (provider/model in the match).
- Per-role transport validation; judge independence guard.
- UI: extract + judge provider/model/transport controls in the fleet launcher
  (batch) and the section page (job).

Out of scope:
- Changing the backend `server_default` for any transport column (stays `cli`).
- Per-phase (content-phase) provider/model overrides — the generator pin across
  content phases is intentional and unchanged.
- Any new provider SDK; new models beyond the existing `MODEL_MANIFEST`.

## Design

### 1. Storage (extends the established Phase-4.1 flat-column pattern)

`homework_jobs` already carries flat `provider`, `model`, `transport`,
`extract_transport`, `judge_transport` (`app/models/homework_job.py:20-30`);
`batches` mirrors them and documents that batch-level provider/model are the
launch-default label only — per-job is authoritative (`app/models/batch.py:15-16`).

Add **four nullable columns to each of `homework_jobs` and `batches`** (8 total):

| Column | Type | NULL means |
|---|---|---|
| `extract_provider` | `String(32)` nullable | fall back to `settings.extract_provider` |
| `extract_model` | `String(128)` nullable | fall back to `settings.extract_model` |
| `judge_provider` | `String(32)` nullable | fall back to `model_tiers.judge_model_for(...)` |
| `judge_model` | `String(128)` nullable | fall back to `model_tiers.judge_model_for(...)` |

One mechanical Alembic migration (`add_nullable` only — safe, no backfill, no
contract phase). Rejected alternatives: a JSON `role_overrides` blob (breaks the
typed-column pattern, harder to validate/query) and a separate roles table
(over-engineered for a fixed set of two roles).

### 2. Resolution (resolved once in `pipeline.py`, mirroring transport)

`pipeline.py:82-90` already resolves the per-role *transports* once at job start.
Add the same for provider/model:

- **Extract:** `extract_provider = job.extract_provider or settings.extract_provider`;
  `extract_model = job.extract_model or settings.extract_model`. Replaces the hard
  references at `pipeline.py:695-696`.
- **Judge:** if `job.judge_provider` set → use `(job.judge_provider, job.judge_model)`;
  else `model_tiers.judge_model_for(gen_provider, gen_model)` (today's auto-tier).
  Threaded into `phase_judge.run_phase_judge` (replacing the internal
  `judge_model_for` call at `phase_judge.py:147`).
- **Generator:** unchanged (`job.provider`/`job.model`).

The provider/model **pin still holds across all content phases** — only the
extract and judge roles read their own override.

### 3. Defaults

- **Generator transport:** UI pre-selects **API**, CLI secondary. Both launch
  paths (`launcher.tsx`, `section.tsx`) flip `useState<Transport>("cli")` →
  `"api"` and reorder the toggle so API is first.
- **Extract/judge transport:** default **CLI** explicitly (not Auto). Rationale:
  extract is the cheap pinned role — running it on API adds cost for zero quality
  gain; judge on API requires every worker to hold the matching key or the job
  fails loudly / sits unclaimed (claim gate, `phase_judge.py:144-146`). Auto
  (inherit) and API remain selectable for deliberate opt-in.
- **Extract/judge provider/model:** default **Auto** (`NULL`) → today's smart
  defaults (gemini-flash extract; strongest/auto-tier judge).
- **Backend `server_default`:** stays `cli` for every transport column.
  Unspecified transport → cheapest/no-bill is the safe failure direction and
  protects non-UI callers. Safe because both UIs always send transport explicitly.

> **Correction to prior premise:** extract is defaulted to CLI for **cost**, not
> because API "can't handle" it. Verified: `summarize_lesson` injects whole-book
> text inline and calls `_spawn(..., attachments=[], ...)` (`agent.py:1594`) — no
> attachment — so extract runs fine on API. (The worklog-0064 unclaimable case was
> a provider/key mismatch, not a text-only limitation; the
> `api-job-claim-gate-and-concurrency` memory is corrected accordingly.)

### 4. Cross-job extract-reuse correctness (a bug this feature activates)

`find_latest_extract` (`app/repositories/phase_outputs.py:139-166`) keys reuse on
`(toc_entry_id, prompt_hash)`, and the extract `prompt_hash` is the constant
`"builtin:extract:v2"` (`pipeline.py:589`). That is safe **only** because extract
is gemini-pinned today. Once extract provider/model is per-job, a gemini-produced
extract would be served to a claude-requested job.

Fix: add `PhaseOutput.provider == extract_provider` and
`PhaseOutput.model_name == extract_model` to the `find_latest_extract` WHERE
clause. Both columns already exist on `phase_outputs`
(`app/models/phase_output.py:21,24`) — no schema change. Transport is **not** part
of the key (auth mode does not change extract output).

### 5. Validation (per role)

Call the existing `validate_transport(provider, model, transport)`
(`agent_models.py:69`) **once per role** (generator, extract, judge) at job/batch
creation. Hard rules it enforces, unchanged: `transport=api` requires an
api-supported provider (`claude`/`gemini`) **and** an explicit model; each
`(provider, model)` must be in `MODEL_MANIFEST`. For a role left on Auto (`NULL`
provider/model), validation runs against the resolved fallback.

### 6. Judge independence guard (split — the one place we are NOT fully permissive)

Today `judge_model_for` auto-swaps to a non-self peer (`_SELF_FALLBACK` defined
`model_tiers.py:55`, applied `:76`) whenever judge == generator, guaranteeing a
model never grades its own output. An explicit `job.judge` bypasses that function entirely, so
the guard must be reasserted:

- **Judge tier *below* the generator:** **soft warn** in the UI, allow launch —
  the user's quality/cost call.
- **Judge *exactly equal* to the generator (same provider+model):** **hard guard**
  — keep the `_SELF_FALLBACK` swap (or block at validation). Self-grading inflates
  pass rates and corrupts the benchmark this API effort exists to produce; it is
  not a dismissible warning.

Tier comparison uses `model_tiers.tier_of(provider, model)` (`model_tiers.py:58`,
1 = strongest): `tier_of(judge) > tier_of(generator)` ⇒ judge is weaker ⇒ soft
warn. Equality of `(provider, model)` ⇒ the hard self-grade guard.

### 7. UI

Both launch surfaces gain, for **Extract** and **Judge**, the same
provider + model + transport controls the generator already has, each provider/model
defaulting to "Auto" and each transport defaulting to "CLI":
- `web/src/components/fleet/launcher.tsx` (batch launch)
- `web/src/routes/section.tsx` (single-section launch)

The provider/model selectors reuse the generator's existing manifest-driven
component and the `/api/v1/agent/models` data. The soft self-/weak-judge warning
renders inline near the judge controls.

## Error handling

- Invalid `(provider, model, transport)` per role → 400 at creation (existing
  `validate_transport` shape).
- API role on a worker without the matching key → existing claim-gate / loud
  re-raise behavior; documented, not changed.
- Auto fallback is total: any `NULL` role field resolves to today's behavior, so a
  partially-specified launch is always valid.

## Testing

- **Unit (resolution):** job with explicit extract/judge provider/model → pipeline
  resolves to the override; `NULL` → resolves to settings/`judge_model_for`.
- **Unit (reuse key):** an extract row produced by `(gemini, flash)` is NOT
  returned by `find_latest_extract` for a `(claude, opus)` request; same
  provider/model IS returned.
- **Unit (validation):** per-role `transport=api` with `model=None` → rejected;
  with a non-api provider → rejected.
- **Unit (judge guard):** judge == generator → `_SELF_FALLBACK`/block engaged;
  judge tier < generator → allowed (warn surfaced via API field, not blocked).
- **Migration:** upgrade/downgrade roundtrip; existing rows read `NULL` and behave
  exactly as today.
- **FE:** `tsc` clean; both launchers post the new fields; defaults are
  generator=API / roles=CLI / provider-model=Auto.
- **Acceptance (real CLI smoke):** one generation with generator≠extract≠judge
  provider/model to prove the three roles route independently end-to-end.

## Operational note (not code)

An API default only *runs* where the fleet workers are API-provisioned (claim
gate). On a CLI-only worker, API jobs sit unclaimed. The role-transport defaults
(extract/judge = CLI) keep the default launch claimable by any worker; only the
generator role defaults to API.
