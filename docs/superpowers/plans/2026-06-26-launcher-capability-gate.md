# Plan — launcher-capability-gate-1: show only fleet-serveable provider/model/transport

**Branch:** `launcher-capability-gate` off `origin/Nggaev-v2`  ·  **Worktree:** `../HCGA-capability-gate`
**Commit prefix:** `cap:`  ·  **PR prefix:** `[capability-gate]`  ·  **Worklog (reserved):** `0085`  ·  **Migration:** `0035`
**No self-merge** — gatekeeper verifies + merges.

## Approach & key decisions

**Problem (verified in code):** `list_agent_models` (`def` at `app/api/v1/jobs.py:472`, takes **no session today**) returns the static `MODEL_MANIFEST` + `api_supported` + `tiers` with **zero fleet awareness**. The FE lists every manifest provider/model (provider `<Select>`s in `launcher.tsx` ~line 824, `routes/section.tsx` ~line 595, and `RoleAgentControls.tsx`) and gates only the **api toggle** by `api_supported` (claude/gemini). So an operator can pick `claude·api` with no `ANTHROPIC_API_KEY` on any worker, or a provider whose CLI is installed nowhere, and the job launches and sits **`pending` forever** (the api-job-claim-gate footgun class). Workers already KNOW their truth — `worker._compute_capabilities` (`worker.py:60`, frozen into `CAPABILITIES`) holds the api flags, and `agent.provider_cli_installed()` (`agent.py:243`) answers CLI-installed per provider — but it is **never published**: `WorkerNode` (`pc_id/last_heartbeat/status/notes`) has no capability column and `upsert_heartbeat` writes none.

