# Global launch defaults — UI-managed model selection (retire `.env`)

**Date:** 2026-06-27
**Status:** design approved (brainstorm), pending implementation plan
**Author:** gatekeeper (brainstorm with user)

## Problem

Today, the **model selection for the judge and extract roles is changed by editing `.env`** (`JUDGE_PROVIDER`/`JUDGE_MODEL`, `EXTRACT_PROVIDER`/`EXTRACT_MODEL`, `EXTRACT_TOC_TRANSPORT`). The launcher already has per-role pickers (`RoleAgentControls` for Extract + Judge), but they default to **"Auto"**, and *Auto silently resolves to the `.env` value* (`settings.judge_*` / `settings.extract_*`). So the operator never sees what will actually run, and the real control surface is `.env` — which is wrong: it requires a file edit + server restart to change a model, and it's invisible in the UI.

The cost trigger that surfaced this: judge was running on `gemini-3.1-pro-preview` (output billed $12/Mtok), costing ≈ the entire content generation per homework and doubling per-homework cost (~$1 → ~$2). The operator had to edit `.env` to change it. That should be a UI action.

## Approach & key decisions

**Chosen approach — a server-side, DB-backed singleton of global launch defaults, edited from a `/settings` route, that fully replaces `.env` as the model-selection surface for judge/extract/TOC.** Launch-time per-role pickers still override per book. **No model value lives in any `.env`** — every model choice (content at launch, judge/extract/TOC defaults at `/settings`) is made on the website; the DB row is the only home for the defaults.

Key decisions (all locked with the user):
1. **UI-managed global default, persisted server-side (DB), not `.env`.** Rejected "make Auto merely transparent" (keeps `.env` as the source) and "force an explicit pick every launch" (annoying for a multi-hundred-book campaign). The user wants a default set *once in the app*.
2. **Scope = Judge (provider/model/transport), Extract (provider/model/transport), TOC transport (upload-time, global-only).** **Content generator stays a mandatory explicit launch pick** — deliberately NOT a global default, so "what am I generating with" is always a conscious choice.
3. **Future-launches-only (resolve-at-launch).** At launch, each job captures the **concrete** provider/model/transport it will use (explicit pick → else global default). Queued/pending jobs keep what they were launched with; changing a default never rewrites in-flight work. This matches the repo's per-job-selection principle and keeps `agent_usages` attribution honest. Rejected "store null + resolve at runtime" (jobs not self-describing; a default change silently re-prices queued work).
4. **`.env` model vars deleted entirely** (not "seeded then ignored" — *removed*). The reason model values lived in `.env` was that resolution happened on the worker at runtime; once resolution moves to launch time, no machine needs a model value in `.env`. The migration seeds the `launch_defaults` row with **explicit default values** (see §1) — the default's only home is the DB row, set in the UI. So `JUDGE_PROVIDER/MODEL`, `EXTRACT_PROVIDER/MODEL`, `EXTRACT_TOC_TRANSPORT` are **removed from `config.py` settings and every `.env`** (head + workers). Workers carry only **credentials + infra**, never a model value.

Load-bearing facts verified against code:
- `settings.judge_provider`/`judge_model` (`config.py:145-146`), `settings.extract_provider`/`extract_model` (`config.py:194-195`), `settings.extract_toc_transport` (`config.py:213`) all exist and are the current `.env`-backed defaults.
- Launch endpoints already accept + persist per-role fields: `app/api/v1/batch.py:39-44,197-203`, `app/api/v1/jobs.py:143-179,243-250`.
- Pipeline already reads per-job overrides: `pipeline.py:135-142` (`getattr(job, "judge_provider", None)` etc.).
- Launcher already sends them: `web/src/components/fleet/launcher.tsx:584-587,744-747`, rendered via `RoleAgentControls` (`launcher.tsx:916-938`), persisted in `web/src/lib/launcher-config.ts`.
- **Worker capability touchpoint:** `worker.py:108-115,235` computes `settings_judge_provider`/`settings_extract_provider` capability hints from `settings.*`; the claim gate uses them for the null-job fallback (`jobs.py:328-331`). Since jobs are always stamped now, these hints get **dropped** — worker capability becomes credential-only (see §6).

