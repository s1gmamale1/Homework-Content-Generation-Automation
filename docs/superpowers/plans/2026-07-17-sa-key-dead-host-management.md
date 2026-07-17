# SA-key management for dead/offline hosts + unconditional startup scrub

## Approach & key decisions

**Problem (external review, all 4 claims verified in code):** (1) the SA-keys panel builds its
host table only from the worker registry (`sa-keys-panel.tsx:80` → `hostLiveness(workers)`), and
registry rows are pruned 10 min after the last heartbeat (`config.py:133`, `worker.py:681`) while
`sa_key_assignments` rows persist — so a dead host's assignment becomes unreachable ("3 workers"
on a key, no row to unassign, key un-deletable via the 409). (2) A restarted worker skips a
pending scrub: `_applied_key_sha` starts `None` (`worker.py:226`) and the scrub branch only
clears when it `is not None` (`worker.py:781-782`) — so "Scrub" can look done while the returning
machine keeps `var/sa_keys/active.json` + the env-file Vertex pair and happily bills the revoked key.

**Fix (4 parts, no migration, backend routes already accept any hostname):**
1. **Worker — residue-gated scrub** (`worker.py` scrub branch): clear when there is *anything to
   clear* — full residue predicate (gate-2 correction #3, all four sources, presence not
   truthiness):
   `self._applied_key_sha is not None`, OR `sa_key_active_path().exists()`, OR
   `"GOOGLE_APPLICATION_CREDENTIALS" in os.environ or "GOOGLE_CLOUD_PROJECT" in os.environ`, OR
   either credential line present in `_WORKER_ENV_PATH` (new pure helper
   `sa_key_apply.env_file_has_credentials(env_path) -> bool`). No accepted env-file-only
   residual — that gap is closed now. Keeps the idle gate (defer while `self._tasks`), keeps the
   clear ops (already idempotent), and stays a no-op on an already-clean host so a persistent
   scrub row causes no per-heartbeat log spam.
2. **FE pure module** `web/src/lib/sa-key-hosts.ts`: `assignmentHosts(liveness, assignments)` →
   union of registry hosts ∪ assignment-only hostnames; assignment-only rows get
   `{online: false, lastHeartbeat: null, assignmentOnly: true}`. Plus a pure status helper
   `assignmentOnlyStatus(registry: "ready" | "loading" | "error") -> "gone" | "checking" |
   "registry unavailable"` so the "gone" verdict is only ever rendered off a *successful*
   registry response (gate-2 correction #2 — no false "gone" flash while `/workers` loads;
   registry errors surface distinctly). House pattern: pure functions + `npx tsx` test file
   (like `sa-key-label.test.ts`).