**Chosen approach — publish-on-heartbeat + JSONB union + endpoint annotation + FE grey-out.**
1. New nullable `workers.capabilities` JSONB column (migration `0035`). Blob shape: `{"cli": {<all 5 providers>: bool}, "api": {"claude": bool, "gemini": bool}}`.
2. Worker computes the blob once at startup (CLI-installed via `provider_cli_installed` for all 5; api via the existing `_compute_capabilities` flags) and writes it on every `upsert_heartbeat` (the blob is process-static; re-writing each beat is cheap + idempotent).
3. Head repo `aggregate_fleet_capability(session, *, stale_after_seconds)` unions the blob over **online** workers (reuse `is_online`'s DB-clock staleness). Zero online → `{"online": false, ...}`.
4. `/agent/models` injects a session and adds a `fleet` block (additive — existing keys untouched).
5. FE derives serveability per `(provider, transport)` from `fleet` and **greys out + tooltips** unservable picks.

**Locked decisions (user, 2026-06-26):**
- **Unservable UX = grey-out + visible reason** (keep visible, disabled, explain why). Not hide, not soft-warn. **Mechanism (see "Grey-out mechanism" below) = disabled item + the reason appended into the *visible label*, NOT a native `title` tooltip** — a disabled Radix `SelectItem` carries `pointer-events-none` (`select.tsx:68`), so a hover tooltip never fires on the greyed row.
- **Empty fleet (zero online) = fail-open + offline banner.** Don't gate anything; show "No workers online — launches will queue until one connects." Preserves the legitimate queue-ahead flow (a job CAN launch before a worker connects). FE serveability returns `ok` for everything when `!fleet.online`.
- **Granularity = provider × transport only.** api-creds / CLI-installed is a per-provider signal. We do NOT gate per individual model id, and **preview-model QUOTA is explicitly out of scope** (undetectable without a billed call). All models under a serveable provider stay enabled.

**Rejected:** (a) head infers from its own `os.environ` — wrong for a multi-machine fleet (the head's creds ≠ the worker's; that mismatch is the bug); (b) typed boolean columns per provider×transport — 10+ columns + a migration per provider; JSONB matches the existing `custom_prompts`/`selected_phases` pattern and the read-all-then-union access shape.

**Grey-out mechanism (resolves the gatekeeper's 🔴 #1 — the locked UX as originally written is NOT implementable).** A disabled `SelectItem` has `pointer-events-none`, so a `title=` hover tooltip is dead. Chosen: **disable the item AND append the reason into its rendered label** (e.g. `codex — CLI not on any worker`, `claude · api — no API creds on fleet`) so the explanation needs no hover. For the **transport toggle** (a button group, not a `SelectItem`) the reason renders as a small amber helper line beneath it — reusing `RoleAgentControls`' existing `warning` `<p>` pattern (`text-amber-300/90`). Rejected the heavier `@radix-ui/react-tooltip` wrapper per item: more markup, and the label-suffix is unambiguous without a hover gesture. The offline-banner already covers the empty-fleet case as plain visible text.

**Two-level FE gating (load-bearing):** the provider `<Select>` disables a provider option **only when no worker can run it in any mode** (`!cli[p] && !api[p]`, via `providerServeableAnyMode`); the **api transport toggle / `API` option** is what disables the api-specific case (`cli[p]` true but `!api[p]`). This keeps cli-only providers pickable on cli while still blocking their api path. The launcher/section toggle AND-s the existing `apiSupported` gate with fleet creds; `RoleAgentControls` has **no such gate today — it is built from scratch** (see Task 6).

**Per-role `inherit` resolution (resolves the gatekeeper's 🟠 #2).** `RoleAgentControls` transport is a 3-way `inherit | cli | api`, and the role's provider can differ from the job provider (judge/extract). Serveability of an `inherit` role depends on the **job transport it resolves to**. The `serveability` helper stays pure `(fleet, provider, "cli"|"api")`; the **caller resolves `inherit` first** via `resolveRoleTransport(roleTransport, jobTransport) = roleTransport === "inherit" ? jobTransport : roleTransport`. So `RoleAgentControls` must receive the **job transport as a new prop** (`jobTransport: "cli" | "api"`) — it does not have it today. Gating applies only when a **concrete** role provider is chosen (provider `Auto`/null = backend-resolved, ungateable).

**Liveness freshness (resolves the gatekeeper's 🟠 #3).** `fleet` rides on `/agent/models`, fetched once via React Query with no `refetchInterval` today — so the banner/greying would freeze at page-load state and a worker connecting later would never un-grey. The model query gains a `refetchInterval` (≈5 s; the fleet dashboard polls at 3500 ms) so capability tracks the live fleet. Note: `/agent/models` becomes a **DB-touching endpoint** (one cheap online-workers SELECT per poll) — acceptable.

**Scope guard:** publishing capability does NOT change the claim gate — `claim_next_job` already AND-s per-job transports against the worker's local `CAPABILITIES`. This feature only makes that truth visible at selection time. No claim-gate edits.

---

## Tasks (TDD per task · commit per task · stage only listed files)

### Task 1 — Migration + model column for `workers.capabilities`
- **Files:** `alembic/versions/0035_worker_capabilities.py` (new), `app/models/worker.py`.
- **Model:** add `capabilities: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)` (import `JSONB` from `sqlalchemy.dialects.postgresql`). Nullable so pre-upgrade rows / workers that never publish read as "unknown".
- **Migration:** `revision="0035_worker_capabilities"`, `down_revision="0034_widen_prompt_hash"`; `op.add_column("workers", sa.Column("capabilities", postgresql.JSONB, nullable=True))` / `op.drop_column` in `downgrade`. (Revision id ≤ 32 chars — `0035_worker_capabilities` = 24, OK.)
- **Test (RED→GREEN):** `tests/integration/test_worker_capabilities_column.py` — real-PG (skip unless `RUN_DB_INTEGRATION=1`): upgrade head, insert a `WorkerNode` with a `capabilities` dict, read it back equal. Acceptance against an **isolated scratch DB** (`createdb`-then-`dropdb`, never edu_copy/edu_homework — see SDD acceptance recipe).
- **Verify:** `uv run --extra dev python -m pytest tests/integration/test_worker_capabilities_column.py -q` (with scratch `DATABASE_URL`).
- **Commit:** `cap: add nullable workers.capabilities JSONB column (migration 0035)`

### Task 2 — Worker builds + publishes the capability blob
- **Files:** `app/services/worker.py`, `app/repositories/workers.py`, tests `tests/services/test_worker_capabilities.py`, `tests/repositories/test_upsert_heartbeat_capabilities.py`.
- **Builder:** in `worker.py`, add a pure `_capability_blob(env, *, judge_provider, judge_model, extract_provider) -> dict` (or extend `_compute_capabilities`) that returns the **published** shape:
  `{"cli": {name: agent.provider_cli_installed(name) for name in providers.PROVIDERS}, "api": {"claude": can_claude_api, "gemini": can_gemini_api}}`.
  Compute once at module load next to `CAPABILITIES` (e.g. `CAPABILITY_BLOB`). Keep `CAPABILITIES` (claim-gate) untouched.
- **Repo:** widen `upsert_heartbeat(session, pc_id, *, status="online", capabilities: dict | None = None)` — include `capabilities` in both the insert `.values(...)` and the `on_conflict_do_update` `set_` **only when not None** (don't clobber a known blob with NULL on a status-only beat).
- **Wire:** `Worker._drain_check_and_beat` passes `capabilities=CAPABILITY_BLOB` into `upsert_heartbeat`.
- **Tests:** (a) `_capability_blob` returns all 5 cli keys + 2 api keys; api flags follow env (claude key set ⇒ `api.claude` true; cleared ⇒ false); cli flag follows `provider_cli_installed` (monkeypatch `shutil.which`). (b) `upsert_heartbeat` with `capabilities=` writes it; a later call with `capabilities=None` does NOT null it (real-PG or a focused unit on the statement). RED-prove the no-clobber branch.
- **Verify:** `uv run --extra dev python -m pytest tests/services/test_worker_capabilities.py tests/repositories/test_upsert_heartbeat_capabilities.py -q`
- **Commit:** `cap: worker publishes per-provider cli+api capability on heartbeat`

### Task 3 — Head-side aggregation over online workers
- **Files:** `app/repositories/workers.py`, `tests/repositories/test_aggregate_fleet_capability.py`.
- **Fn:** `aggregate_fleet_capability(session, *, stale_after_seconds) -> dict`. Select online `WorkerNode` rows (heartbeat ≥ DB-now − stale, same predicate as `has_live_workers`). If none → `{"online": False, "workers_online": 0, "cli": {}, "api": {}}`. Else union: `online: True`, `workers_online: n`, and for each provider seen across blobs `cli[p] = any(w.capabilities["cli"].get(p) for online w)`, likewise `api`. Treat a NULL/absent blob as contributing nothing (all-False), so a legacy worker with no published caps doesn't falsely enable anything — but it still counts toward `workers_online` (it IS online; fail-open banner only triggers at **zero** online).
- **Test (real-PG):** 0 workers → `online False`; 2 workers with disjoint caps (one cli-claude, one api-gemini) → union has both; stale worker excluded from the union.
- **Verify:** `uv run --extra dev python -m pytest tests/repositories/test_aggregate_fleet_capability.py -q`
- **Commit:** `cap: aggregate_fleet_capability — union over online workers`

### Task 4 — `/agent/models` returns the `fleet` block
- **Files:** `app/api/v1/jobs.py`, `tests/api/test_agent_models_fleet.py`.
- **Change:** make `list_agent_models` take `session: AsyncSession = Depends(get_session)` and append `"fleet": await workers_repo.aggregate_fleet_capability(session, stale_after_seconds=settings.worker_registry_stale_seconds)` (same staleness field the `/workers` liveness endpoint uses, `app/api/v1/workers.py:15`). Existing `providers`/`api_supported`/`tiers` keys unchanged.
- **Test (real-PG or seeded session):** endpoint with no workers → `fleet.online is False`; with a seeded online worker blob → `fleet.cli`/`fleet.api` reflect it. Assert `providers`/`api_supported` still present (no regression).
- **Verify:** `uv run --extra dev python -m pytest tests/api/test_agent_models_fleet.py -q`
- **Commit:** `cap: /agent/models exposes aggregate fleet capability`

### Task 5 — FE types + serveability helper
- **Files:** `web/src/lib/types.ts`, `web/src/lib/serveability.ts` (new).
- **Types:** add `FleetCapability { online: boolean; workers_online: number; cli: Record<string, boolean>; api: Record<string, boolean>; }` and `fleet?: FleetCapability` on `ProviderModelManifest`.
- **Helpers (pure, the single source of truth — both launcher and section/role import these):**
  - `serveability(fleet: FleetCapability | undefined, provider: string, transport: "cli" | "api"): { ok: boolean; reason: string | null }` — fail-open: `if (!fleet || !fleet.online) return { ok: true, reason: null }`; else `ok = transport === "api" ? !!fleet.api[provider] : !!fleet.cli[provider]`; reason when not ok: api → `no API creds on fleet`, cli → `CLI not on any worker` (short — these get appended to a `SelectItem` label, so keep them tight).
  - `providerServeableAnyMode(fleet, provider): boolean` = `!fleet?.online || !!fleet?.cli[provider] || !!fleet?.api[provider]` (drives the provider-option disable).
  - `resolveRoleTransport(roleTransport: "inherit" | "cli" | "api", jobTransport: "cli" | "api"): "cli" | "api"` = `roleTransport === "inherit" ? jobTransport : roleTransport` (resolves an `inherit` role to the job's effective transport before a serveability check — see the per-role decision above).
- **Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit` (no FE test runner configured — standalone typecheck is the gate; the repo's `npm run build` runs `tsc -b` then `vite build` — don't conflate the two).
- **Commit:** `cap: FE fleet-capability types + serveability/role-transport helpers`

### Task 6 — FE wiring: grey-out (label-suffix) + offline banner + freshness
- **Files:** `web/src/components/fleet/launcher.tsx`, `web/src/components/fleet/RoleAgentControls.tsx`, `web/src/routes/section.tsx`.
- **Refetch (do first):** add `refetchInterval: 5000` (and `refetchOnWindowFocus: true`) to the agent-models query in `launcher.tsx` and `routes/section.tsx` so `fleet` tracks the live fleet (a worker connecting later un-greys without a manual reload). `/agent/models` now hits the DB per poll — fine.
- **Provider `<Select>` (launcher + section):** for each provider `<SelectItem>`, when `!providerServeableAnyMode(fleet, p)` set `disabled` AND render the label as `{p} — no worker runs it` (label-suffix, since a disabled item's `title` won't fire — see Grey-out mechanism). Servable providers render unchanged.
- **Transport toggle (launcher + section):** AND the existing `apiSupported` gate with `serveability(fleet, provider, "api").ok` to enable/disable the API side; when disabled-by-fleet, show the reason as a small amber helper line beneath the toggle (the `RoleAgentControls.warning` `<p>` pattern). Keep the existing `apiSupported`→cli reset effect; extend it so an api selection that becomes fleet-unservable also resets to cli.
- **`RoleAgentControls` (gating built from scratch — nothing to mirror):**
  - New prop `jobTransport: "cli" | "api"` (launcher/section pass their `transport` state). 
  - Provider `<SelectItem>`: disable + label-suffix when `!providerServeableAnyMode(fleet, p)` (skip when provider is `Auto`).
  - Transport `<SelectItem>`s: with a concrete role provider, disable `API` when `!serveability(fleet, provider, "api").ok`, and disable `Auto`(`inherit`) when `!serveability(fleet, provider, resolveRoleTransport("inherit", jobTransport)).ok` (i.e. inherit would resolve to a job transport this provider can't serve). Surface the active reason via the existing `warning` `<p>`.
  - `manifest` already carries `fleet` (same query object) — read it from `manifest.fleet`; no new prop for fleet.
- **Offline banner:** when `!fleet?.online`, render one prominent line in the launcher panel and the section launcher — "No workers online — launches will queue until one connects." Nothing greyed in this state (fail-open).
- **Reset guard:** if the currently-selected provider/transport becomes unservable (and `fleet.online`), nudge selection to a servable default (extend the existing reset `useEffect`s) so the launch button can't submit a known-pending combo.
- **Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
- **Commit:** `cap: launcher/section/role pickers grey out unservable picks + offline banner`

### Task 7 — Finish (part of the same PR, not deferred)
- Full suite: `uv run python -m pytest tests/ -q` (offline) + the real-PG integration subset on the scratch DB.
- **Rebase check** before PR: `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if base moved, rebase onto `origin/Nggaev-v2`, re-run suite.
- Worklog `0085` → `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`; close `launcher-capability-gate-1` in `docs/memory/WISHLIST.md`; `git mv` this plan → `docs/superpowers/plans/shipped/`.
- **De-stale reference docs** the change touches: `docs/HOW_IT_WORKS.md` (fleet/heartbeat + launcher picker), `docs/CODE_MAP.md` (`workers.capabilities`, `aggregate_fleet_capability`, `/agent/models` fleet block), `docs/DATABASE.md` (new column). README only if the picker behavior is user-facing enough to mention.
- Push branch, open PR `[capability-gate] launcher-capability-gate-1 …`. **No self-merge.**

## Acceptance gate
No generation-path change (this is selection-UX + a heartbeat field), so no CLI smoke is required by the acceptance rule. The binding proofs are: (1) real-PG migration up/down on a scratch DB; (2) real-PG `aggregate_fleet_capability` union/empty/stale tests; (3) `/agent/models` returns `fleet` with no workers (`online:false`) and with a seeded worker; (4) `tsc --noEmit` + `npm run build` clean. RED-prove the no-clobber heartbeat branch and the empty-fleet fail-open so neither assertion is vacuous.
