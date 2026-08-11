# Operator-token hard-cut rotation

Use this runbook to replace the historical weak operator token with one strong
shared token on the head and every worker. The rollout is head-first and takes
place behind a temporary all-claim version fence.

An API pause is not a global claim fence: `api_paused_at` blocks only jobs whose
resolved transport spends through an API. CLI jobs can still claim, and a job
that has already become `done` can still be finishing process-local Notion
archival. The temporary version floor, process drains, and zero-task checks are
therefore load-bearing.

Automation may prepare files and operate worker drains/restarts under the
operator's authorization. **DO NOT restart or kill the head from automation.**
The user/operator owns the head process and performs that restart explicitly.
This rotation does not delete, rotate, assign, unassign, or scrub any provider
credential.

## 1. Capture ownership and install the all-claim fence

Read the complete singleton before changing anything:

```sql
SELECT api_paused_at,
       api_paused_reason,
       min_worker_version,
       min_worker_version_stamped_by,
       min_worker_version_stamped_at
FROM budget_state
WHERE id = 1;
```

Record these immutable rollout variables outside shell history/logs:

- `observed_pause_at` and `observed_pause_reason`;
- `prior_floor`, `prior_floor_stamped_by`, and `prior_floor_stamped_at`;
- `target_code_version`, read from the exact code staged for deployment.

Before computing either fence, inventory **every registry row**, not only rows
currently reported online:

```sql
SELECT pc_id,
       last_heartbeat,
       status,
       capabilities->>'code_version' AS reported_code_version,
       capabilities->>'git_sha' AS reported_git_sha
FROM workers
ORDER BY pc_id;
```

First disable every known worker supervisor/scheduled auto-restart without
stopping the currently running process. This freezes the set of service starts
while the evidence and floor are built. Reconcile the result with the fleet
host/process inventory, including all
**online, retained, and offline-known** processes. Under the protected deployment
channel, inspect the actual head and worker service environments for
`WORKER_CODE_VERSION`; an unset override and a proven bounded integer are
different evidence states. Inventory each model-calling process and each host
configuration separately—two processes on one PC can use different service
files.

An unexpected or ahead override is a stop condition even though its numeric
value participates in fence sizing: explain it, remove/correct it, restart the
affected process, and repeat the inventory. An invalid or unreadable override is
also a stop condition; do not trust the heartbeat as a substitute. A known
offline/unreachable host whose effective code, service configuration, or
override cannot be read and bounded is an **unconditional STOP** for this
rotation.

The only parking exceptions are independently verified controls outside that
worker binary: a disabled supervisor/scheduled task with durable proof, explicit
network/DB isolation, or a head-side mechanism that the old worker code cannot
bypass. A DB **SA scrub tombstone alone never qualifies**—the scrub claim gate is
worker-local, so an older binary or ahead override may ignore it and claim.

An exact-binary proof is acceptable only when the SHA proves both its version
gate and scrub/tombstone claim gate, and its `WORKER_CODE_VERSION` override is
readable and bounded. Otherwise abort until the host is reachable or an external
park is independently established. Preserve that external park after the final
reopen until the host is updated and attested. Do not create or dismiss a
provider-credential scrub as part of this auth rotation.

Calculate the two checked values using the production helper. Populate the two
lists only from the reconciled evidence above; do not paste the example values:

```python
from app.services.operator_auth import rotation_version_floors

final_floor, temporary_floor = rotation_version_floors(
    prior_floor=prior_floor,
    target_code_version=target_code_version,
    reported_code_versions=reported_code_versions,
    configured_overrides=configured_worker_code_versions,
)
```

The helper enforces PostgreSQL's signed-Integer range (`0..2_147_483_647`) and
aborts if the maximum known version cannot be incremented without overflow.
Its exact contracts are:

- `final_floor = max(prior_floor or 0, target_code_version)`;
- `temporary_floor` is one greater than the maximum of the prior floor, target,
  every effective reported version, and every configured override.

The temporary floor therefore blocks a known ahead-override process even if it
starts before attestation. An unknown-version worker fails closed. Keep all
worker supervisors disabled until this fence is installed and read back; then
repeat the version/config inventory. Any new process, row, override, or mismatch
means abort the rotation and rebuild the evidence/floors before continuing.

Record either `pause_owned=true` or `pause_owned=false`:

- If the row is unpaused, acquire this operation's API pause with the scoped
  write below. A one-row result means `pause_owned=true`.