3. **Panel wiring** (`sa-keys-panel.tsx`): iterate the union; assignment-only rows show the
   status from `assignmentOnlyStatus(...)` mapped off `workersQ` state (`isSuccess` → "gone",
   pending → "checking…", `isError` → "registry unavailable"); Unassign/Scrub stay enabled
   (backend already accepts any hostname); the Assign dropdown stays enabled too. **Honest scrub
   status** (gate-2 correction #1): scrub toast becomes "Scrub requested; applies when the host
   returns and is idle." and the assignment label becomes `SCRUB REQUESTED` (not `(scrubbed)`) —
   the API only records the revoke request; the wipe happens later, worker-side, when idle.
4. **Tests**: parameterized worker tests proving EVERY residue-OR branch in isolation (gate-2
   correction #4) — active-file only, process-env only, env-file only, in-memory-sha only —
   plus no-residue no-op and busy-worker defer; and the combined
   offline-host-returns-with-local-key scenario (RED first).

**Rejected alternatives:**
- *Worker self-acks scrub by deleting its assignment row after clearing* — rejected: assignments
  are keyed by **hostname**, not pc_id; two worker processes on one host (embedded + standalone)
  share the row, and the first to clear would delete the instruction before the second saw it.
  The scrub row persists; the operator dismisses it via Unassign once satisfied (now possible for
  dead hosts thanks to the union view).
- *Backend union endpoint* — rejected: the panel already fetches both `/sa-keys/assignments` and
  `/workers`; the union is pure display logic, belongs in a testable FE module.
- *Unconditionally re-running the clear every sync cycle* — rejected: per-heartbeat
  `logger.warning` spam + pointless file/env churn on clean hosts with a lingering scrub row.

**Load-bearing facts verified:** `repo.unassign` deletes the row regardless of `key_id`/scrub state
(`sa_keys.py:155-159`) so "dismiss a scrubbed husk" needs no new endpoint; `clear_credentials_env`
pops only the Vertex pair (GEMINI_API_KEY untouched — unchanged scope); `upsert_env_file(None)`
removes lines idempotently; existing worker-sync test harness in
`tests/services/test_worker_sa_key_sync.py` (fake session + monkeypatched repo + tmp env file).

**Known residual:** none — the gate-2 review closed the env-file-only gap (the residue predicate
now scans `_WORKER_ENV_PATH` directly via `env_file_has_credentials`).

---

## Task 1 — Worker: residue-gated scrub (TDD)

**Files:** `app/services/worker.py` (scrub branch, ~:780-794),
`app/services/sa_key_apply.py` (new helper `env_file_has_credentials`),
`tests/services/test_worker_sa_key_sync.py` (append),
`tests/services/test_sa_key_apply.py` (append helper tests if the file exists, else in the sync file).

1. **RED** — add a parameterized residue matrix `test_restart_scrub_clears_each_residue_source`
   (`@pytest.mark.parametrize`) where each case seeds **exactly one** residue source with
   `w._applied_key_sha = None` (fresh boot) and a fake repo returning
   `{"scrub": True, "sha256": None, "key_id": None, "project_id": None}`:
   - `active_file_only` — `storage.sa_key_active_path()` written under a tmp `var_dir`; env clean;
     tmp `_WORKER_ENV_PATH` empty → assert active.json unlinked after `_sync_sa_key()`.
   - `process_env_only` — monkeypatched `GOOGLE_APPLICATION_CREDENTIALS` (and separately a case
     with only `GOOGLE_CLOUD_PROJECT`, proving presence-not-truthiness with `""` value) → assert
     both vars popped.
   - `env_file_only` — tmp `_WORKER_ENV_PATH` containing `GOOGLE_APPLICATION_CREDENTIALS=...`
     line, env clean, no active.json → assert the line removed. (This is the branch the old
     predicate could never see.)
   - `in_memory_sha_only` — `w._applied_key_sha = "SHA-OLD"`, nothing else → assert sha reset to
     None (today's behavior, kept green).
   Each case also asserts `CAPABILITIES` rebound (`can_gemini_api` False). Plus:
   - `test_scrub_noop_when_no_residue` — clean everything → monkeypatch
     `sa_key_apply.clear_credentials_env` to record calls; assert not called (no log-spam churn).
   - `test_scrub_defers_while_busy` — residue present but `w._tasks = {object()}` → assert
     nothing cleared (idle gate holds).
   - `test_restarted_worker_scrubs_persisted_key` — the combined real-world scenario (all
     sources seeded at once), the reviewer's original case 4.
   Run: `uv run python -m pytest tests/services/test_worker_sa_key_sync.py -q` — the isolated
   file/env/env-file cases MUST fail pre-fix (the sha-only guard skips the clear).
2. **GREEN** — add `sa_key_apply.env_file_has_credentials(env_path: Path) -> bool` (True when a
   non-comment `GOOGLE_APPLICATION_CREDENTIALS=` or `GOOGLE_CLOUD_PROJECT=` line exists; False
   for a missing file — same line-parsing idiom as `upsert_env_file`). Change the scrub branch
   condition from `if self._applied_key_sha is not None:` to the four-source residue predicate
   (sha is not None / active-key file exists / either var **in** `os.environ` /
   `env_file_has_credentials(_WORKER_ENV_PATH)`); keep the idle-gate `if self._tasks: return`
   inside; clear body unchanged.
3. Run the file + the sibling `tests/services/test_worker_startup_applies_key.py`; then
   `uv run python -m pytest tests/services/ -q`.
4. Commit: `fix(worker): scrub applies after restart — four-source residue gate, not in-memory sha`.

## Task 2 — FE pure module: host union (TDD)

**Files:** `web/src/lib/sa-key-hosts.ts` (new), `web/src/lib/sa-key-hosts.test.ts` (new).

1. **RED** — write `sa-key-hosts.test.ts` (npx-tsx style, `node:assert/strict`): union keeps all
   registry hosts with their liveness; adds assignment-only hostnames as
   `{online:false, lastHeartbeat:null, assignmentOnly:true}`; a host in both is NOT duplicated and
   keeps registry liveness (`assignmentOnly:false`); sorted by hostname; empty-inputs cases.
   Status helper cases: `assignmentOnlyStatus("ready") === "gone"`,
   `("loading") === "checking"`, `("error") === "registry unavailable"`.
   Run: `cd web && npx tsx src/lib/sa-key-hosts.test.ts` — fails (module absent).
2. **GREEN** — implement `assignmentHosts(liveness: HostLiveness[], assignments: {hostname: string}[]): SaKeyHostRow[]`
   (extends `HostLiveness` with `assignmentOnly: boolean`) and
   `assignmentOnlyStatus(registry: "ready" | "loading" | "error")`. Import `HostLiveness` from
   `./host-liveness`.
3. `cd web && npx tsc -p tsconfig.app.json --noEmit`.
4. Commit: `feat(web): sa-key-hosts union — assignment-only (dead) hosts stay manageable`.

## Task 3 — Panel wiring

**Files:** `web/src/components/fleet/sa-keys-panel.tsx`.

1. Replace `const hosts = hostLiveness(...)` with
   `const hosts = assignmentHosts(hostLiveness(workersQ.data?.workers ?? []), assignments)`.
2. Status cell: assignment-only rows render `assignmentOnlyStatus(...)` mapped off `workersQ`
   state — `isSuccess` → `gone` (muted, no green dot, `title` "no registry row — worker last
   seen >10 min ago"), pending → `checking…`, `isError` → `registry unavailable`. Never render
   "gone" before the registry query has succeeded (no false flash).
   `onlineCount`/`hosts.length` header math unchanged (union total is the honest denominator).
3. **Honest scrub status:** scrub toast → "Scrub requested; applies when the host returns and is
   idle." (`sa-keys-panel.tsx:52`); assignment label `(scrubbed)` → `SCRUB REQUESTED`
   (`sa-keys-panel.tsx:255-256`). The persistent row stays — it IS the standing revoke
   instruction; Unassign dismisses it once the operator is satisfied.
4. No action-button changes (Unassign/Scrub/Assign already keyed by hostname).
5. Gates: `cd web && npx tsc -p tsconfig.app.json --noEmit` && `cd web && npm run build`.
6. Commit: `feat(web): SA-keys panel — dead-host union view + honest scrub-requested status`.

## Task 4 — Finish

1. Full suite: `uv run python -m pytest tests/ -q`; FE tsx tests + tsc + build.
2. Rebase check: `git fetch origin && git log HEAD..origin/Nggaev-v2`.
3. Worklog **0147** in `docs/memory/MASTER_MEMORY.md` + `INDEX.md` row (re-verify the tail number
   at write time — it goes stale mid-lane).
4. De-stale docs: `docs/CODE_MAP.md` / `docs/HOW_IT_WORKS.md` SA-key section (scrub semantics +
   union view), if they describe the old behavior.
5. `git mv` this plan into `docs/superpowers/plans/shipped/`.
6. Push per finishing-a-development-branch (user decides merge path).

**Operator note (post-ship):** any host scrubbed while its worker was down must have its worker
**restarted after pulling** this fix — the startup `_sync_sa_key()` then performs the clear. The
"(scrubbed)" row stays visible until you Unassign it (deliberate — it's the standing revoke
instruction).
