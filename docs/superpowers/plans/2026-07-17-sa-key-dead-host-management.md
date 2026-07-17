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
   clear* — `self._applied_key_sha is not None or sa_key_active_path().exists() or
   os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")` — instead of the in-memory sha alone. Keeps
   the idle gate (defer while `self._tasks`), keeps the clear ops (already idempotent), and stays
   a no-op on an already-clean host so a persistent scrub row causes no per-heartbeat log spam.
2. **FE pure module** `web/src/lib/sa-key-hosts.ts`: `assignmentHosts(liveness, assignments)` →
   union of registry hosts ∪ assignment-only hostnames; assignment-only rows get
   `{online: false, lastHeartbeat: null, assignmentOnly: true}`. House pattern: pure function +
   `npx tsx` test file (like `sa-key-label.test.ts`).
3. **Panel wiring** (`sa-keys-panel.tsx`): iterate the union; assignment-only rows show status
   "gone" (registry row pruned) instead of a heartbeat age; Unassign/Scrub stay enabled (backend
   already accepts any hostname); the Assign dropdown stays enabled too (assigning to an offline
   host is already possible today and correctly applies when the host returns).
4. **Tests** for the offline-host-returns-with-local-key scenario (worker unit test, RED first).

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

**Known residual (accepted):** residue detection reads the active-key file + process env; an
exotic state where only the env *file* has the pair (vars unset, active.json gone) is missed until
the next boot re-loads the env file — at which point the same predicate catches it.

---

## Task 1 — Worker: residue-gated scrub (TDD)

**Files:** `app/services/worker.py` (scrub branch, ~:780-794),
`tests/services/test_worker_sa_key_sync.py` (append).

1. **RED** — add `test_restarted_worker_scrubs_persisted_key`: build `Worker(concurrency=1)`,
   `w._tasks = set()`, `w._applied_key_sha = None` (fresh boot), pre-seed residue:
   `storage.sa_key_active_path()` written under a tmp `var_dir` + monkeypatched env
   `GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CLOUD_PROJECT` + tmp `_WORKER_ENV_PATH` containing the
   pair; fake repo returns `{"scrub": True, "sha256": None, ...}`. Run `w._sync_sa_key()`; assert
   active.json unlinked, both env vars popped, env file lines removed, `CAPABILITIES` rebound
   (`can_gemini_api` False). Run: `uv run python -m pytest tests/services/test_worker_sa_key_sync.py -q`
   — MUST fail on the unlink/env asserts pre-fix (the guard skips the clear).
2. Also add `test_scrub_noop_when_no_residue`: clean env, no active.json, `_applied_key_sha=None`,
   scrub row present → `_sync_sa_key()` performs no clear (monkeypatch
   `sa_key_apply.clear_credentials_env` to record calls; assert not called).
3. **GREEN** — change the scrub branch condition from `if self._applied_key_sha is not None:` to a
   residue predicate (sha, active-key file, or env var present); keep the idle-gate `if
   self._tasks: return` inside; body unchanged.
4. Run the file + the sibling `tests/services/test_worker_startup_applies_key.py`; then
   `uv run python -m pytest tests/services/ -q`.
5. Commit: `fix(worker): scrub applies after restart — residue-gated, not in-memory-sha-gated`.

## Task 2 — FE pure module: host union (TDD)

**Files:** `web/src/lib/sa-key-hosts.ts` (new), `web/src/lib/sa-key-hosts.test.ts` (new).

1. **RED** — write `sa-key-hosts.test.ts` (npx-tsx style, `node:assert/strict`): union keeps all
   registry hosts with their liveness; adds assignment-only hostnames as
   `{online:false, lastHeartbeat:null, assignmentOnly:true}`; a host in both is NOT duplicated and
   keeps registry liveness (`assignmentOnly:false`); sorted by hostname; empty-inputs cases.
   Run: `cd web && npx tsx src/lib/sa-key-hosts.test.ts` — fails (module absent).
2. **GREEN** — implement `assignmentHosts(liveness: HostLiveness[], assignments: {hostname: string}[]): SaKeyHostRow[]`
   (extends `HostLiveness` with `assignmentOnly: boolean`). Import `HostLiveness` from
   `./host-liveness`.
3. `cd web && npx tsc -p tsconfig.app.json --noEmit`.
4. Commit: `feat(web): sa-key-hosts union — assignment-only (dead) hosts stay manageable`.

## Task 3 — Panel wiring

**Files:** `web/src/components/fleet/sa-keys-panel.tsx`.

1. Replace `const hosts = hostLiveness(...)` with
   `const hosts = assignmentHosts(hostLiveness(workersQ.data?.workers ?? []), assignments)`.
2. Status cell: assignment-only rows render `gone` (muted, no green dot) instead of `ago(null)`
   (`"—"`), with a `title` explaining "no registry row — worker last seen >10 min ago".
   `onlineCount`/`hosts.length` header math unchanged (union total is the honest denominator).
3. No action-button changes (Unassign/Scrub/Assign already keyed by hostname).
4. Gates: `cd web && npx tsc -p tsconfig.app.json --noEmit` && `cd web && npm run build`.
5. Commit: `feat(web): SA-keys panel shows assignment-only dead hosts (union view)`.

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