- If any foreign reason is present—including `manual-blocker-remediation`—do
  not replace it. Record `pause_owned=false`, its exact timestamp/reason, and
  require it to remain continuously set.
- If the reason is already `operator-auth-rotation`, prove this run owns that
  acquisition. Otherwise treat it as foreign.

```sql
UPDATE budget_state
SET api_paused_at = now(), api_paused_reason = 'operator-auth-rotation'
WHERE id = 1
  AND api_paused_at IS NULL
  AND api_paused_reason IS NULL;
```

Re-read the row, update the recorded observed pause fields when this run acquired
it, then install the version fence with a full expected-state predicate:

```sql
UPDATE budget_state
SET min_worker_version = :temporary_floor,
    min_worker_version_stamped_by = 'operator-auth-rotation',
    min_worker_version_stamped_at = now()
WHERE id = 1
  AND min_worker_version IS NOT DISTINCT FROM :prior_floor
  AND min_worker_version_stamped_by IS NOT DISTINCT FROM :prior_floor_stamped_by
  AND min_worker_version_stamped_at IS NOT DISTINCT FROM :prior_floor_stamped_at
  AND api_paused_at IS NOT DISTINCT FROM :observed_pause_at
  AND api_paused_reason IS NOT DISTINCT FROM :observed_pause_reason;
```

Require exactly one affected row and read it back. Abort on any mismatch. Keep
`temporary_floor` and `min_worker_version_stamped_by='operator-auth-rotation'`
unchanged through the head restart, every worker restart, and all attestation.

## 2. Drain and stop every online worker process

Enumerate the live workers registry by full process ID (`hostname:pid@sha`), not
by hostname. Send one drain request per online process ID. Disable each worker's
supervisor/scheduled auto-restart for the maintenance window so a gracefully
exited worker cannot immediately return. The temporary version floor already
prevents any new claim while drain signals propagate.

Wait for each process to report draining, finish its in-memory task set, and
exit. A homework row becoming terminal is insufficient: post-done Notion
archival runs inside the worker task after the job is marked `done`. Process
drain/exit is the proof that this archival work has also settled.

Require all three independent drain views:

1. Database jobs:

   ```sql
   SELECT count(*) AS active_jobs
   FROM homework_jobs
   WHERE status IN ('running', 'cancelling');
   ```

2. Provider limiter holders:

   ```sql
   SELECT count(*) AS active_credential_slots
   FROM credential_slots;
   ```

3. Runtime state: every dedicated-worker OS/process supervisor reports zero
   worker processes, and the still-running head reports no embedded worker task.
   Recheck twice, at least one registry-heartbeat interval apart.

All counts/tasks must be zero. Do not cancel jobs, kill model subprocesses, or
manually delete limiter rows to manufacture a drain.

Before its user-owned restart, stage and attest `WORKER_CONCURRENCY=0` on the
head. If the current head had an embedded worker, its full process ID must have
received the same drain and its embedded task must be stopped. A head with a
model-calling worker enabled is not safe to rotate.

## 3. Snapshot database credential metadata

Capture these read-only facts. Do not select or print credential bytes:

```sql
SELECT count(*) AS stored_vertex_keys FROM sa_keys;

SELECT hostname, key_id, scrub_requested_at
FROM sa_key_assignments
WHERE hostname = 'Host-59';
```

Require exactly six stored Vertex key rows. Require exactly one Host-59 row,
with its existing non-null `key_id` and `scrub_requested_at IS NULL`. Save that
key ID for the post-check; this rotation must preserve it exactly.

## 4. Snapshot the vault through the production safety boundary

Run this on the head under the same account and `VAR_DIR` used by the service.
The production helper hardens the vault, holds the verified directory
fd/Windows handles while enumerating and hashing, rejects unsafe or unknown
entries and unresolved delete quarantines, and returns digests only:

```bash
umask 077
uv run python - <<'PY' > operator-auth-vault-sha256.before
from app.services import sa_key_vault

sa_key_vault.harden_vault()
snapshot = sa_key_vault.snapshot_uuid_inventory()
if len(snapshot) != 6:
    raise SystemExit("STOP: expected exactly six stored Vertex key files")
for key_id, digest in sorted(snapshot.items()):
    print(f"{key_id}.json {digest}")
PY
```

Do not replace this with shell `find`, direct `pathlib` iteration, or file byte
reads. Any vault refusal is a stop/manual-investigation condition. Do not rename
or delete a quarantine or unsafe entry by hand; classify it against settled DB
metadata and let head startup reconciliation act only after it is understood.

## 5. Generate one token and its non-disclosing expected fingerprint

