# Fleet Phase 4.1 — per-role transport (content / extract / judge) — design

**Date:** 2026-06-11 · **Branch:** `feat/autonomous-fleet-engine` · **Status:** spec for user review

## 1. Goal + locked decision

Phase 4 made `transport` all-or-nothing: one value per job covering content, extract,
and judge (spec §3). Real usage immediately hit the wall: the operator wants e.g.
**gemini-API content billed to the $300 Vertex credit while extract+judge bill the
Claude *subscription*** — and to choose this **per job, in the UI** (locked with user
2026-06-11; per-worker env knobs rejected: "not an easy job to change env and restart
server for every job").

The original §3 objection (a mixed job lies about its cost) is already solved:
**per-row `auth_mode` attribution shipped in Phase 4** — each spawn records what it
actually used, so a mixed job's `$` readout stays honest. This amendment supersedes
§3's all-spawns-one-transport rule.

## 2. The model

Two new job fields (and batch launch defaults), each enum **`cli | api | inherit`**,
default **`inherit`**:

- `extract_transport` — auth mode for the extract spawn (provider stays pinned to
  `settings.extract_provider/_model` as today; only billing/auth is chosen).
- `judge_transport` — auth mode for both judge spawns (initial + post-regen).

Resolution at execution time (pure, in pipeline):
`resolved_X = job.X_transport if != "inherit" else job.transport`.
With both defaulted to `inherit`, **behavior is exactly Phase 4** — full back-compat;
existing rows backfill to `inherit` via server_default.

The user's case: job `transport=api` (gemini content → Vertex SA) +
`extract_transport=cli` + `judge_transport=cli` (claude subscription, no Anthropic
API key needed anywhere).

## 3. Validation (at POST /generate and /jobs/batch)

- Enum check: each of the three values ∈ its allowed set (`transport`: cli|api;
  role fields: cli|api|inherit).
- The existing content rules stand unchanged (api ⇒ provider ∈ API_PROVIDERS +
  explicit model).
- **No provider-compat check for role transports at POST** — judge/extract
  *providers* are worker settings the API can't see. A role resolving to api on a
  worker whose judge/extract provider has no api support (kimi/codex/opencode) fails
  **loudly at spawn** via the existing `_auth_env` raise. Documented, not silent.

## 4. Claim gate v2 (the real engineering)

Today: `WHERE transport='cli' OR :has_api_keys` (both keys). That's wrong twice under
per-role transports: an api-content job with cli judge/extract doesn't need an
Anthropic key; a cli-content job with api judge does.

**New worker capability params, computed once at startup** from the worker's env +
its own judge/extract provider settings:
- `can_claude_api` = real `ANTHROPIC_API_KEY` present
- `can_gemini_api` = `GEMINI_API_KEY` or Vertex SA pair present
- `judge_api_ok`   = capability for `settings.judge_provider` (claude→can_claude_api,
  gemini→can_gemini_api, else False)
- `extract_api_ok` = same for `settings.extract_provider`

**Claim predicate** (three AND-ed role conditions; SQL via bindparams):
- content: `transport='cli' OR (provider='claude' AND :can_claude_api) OR
  (provider='gemini' AND :can_gemini_api)`
- judge: `judge_transport='cli' OR (judge_transport='inherit' AND transport='cli')
  OR :judge_api_ok`
- extract: `extract_transport='cli' OR (extract_transport='inherit' AND
  transport='cli') OR :extract_api_ok`

Net effect: workers claim exactly the jobs they can serve; a fleet can mix a
keys-equipped PC and a subscription-only PC and the queue routes correctly.
(`_compute_has_api_keys` is replaced by this capability set; the startup warning
enumerates which sides are missing.)

## 5. Pipeline + judge threading

- `pipeline.run` resolves both role transports next to `transport = job.transport`.
- extract run fn gets `transport=resolved_extract`; content runs keep
  `transport=job.transport`; both `phase_judge.judge(...)` calls get
  `transport=resolved_judge`.
- **Loud-judge rule keys off the JUDGE's resolved transport** (`judge auth error +
  resolved_judge==api` → job fails loudly; cli judge keeps the soft degrade). Same
  for the regen-guard carve-out.
- The api failover restriction (requested-provider-only) applies **per spawn,
  based on that spawn's resolved transport** — an api extract doesn't cross
  providers; a cli extract failover behaves as today.

## 6. Attribution / $

Nothing new needed: every spawn already records `auth_mode` from the transport it
was passed, and `/agent/stats` already splits `$` per transport. A mixed job shows
its api rows priced and its cli rows at $0 — exactly the honest readout.

## 7. Frontend

- Generate form + fleet batch launcher: two compact selects — **Extract** and
  **Judge** — options `Auto (follow job)` / `CLI (subscription)` / `API (billed)`,
  default Auto. Always visible (meaningful for cli-content jobs too, e.g. cli
  content + api judge).
- Job/batch payloads + types carry the two fields. The `api` badge keeps meaning
  "content transport = api" (primary cost line); per-role detail is visible in the
  job drill-in later (deferred — not in scope).

## 8. Schema

Migration `0025`: `homework_jobs.extract_transport`, `homework_jobs.judge_transport`,
`batches.extract_transport`, `batches.judge_transport` — `String(16) NOT NULL
server_default 'inherit'`. **The batches unique key stays `(book_id, transport)`** —
role transports are launch defaults stamped onto member jobs, not part of batch
identity (re-launching the same book+transport with different judge billing reuses
the batch; the JOBS carry the truth). No backfill needed beyond the server_default.

## 9. Acceptance

1. Unit: resolution (`inherit` → job transport; explicit wins), claim-gate matrix
   (api-content+cli-roles claimable without Anthropic key; cli-content+api-judge
   requires judge capability), loud-judge keyed to resolved judge transport.
2. Real-DB: POST with role transports persists + validates; claim respects the
   three-way predicate.
3. Live smoke (the user's exact case, free-ish): job `transport=api` (gemini via
   Vertex SA) + `extract_transport=cli` + `judge_transport=cli` on a worker with NO
   Anthropic key → claimed, extract+judge rows `auth_mode=cli`, content rows
   `auth_mode=api`, `$` readout counts only the gemini side.

## 10. Out of scope

Per-phase (content-phase-level) transport; per-job judge/extract *provider* picks
(stay worker-pinned); batch identity changes; UI drill-in per-role badges.
