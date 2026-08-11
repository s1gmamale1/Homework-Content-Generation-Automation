# Operator-token hard-cut rotation

Use this runbook to replace the historical weak operator token with one strong
shared token on the head and every worker. The rollout is deliberately
head-first and takes place while generation is globally paused and drained.
It does not delete, rotate, assign, unassign, or scrub any Vertex credential.

Automation may prepare files and report readiness. **DO NOT restart or kill the
head from automation.** The user/operator owns the head process and performs
that restart explicitly.

## 1. Establish pause ownership

Read the existing singleton before changing anything:

```sql
SELECT api_paused_at, api_paused_reason
FROM budget_state
WHERE id = 1;
```

Record either `pause_owned=true` or `pause_owned=false` in the rollout notes.

- If the row is unpaused, acquire this operation's pause with the scoped write
  below. A successful acquisition means `pause_owned=true`.
- If any foreign reason is already present—including
  `manual-blocker-remediation`—do not replace it. Record
  `pause_owned=false`, require that foreign pause to remain continuously set,
  and continue only after the drain check reaches zero.
- If the reason is already `operator-auth-rotation`, first prove this run owns
  that prior acquisition. Otherwise treat it as foreign.

```sql
UPDATE budget_state
SET api_paused_at = now(), api_paused_reason = 'operator-auth-rotation'
WHERE id = 1
  AND (api_paused_at IS NULL OR api_paused_reason = 'operator-auth-rotation');
```

Re-read the singleton and stop if it does not match the expected ownership.

## 2. Drain without cancelling work

Do not cancel jobs. Wait until the scoped/live running count is zero and
re-check that the pause has not disappeared or changed owner:

```sql
SELECT count(*) AS running_jobs
FROM homework_jobs
WHERE status = 'running';
```

The required value is `0`. A pause alone is not proof of a drain.

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

## 4. Snapshot the vault without disclosing it

Run this on the head under the same account and `VAR_DIR` used by the service.
It prints only a UUID filename and SHA-256 digest; it never prints JSON:

```bash
umask 077
uv run python - <<'PY' > operator-auth-vault-sha256.before
import hashlib
import re

from app.services import storage

uuid_file = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$"
)
vault = storage.sa_key_dir()
for path in sorted(vault.iterdir(), key=lambda value: value.name):
    if path.name.endswith(".delete-quarantine"):
        raise SystemExit("STOP: unresolved delete-quarantine requires investigation")
    if uuid_file.fullmatch(path.name):
        print(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
PY
```

Require six digest rows and zero unresolved delete-quarantine files. If a
quarantine exists, stop. Do not rename or delete it by hand. Classify its exact
UUID/SHA against the read-only DB snapshot; let the newly deployed head perform
startup reconciliation only after the state is understood.

## 5. Generate one strong token privately

Create a protected temporary file without putting the token in terminal output,
shell history, logs, tickets, or chat:

```bash
umask 077
TOKEN_FILE="$(mktemp)"
uv run python - <<'PY' > "$TOKEN_FILE"
import secrets
print(secrets.token_urlsafe(48))
PY
test "$(wc -l < "$TOKEN_FILE")" -eq 1
```

Keep that file only until staging and verification are complete. Do not run
shell tracing (`set -x`) during this procedure.

## 6. Stage the same value everywhere

Using the existing protected deployment channel, replace `AUTH_TOKEN` in the
head and every worker `.env` with the **same strong token** read from
`$TOKEN_FILE`. Do not print it. Set:

```dotenv
ALLOW_INSECURE_LOCAL_AUTH=false
```

Never stage `AUTH_TOKEN=123,<new>` (or `<new>,123`). Startup rejects every
weak member, and retaining the old value defeats the hard cut. Do not change
`GEMINI_API_KEY`, either `GOOGLE_*` variable, `active.json`, SA assignment rows,
or the six stored Vertex objects.

Stage the new code on the head and all workers while their old processes remain
paused. A staged file is not proof that a running process loaded it.

## 7. Operator restarts the head

**DO NOT restart or kill the head from automation.** After all files and code
are staged, the operator restarts the head. Do not restart workers yet.

This creates an unavoidable, safe mismatch window: old workers still hold
`123` in memory, so the new head returns 401 to them. Because claims are paused
and the running count is zero, no homework is lost or newly claimed. The DB
assignments, worker `active.json` files, plain API keys, and stored Vertex files
remain untouched.

Wait for the head health check. Verify that startup raised the automatic
`version floor` to the deployed code version before proceeding. If head startup
fails, use the rollback section; do not restore `123`.

## 8. Perform rolling worker restarts

Begin **rolling worker restarts** only after head health and the version fence
are confirmed. Restart one process group at a time, then wait for its healthy
heartbeat before continuing. Do not infer process state from `.env` contents.

Attest **every online model-calling process**, not merely each hostname:

- deployed code SHA and reported code version;
- token fingerprint (for example, a one-way SHA-256 prefix), never the token;
- expected worker and agent concurrency;
- expected provider capabilities and credential identity;
- fresh healthy heartbeat and eligibility above the version floor.

If a PC runs two model-calling processes, attest both. An offline host is not
rollout-complete: leave it fenced by the version floor and, where present, its
tombstone until it is updated and attested. Do not remove an offline fence to
inflate capacity.

## 9. Post-rotation verification

Perform every check before considering an unpause:

1. Re-run the vault SHA-256 command into
   `operator-auth-vault-sha256.after` and compare it byte-for-byte with the
   before file. Require six UUID files and zero unresolved delete-quarantine
   files.
2. Re-run the two read-only DB queries. Require six DB rows and the exact saved
   Host-59 `key_id` with `scrub_requested_at IS NULL`.
3. Verify the vault directory/file POSIX modes or Windows protected DACLs using
   the deployed vault inspection tooling. Do not expose file contents.
4. Verify every online process's code SHA, token fingerprint, concurrency,
   capabilities, version eligibility, and fresh heartbeat. Confirm the plain
   `GEMINI_API_KEY` posture is unchanged wherever it was already present.
5. Exercise the auth matrix without printing the token: missing and invalid
   credentials return 401; a valid header returns 200; `?token=` is rejected
   with 401 on every `/api/v1/sa-keys*` route; a valid header plus any query
   token is also rejected there. General SSE/source-download query auth remains
   available only on its intended non-vault routes.
6. Confirm `running_jobs=0` and that the original pause owner is still present.

Remove the protected token temp only after every intended process is attested:

```bash
rm -f -- "$TOKEN_FILE"
unset TOKEN_FILE
```

## 10. Release only an owned pause

If and only if this run recorded `pause_owned=true`, release with the
owner-scoped predicate:

```sql
UPDATE budget_state
SET api_paused_at = NULL, api_paused_reason = NULL
WHERE id = 1 AND api_paused_reason = 'operator-auth-rotation';
```

Require one affected row. If `pause_owned=false`, emit no clearing SQL at all.
The foreign owner retains the pause and decides when the broader remediation is
complete.

## Rollback

Auth rollback is another hard cut under the same pause and drain procedure.
Use another newly generated strong token, or roll back to old code while keeping
the new strong token. A rollback must never restore `123`, never add it beside
a strong one, and never bypass startup validation.

The operator again owns the head restart. Roll workers only after the rolled-back
head is healthy and its version fence is verified. Preserve the six stored Vertex
objects, Host-59 assignment, plain API-key posture, offline fences, and foreign
pause ownership throughout.
