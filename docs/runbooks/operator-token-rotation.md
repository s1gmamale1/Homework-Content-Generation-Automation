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
- `target_code_version`, read from the exact code staged for deployment;
- `temporary_floor = max(prior_floor or 0, target_code_version) + 1`.

The temporary floor must be strictly above both the prior floor and the target
code. Every worker at the target version is then structurally unable to claim.
An unknown-version worker also fails closed.

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
leave it fenced by the version floor and, where present, its tombstone until it is
updated and attested. Do not remove an offline fence to inflate capacity.

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

When `pause_owned=true`, restore the exact prior floor metadata and clear only
this operation's pause in one transaction. The full fence/owner predicate makes
the floor lower and unpause one indivisible final gesture:

```sql
BEGIN;
UPDATE budget_state
SET api_paused_at = NULL,
    api_paused_reason = NULL,
    min_worker_version = :prior_floor,
    min_worker_version_stamped_by = :prior_floor_stamped_by,
    min_worker_version_stamped_at = :prior_floor_stamped_at
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
floor, attested process set, and expected fingerprint. The foreign owner must
explicitly accept the fence handoff. The accepting owner then performs this
floor-only restore:

```sql
BEGIN;
UPDATE budget_state
SET min_worker_version = :prior_floor,
    min_worker_version_stamped_by = :prior_floor_stamped_by,
    min_worker_version_stamped_at = :prior_floor_stamped_at
WHERE id = 1
  AND api_paused_at IS NOT DISTINCT FROM :foreign_pause_at
  AND api_paused_reason IS NOT DISTINCT FROM :foreign_pause_reason
  AND min_worker_version = :temporary_floor
  AND min_worker_version_stamped_by = 'operator-auth-rotation';
-- The accepting foreign owner requires exactly one affected row.
COMMIT;
```

That handoff transaction deliberately leaves the foreign pause untouched. Only
after either final transaction commits may claiming resume. Re-enable any
maintenance-disabled supervisor policy only after its worker is already running
and attested.

## Rollback

Rollback stays behind the same temporary version floor and zero-task drain. Use
another newly generated strong token, or roll back to old code while keeping the
new strong token. A rollback must never restore `123`, never add it beside a
strong token, and never bypass startup validation.

The operator again owns the head restart, which remains
`WORKER_CONCURRENCY=0`. Workers restart and publish the rollback token-set
fingerprint while still fenced. Preserve the six stored Vertex objects, Host-59
assignment, plain API-key posture, offline fences, and foreign pause ownership.
Use the same final owner-scoped reopen/handoff rules; never lower the all-claim
fence merely because the API pause remains set.