Create a protected temporary file without putting the token in terminal output,
shell history, logs, tickets, or chat:

```bash
umask 077
TOKEN_FILE="$(mktemp)"
export TOKEN_FILE
uv run python - <<'PY' > "$TOKEN_FILE"
import secrets
print(secrets.token_urlsafe(48))
PY
test "$(wc -l < "$TOKEN_FILE")" -eq 1
```

Do not run shell tracing (`set -x`). Compute the exact fingerprint that running
workers must publish. This prints only a domain-separated SHA-256 identifier:

```bash
expected_auth_fingerprint="$({ uv run python - <<'PY'; } 2>/dev/null
import os

from app.services.operator_auth import runtime_token_set_fingerprint

with open(os.environ["TOKEN_FILE"], encoding="ascii") as handle:
    token = handle.read().rstrip("\n")
fingerprint = runtime_token_set_fingerprint(
    token, allow_insecure_local=False
)
if fingerprint is None:
    raise SystemExit("STOP: generated token failed startup policy")
print(fingerprint)
PY
)"
export expected_auth_fingerprint
test -n "$expected_auth_fingerprint"
```

The raw token must never appear in the fingerprint, workers registry, logs, or
rollout evidence.

## 6. Stage the same value everywhere

Using the existing protected deployment channel, replace `AUTH_TOKEN` in the
head and every worker `.env` with the **same strong token** read from
`$TOKEN_FILE`. Do not print it. Set:

```dotenv
ALLOW_INSECURE_LOCAL_AUTH=false
```

On the head also require:

```dotenv
WORKER_CONCURRENCY=0
```

Never stage `AUTH_TOKEN=123,<new>` (or `<new>,123`). Startup rejects every weak
member, and retaining the old value defeats the hard cut. Do not change
`GEMINI_API_KEY`, either `GOOGLE_*` variable, `active.json`, SA assignment rows,
or the six stored Vertex objects.

Stage the new code on the head and all workers while worker processes remain
stopped. A staged file is not proof that a running process loaded it.

## 7. Operator restarts and attests the head

**DO NOT restart or kill the head from automation.** After all files/code are
staged and the operator has independently confirmed `WORKER_CONCURRENCY=0`, the
operator restarts the head. Do not start workers yet.

The old worker processes have already exited, so there is no token mismatch
traffic and no post-done archive work left. The temporary version floor remains
strictly above the target version. The new head's raise-only automatic stamp
cannot lower it.

Wait for head health and prove the head accepts the staged token without printing
it: load the protected token file into a non-echoing variable and require a 200
from one normal authenticated GET. Missing/invalid credentials must return 401.
This is the evidence that the head accepts the staged token; the staged `.env`
alone is not evidence. Re-attest the running head has
`WORKER_CONCURRENCY=0` and no embedded worker task.

## 8. Perform rolling worker restarts behind the fence

For each drained worker process, clear only that process's drain state immediately
before restarting it; the temporary version floor still blocks every claim.
Re-enable its supervisor and start it on the target code/token. Wait for its new
full process-ID heartbeat before moving to the next process.

Attest **every restarted online model-calling process**, not merely each hostname:

- deployed code SHA/version and ineligibility against `temporary_floor`;
- `capabilities.auth_token_fingerprint == expected_auth_fingerprint` exactly;
- expected worker/agent/credential concurrency;
- expected provider capabilities and credential identity;
- fresh healthy heartbeat, with no claim or model call while fenced.

The resulting evidence set must cover every online model-calling process. Its
token fingerprint is the published `auth_token_fingerprint`, compared exactly
to the expected value—not a hostname-level inference.

If a PC runs two model-calling processes, attest both. Any missing/`None`/`local-dev`
fingerprint or mismatch is a hard stop. An offline host is not rollout-complete:
leave it fenced by the version floor and any independently verified external
park until it is updated and attested. A worker-local SA tombstone is only
supplemental evidence. Do not remove an offline fence to inflate capacity.

## 9. Post-rotation verification

Perform every check before lowering the temporary floor:

1. Re-run the production vault snapshot command into
   `operator-auth-vault-sha256.after` and compare it byte-for-byte with the
   before file. Require six UUID files and no vault refusal.
2. Re-run the credential DB queries. Require six DB rows and the exact saved
   Host-59 `key_id` with `scrub_requested_at IS NULL`.
3. Verify vault POSIX modes or Windows protected DACLs using production vault
   inspection; never expose file contents.