## 1. Storage — singleton table `launch_defaults`

One row (enforced `id = 1`, a `CHECK (id = 1)` or a unique constant). Columns are nullable for partial `PUT` updates; the migration inserts the row with explicit values (§1), so reads always see a populated default:

| column | type | notes |
|---|---|---|
| `id` | int PK | always 1 |
| `judge_provider` | text NULL | |
| `judge_model` | text NULL | |
| `judge_transport` | text NULL | `cli`/`api`/**`inherit`** — `inherit` = follow the content job's transport (today's default) |
| `extract_provider` | text NULL | |
| `extract_model` | text NULL | |
| `extract_transport` | text NULL | `cli`/`api`/`inherit` |
| `toc_transport` | text NULL | `cli`/`api` only — no job to inherit from at upload time |
| `updated_at` | timestamptz | |

**Migration:** new Alembic revision (≤32-char id, per `alembic-jsonb-upsert-gotchas`). Creates table and inserts the singleton row **with explicit seed values** (no `.env`/`settings` read anywhere — the values are written literally in the migration):

| field | seed value | note |
|---|---|---|
| `judge_provider` / `judge_model` | `gemini` / `gemini-2.5-flash` | the cheaper judge; bump in the UI if desired (the cost decision under discussion) |
| `extract_provider` / `extract_model` | `gemini` / `gemini-2.5-flash` | current extract strategy |
| `judge_transport` / `extract_transport` | `inherit` | matches today's RoleAgentControls "Auto" default → follow the content job's transport |
| `toc_transport` | `cli` | conservative; flip to `api` in the UI (current campaign uses `api` over Vertex) |

This is **not** behavior-neutral with a `.env` that currently pins judge to `gemini-3.1-pro-preview` — the seed deliberately chooses the cheaper judge, and the operator sets the final value in `/settings` on first run. (Seed values are the implementer's to confirm against the locked model strategy at build time.)

## 2. Access layer — `launch_defaults_repo`

- `get(session) -> LaunchDefaults` — read the singleton (cheap; called per launch/upload, not a hot path). A short in-process cache is optional, not required.
- `update(session, fields: dict) -> LaunchDefaults` — partial update of the singleton, `updated_at = now()`.

No startup-seed helper — the singleton row is created **with values by the migration** (§1), so `get()` always returns a populated row.

## 3. Resolution order (per launch)

In the launch endpoints (`batch.py`, `jobs.py`), before persisting the job/batch row, resolve each of judge/extract:

```
resolved = explicit_pick_if_present  else  global_default(role)
```

Persist **concrete** `judge_provider/model` + `extract_provider/model` onto the job/batch row (never null going forward), validated against `MODEL_MANIFEST` (`agent_models.is_valid`). **Transport** is stored as the resolved default value — which *may be `inherit`* — and is resolved against the job transport by the existing `resolve_role_transport` (`pipeline.py:127`) at run, exactly as today. This is still future-launches-only safe because the job's own transport is fixed at launch. (So: provider/model are made concrete at launch; transport keeps its existing inherit-resolution path — minimal churn, lower risk.)

**Remove** `settings.judge_provider/model` and `settings.extract_provider/model` (and `settings.extract_toc_transport`) from `config.py` entirely — jobs always carry explicit provider/model now, so nothing reads them. Touchpoints to repoint or delete: `model_tiers.judge_model_for` (`model_tiers.py:88` reads `settings.judge_*`) — its only caller is the `judge_provider is None` branch of `resolve_judge`, which is dead once jobs are always stamped; keep a defensive path that reads the **DB global default** (not `.env`) if a null ever slips through. The self-grade `_self_fallback` guard is unaffected and still applies.

**Content:** unchanged — remains a mandatory explicit pick; not read from `launch_defaults`.

## 4. TOC transport (upload-time)

The upload/TOC path currently reads `settings.extract_toc_transport`. Repoint it to `launch_defaults.toc_transport`. No new upload-time picker — it's a global setting, edited in `/settings`.

## 5. API + UI

- `GET /api/v1/settings/launch-defaults` → the row.
- `PUT /api/v1/settings/launch-defaults` → partial update; **validates** each (provider, model) against `MODEL_MANIFEST` and transport via `validate_transport`; rejects off-manifest pairs with 422.
- New **`/settings` route** in the SPA: a small form, one row per role (Judge / Extract / TOC), reusing the manifest dropdowns. Auth like the rest of the API.
- **Transparency win:** the launcher reads `launch-defaults` and renders the "Auto" placeholder as the resolved value, e.g. `Auto → gemini-2.5-flash`, so the operator always sees what Auto means. (`RoleAgentControls` gets the resolved default passed in.)

## 6. Worker capability gate (touchpoint, don't miss)

`worker.py:108-115,235` computes `settings_judge_provider`/`settings_extract_provider` hints from `settings.*` for the null-job claim fallback (`jobs.py:328-331`). Since jobs now **always** carry explicit resolved providers, that null fallback is dead — the claim gate evaluates each job's own stamped provider×transport against the worker's **credential-based** capabilities (`{cli,api}` blob, already published). So the worker no longer needs any model/provider value at all: **drop the `settings_judge_provider`/`settings_extract_provider` hints** (and the `settings.judge_*` reads at `worker.py:115,235`). Worker capability becomes purely "what creds/CLIs do I have" — which is exactly what the `.env` should still carry (credentials), and nothing more. Confirm the claim gate (`jobs.py:328-331`) no longer references the removed hints.

## 7. Testing / acceptance

- **Migration/repo:** the migration's `upgrade()` creates the singleton with the §1 seed values (assert the row exists + matches after `alembic upgrade head` on a scratch DB); `get/update` round-trip; singleton invariant (can't insert id≠1). RED-prove the singleton constraint so deleting it fails the test (per the vacuous-test lesson, `sdd-acceptance-gate-learnings`).
- **Resolution:** launch with explicit pick → job stores the pick; launch with Auto → job stores the **global default's concrete value**; changing the default afterward does NOT mutate the already-launched job (future-launches-only). RED-prove.
- **API:** `PUT` rejects an off-manifest model (422); `GET` returns the seeded row.
- **TOC:** upload reads `launch_defaults.toc_transport`, not `settings.extract_toc_transport`.
- **Real acceptance smoke (CLAUDE.md gate, generation-affecting):** set global judge default to a known model via `PUT`, launch a job with Judge=Auto, run it, and assert the judge call in `agent_usages` recorded **that** `model_name` — proving the UI default (not `.env`) drove the run. Use a real CLI/API call per the acceptance-gate rule.
- Full suite green; FE `tsc --noEmit` + build.

## Out of scope (YAGNI)

- Content generator default (stays mandatory explicit pick).
- Migrating cost caps / worker concurrency off `.env` (the `/settings` route leaves room for it later; not this change).
- Per-book launcher persistence (already exists, `launcher-config.ts`) — unchanged; it layers on top (explicit per-book pick still wins over the global default).
- The provider **credentials** (`GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) and infra (`DATABASE_URL`, `FLEET_HEAD_URL`, `AUTH_TOKEN`, `WORKER_CONCURRENCY`, `JOB_TIMEOUT_SECONDS`) **stay in `.env`** on each machine — the worker makes the actual API call and needs creds locally; these can't live in a DB row the head reads. This feature only moves model *selection*, never credentials.

## What gets deleted from `.env` (head + every worker)

`JUDGE_PROVIDER`, `JUDGE_MODEL`, `EXTRACT_PROVIDER`, `EXTRACT_MODEL`, `EXTRACT_TOC_TRANSPORT` — and the matching fields in `config.py`. After this ships, **no machine has a model value in `.env`**; the global default lives in the `launch_defaults` DB row, edited at `/settings`. Document this in the worker setup runbook + `.env.example`.

## Finish checklist (per CLAUDE.md)

Worklog + INDEX row; close the relevant WISHLIST/ROADMAP item; `git mv` this spec's plan to `plans/shipped/`; de-stale `README.md` / `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` / `docs/DATABASE.md` (new table) to describe the global-defaults flow and that `.env` model vars are **removed** (the DB row + `/settings` is the home; `.env` keeps only credentials + infra). Update the worker setup runbook + `.env.example` accordingly.