4. Verify the head accepts the new token and is running with
   `WORKER_CONCURRENCY=0` and no embedded worker task.
5. Verify every restarted online model-calling process's code, version,
   `auth_token_fingerprint`, concurrency, credential/capability identity, and
   heartbeat. Confirm plain `GEMINI_API_KEY` posture is unchanged.
6. Exercise the auth matrix: missing/invalid credentials return 401; a valid
   header returns 200; `?token=` and header+query are both rejected on every
   `/api/v1/sa-keys*` route. Intended normal SSE/source query auth remains.
7. Re-run `active_jobs` and `active_credential_slots`; require both zero. Inspect
   worker logs/OS state and require no claim/model call occurred under the fence.
8. Re-read `budget_state`; require the temporary floor/stamp and original pause
   ownership to be unchanged.

Remove the protected token temp only after all intended processes are attested:

```bash
rm -f -- "$TOKEN_FILE"
unset TOKEN_FILE
```

Keep `expected_auth_fingerprint` in the non-secret rollout evidence.

## 10. Final owner-scoped reopen

When `pause_owned=true`, set the floor to `final_floor` and clear only this
operation's pause in one transaction. Never blindly restore `prior_floor`:
for example, a prior floor of 953 and target version 1000 must finish at 1000,
so an offline v954 worker remains stale until it is updated. The new stamp
records the attested hard-cut deployment rather than falsely restoring the old
stamp metadata. The full fence/owner predicate makes the floor transition and
unpause one indivisible final gesture:

```sql
BEGIN;
UPDATE budget_state
SET api_paused_at = NULL,
    api_paused_reason = NULL,
    min_worker_version = :final_floor,
    min_worker_version_stamped_by = 'operator-auth-rotation-final',
    min_worker_version_stamped_at = now()
WHERE id = 1
  AND api_paused_reason = 'operator-auth-rotation'
  AND min_worker_version = :temporary_floor
  AND min_worker_version_stamped_by = 'operator-auth-rotation';
-- Require exactly one affected row before COMMIT.
COMMIT;
```

If `pause_owned=false`, emit no clearing SQL and **do not restore or lower the
temporary floor**. The foreign pause does not block CLI claims, so lowering the
all-claim fence would silently reopen generation. Record a fence handoff with
`foreign_pause_reason`, `foreign_pause_at`, the prior-floor snapshot, temporary
floor, final floor, attested process set, expected fingerprint, and retained
offline external-parking evidence. The foreign owner must explicitly accept the
fence handoff.
The accepting owner then performs this floor-only transition:

```sql
BEGIN;
UPDATE budget_state
SET min_worker_version = :final_floor,
    min_worker_version_stamped_by = 'operator-auth-rotation-final',
    min_worker_version_stamped_at = now()
WHERE id = 1
  AND api_paused_at IS NOT DISTINCT FROM :foreign_pause_at
  AND api_paused_reason IS NOT DISTINCT FROM :foreign_pause_reason
  AND min_worker_version = :temporary_floor
  AND min_worker_version_stamped_by = 'operator-auth-rotation';
-- The accepting foreign owner requires exactly one affected row.
COMMIT;
```

That handoff transaction deliberately leaves the foreign pause untouched. Only
after either final transaction commits may eligible claiming resume. Offline
stragglers below `final_floor` remain stale, and unbounded/unreachable hosts keep
their independently enforced external park. Re-enable any maintenance-disabled
supervisor policy only after its worker is already running and attested.

## Rollback

Rollback stays behind the same temporary version floor and zero-task drain. The
default recovery is a **sealed strong replacement** on the **current hardened
code**. A code-version fallback is permitted only when preflight recorded a
specific `designated_hardened_rollback_ref` whose reviewed build demonstrably
retains **Tasks 1–6**: fail-closed startup auth, header-only SA routes,
held-handle vault protection, transaction-safe SA operations, startup
reconciliation, runtime token fingerprinting, and this exact all-claim floor
protocol. An arbitrary historical build is prohibited. If that evidence is
absent, keep the current hardened build and rotate only the token. A rollback
must never restore `123`, never add it beside a strong token, and never bypass
startup validation.

The operator again owns the head restart, which remains
`WORKER_CONCURRENCY=0`. Workers restart and publish the rollback token-set
fingerprint while still fenced. Preserve the six stored Vertex objects, Host-59
assignment, plain API-key posture, offline fences, and foreign pause ownership.
Use the same checked version inventory, `final_floor`, and owner-scoped
reopen/handoff rules; never lower the all-claim fence merely because the API
pause remains set.
