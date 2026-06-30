# Service-Account Key Web Upload & Worker Auto-Distribution — Design

**Goal:** Replace the manual "copy a GCP service-account `.json` to each worker PC and hand-edit its `.env`" step with a web flow: upload SA keys to the head, assign a key to one or many workers, and have each worker pull its assigned key and apply it **live** (no restart) — picking up new api-job capability within ≤30s.

**Status:** Design approved in shape by the gatekeeper (A over B/C, hostname-keyed assignment, reuse the auth-gated pull). This spec folds in the three must-fix gaps as first-class, separately-tested concerns, states the lower-severity items, and adds a scrub/revoke path. Ready for `writing-plans`.

---

## Approach & key decisions

**Chosen shape — pull on the existing heartbeat (Approach A).** Workers already run a 30s registry heartbeat (`worker.py:627`) and already read server-side signals off their own registry state (that is how `drain` works). We add an assignment lookup keyed by **hostname** and let the worker pull + apply its key over the **existing auth-gated file-pull pattern** (same shape as `GET /books/{id}/source.pdf`, R13). No new channel.

**Rejected:**
- **B — dedicated key-poll loop:** a second timer for a ≤30s-latency need; more code, no benefit over piggybacking the heartbeat.
- **C — server→worker push (SSE/websocket):** instant, but there is no existing server→worker push channel; a large new surface for a need the 30s heartbeat already covers.

**Load-bearing facts, each verified against current code:**
1. **Capabilities are frozen at import.** `CAPABILITIES` (`worker.py:94`) and `CAPABILITY_BLOB` (`worker.py:99`) are computed **once** from `os.environ` at module load. The claim gate reads `CAPABILITIES` (`worker.py:200,345`); the heartbeat publishes `CAPABILITY_BLOB` (`worker.py:627`). ⇒ Mutating `os.environ` live does **nothing** to these — a keyless worker that gets a key stays `can_gemini_api=False` forever unless we recompute and reassign both globals.
2. **Auth env is read fresh per spawn.** `_auth_env` is called with `{**os.environ, …}` on **every** spawn (`agent.py:511`); its Vertex branch **raises loudly** if `GOOGLE_APPLICATION_CREDENTIALS` **or** `GOOGLE_CLOUD_PROJECT` is missing (`agent.py:312–315`). ⇒ A torn/half-applied swap mid-spawn either mis-bills the wrong project or hard-fails a concurrent job. The swap must be atomic and happen at a between-claim boundary.
3. **Worker identity is `hostname:pid`.** `self.id = f"{hostname}:{pid}"` (`worker.py:105`); the registry row is per-pid and pruned after offline. ⇒ Assignment must key on bare `socket.gethostname()` to survive restarts; the assignment lookup is a **separate query** from the per-pid registry row.
4. **Creds never live in `settings`.** `_auth_env`/`api_transport` read them straight from `os.environ`; `.env` is loaded once via `load_dotenv(override=False)` (`config.py:14`). ⇒ Live apply = mutate `os.environ` (takes effect next spawn) + persist to `.env` (durable across restart).
5. **Router auth accepts `?token=`.** `books`/`workers` routers are gated at include level (`api/v1/__init__.py:11,18`) but `get_current_user` accepts both Bearer header and `?token=` query param (`auth.py:24–37`). ⇒ A credential download over `?token=` would leak the token into logs/proxy history; the download route needs a **header-only** dependency.
6. **`var/` and `.env` are gitignored** (`.gitignore:22`, `.gitignore:4`); `settings.var_dir` is the configurable base (`storage.py`). ⇒ Worker writes land under `var/` and never get committed.

---

## Architecture

Three units, each independently testable:

- **Key pool (head):** `sa_keys` table + upload/list/delete/download endpoints + `storage.sa_key_path(id)`. Stores the JSON bytes on disk, exposes only metadata over the API, serves raw bytes only over a header-only auth gate.
- **Assignment (head):** `sa_key_assignments` table (hostname → key_id) + assign/unassign/scrub endpoints + a `hostname` grouping on the worker-list view so the Fleet UI can render host → key.
- **Apply path (worker):** a single `apply_sa_key(env, key_bytes, project_id)` routine called at startup-before-claim and on heartbeat-when-changed, plus the capability-recompute and atomic-swap guarantees.

---

## Data model

**`sa_keys`** (new table; migration `0041_sa_keys`):
- `id` UUID PK
- `original_filename` text
- `project_id` text NOT NULL — extracted from the JSON
- `client_email` text NOT NULL — extracted from the JSON
- `sha256` text NOT NULL UNIQUE — dedup; re-upload of identical bytes returns the existing row
- `byte_size` int NOT NULL
- `label` text NULL — optional operator nickname
- `created_at` timestamptz NOT NULL default now()
- The `private_key` is **never** stored in a column and **never** returned by a list/metadata endpoint — only the raw file on disk, served by the download endpoint.

**`sa_key_assignments`** (new table; same migration):
- `hostname` text PK — the stable identity (`socket.gethostname()`)
- `key_id` UUID NOT NULL FK → `sa_keys(id)`
- `updated_at` timestamptz NOT NULL default now()
- Many hostnames may reference one `key_id` (shared-key case); one row per hostname (per-worker case). Both modes use this one table.
- `ON DELETE RESTRICT` from `sa_keys` — a key still assigned cannot be deleted (force unassign/scrub first).

On-disk: `storage.sa_key_path(id)` → `<var_dir>/sa_keys/<id>.json` (honors `VAR_DIR`, mirrors `book_pdf_path`).

---

## API surface (head)

Mounted under a new `sa_keys` router, included with the standard `dependencies=[Depends(get_current_user)]`, **except** the download route which uses a header-only dependency.

- `POST /api/v1/sa-keys` — multipart upload (mirrors `upload_book`). Reads the file, **validates** it parses as JSON with `type == "service_account"` and non-empty `project_id` + `client_email` + `private_key` (reject 422 otherwise), extracts `project_id`/`client_email`, dedups by sha256, writes bytes via `storage.sa_key_path`, inserts the row. Returns metadata (no `private_key`).
- `GET /api/v1/sa-keys` — list pool metadata + per-key assigned-worker count. Never includes `private_key`.
- `DELETE /api/v1/sa-keys/{id}` — delete a key; 409 if still assigned to any hostname.
- `GET /api/v1/sa-keys/{id}/download` — raw JSON bytes for the worker. **Header-only auth** (new `get_current_user_strict` that drops the `?token=` query path) and **hard-refuses (503) when auth is disabled** (`valid_auth_tokens()` empty) — a key vault must never be served wide-open. Bytes only, `Content-Type: application/json`.
- `PUT /api/v1/workers/{hostname}/sa-key` `{ "key_id": "<uuid>" }` — upsert assignment for a hostname.
- `DELETE /api/v1/workers/{hostname}/sa-key` — **non-destructive unassign** (default): head stops advertising a key; the worker keeps whatever it last applied.
- `POST /api/v1/workers/{hostname}/sa-key/scrub` — **revoke**: head signals the worker to clear `active.json`, unset both env vars, recompute capabilities (drops `can_gemini_api`), and remove the assignment row. The "this key leaked, stop using it" affordance.

The worker-list view (`GET /api/v1/workers`) gains a `hostname` field and the resolved `assigned_key_id` / `key_project_id` per host, so the UI can show host → online pids → assigned key → applied key.

---

## Worker apply path (the critical section)

A new module `app/services/sa_key_apply.py` with a pure-ish `apply_sa_key(*, base_env, key_bytes, project_id, var_dir, env_path) -> dict` plus the capability re-bind. Required guarantees, each a first-class task with its own test:

**T-CAP — recompute capabilities on apply (must-fix #1).** After applying (or scrubbing) a key, the worker re-runs `_compute_capabilities(os.environ)` and `_capability_blob(os.environ)` and **reassigns the module globals** `CAPABILITIES` and `CAPABILITY_BLOB`, so the next claim attempt (`worker.py:200,345`) and the next heartbeat (`worker.py:627`) reflect the new key. **Test:** boot a worker with no gemini creds → `CAPABILITIES["can_gemini_api"] is False`; apply a key → assert it flips to `True` and the published blob updates. (This is the gap that makes the headline use case — "assign a key to an idle worker, it starts claiming" — actually work.)

**T-ATOMIC — atomic swap at a between-claim boundary (must-fix #2).** (a) Write JSON to a temp file in the same dir + `os.replace` onto `active.json` (atomic rename). (b) Set `GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_CLOUD_PROJECT` in `os.environ` **together**, never one without the other. (c) Perform the swap **only at the worker's between-claim point** — never while any of the N concurrent spawns is assembling `child_env` (`agent.py:511`). Startup-before-claim covers cold start; the live swap during a running campaign needs the same guarantee. **Test:** assert `active.json` is never observed half-written (temp+rename), and that `GOOGLE_CLOUD_PROJECT` is never set while `GOOGLE_APPLICATION_CREDENTIALS` is absent (and vice versa) across an apply.

**T-AUTH — header-only download + refuse-when-open (must-fix #3).** `get_current_user_strict` rejects `?token=` and requires the Bearer header; the `/sa-keys/{id}/download` route uses it; the endpoint returns 503 when `valid_auth_tokens()` is empty. **Test:** `?token=valid` → 401 on the download route (but still 200 on a normal endpoint, proving the strict variant is scoped); missing header → 401; auth-disabled → 503.

**Change detection:** the worker tracks the sha256 of the key it currently has applied. On heartbeat it compares the head-advertised `key_sha256` against the applied one; only a mismatch triggers a pull+apply. No-op when unchanged (no disk churn, no env thrash).

**`.env` persistence:** after a successful live apply, upsert exactly `GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_CLOUD_PROJECT` into the worker's `.env`, **UTF-8, line-preserving** — rewrite only those two keys, leave every other line byte-for-byte (the worker `.env` now carries Cyrillic `ru:` NOTION keys; an ASCII rewrite would corrupt them). **Test:** upsert preserves a pre-existing non-ASCII line verbatim. Persistence is for restart durability; correctness on a running worker comes from the live `os.environ` mutation + startup-before-claim re-pull, not from `.env`.

---

## Spec-must-state items (lower severity, written in)

- **Honor `VAR_DIR`.** Worker writes go to `storage.sa_key_active_path()` → `<var_dir>/sa_keys/active.json`, never a hardcoded `<repo>/var/...`. R13 fleets point `VAR_DIR` at a shared volume. Lands under `var/` so gitignore (`.gitignore:22`) holds.
- **Duplicate-hostname case.** Cloned VMs or two worker processes on one host share one assignment row and therefore the same `active.json`/`.env`. Fine for shared-key; surprising for per-worker. Documented, not engineered around (the fleet is one-worker-per-PC).
- **`~/.gemini/settings.json` `selectedType` is orthogonal.** This feature manages the **env-var / Vertex (SDK)** credential used by `transport=api`. The CLI gemini path (extract/TOC) still honors `selectedType`, which silently overrides env auth. The existing worker-startup warning stays; CLI auth hygiene remains a separate operator concern. The spec does **not** claim this fixes CLI auth.
- **Blast radius (security posture).** Unlike book PDFs, SA keys are live GCP credentials: a central `sa_keys` table + `/download` behind one bearer means a single leaked token can exfiltrate the **entire pool** in one GET. Mitigations baked in: header-only download (no `?token=` log leak), refuse-when-auth-disabled, and **ship is gated on a real `AUTH_TOKEN`** (the dev `AUTH_TOKEN=123` is acceptable for PDFs, not for a key vault). Encryption-at-rest is explicitly **out of scope** for v1 (operator accepted plaintext-on-disk, consistent with `.env`), but the elevated blast radius is stated so the choice is deliberate.

---

## Frontend (Fleet UI)

A "Keys" panel (FE acceptance = `tsc --noEmit` + `npm run build`; no JS test runner):
- Drag-drop upload of `.json`; on success show `project_id`, `client_email`, and per-key worker-count.
- Worker list grouped by hostname with a per-host key dropdown (assign), an unassign action, and a scrub/revoke action.
- Reflect "assigned key" vs "applied key" so an operator can see propagation land.

---

## Testing summary

- **Backend (pytest, scratch-DB for the new tables):** SA-key JSON validation (reject non-SA / missing fields); upload + sha256 dedup; assignment repo (hostname scoping, shared-key many→one, RESTRICT on delete-while-assigned); **T-CAP** capability recompute on apply; **T-ATOMIC** atomic temp+rename and paired env set; **T-AUTH** header-only + refuse-when-open; `.env` upsert preserves non-ASCII lines; change-detection no-op when sha unchanged; scrub clears env + capabilities.
- **FE:** `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`.
- **Acceptance (generation-affecting):** a real worker, booted keyless, that begins claiming gemini api jobs after a key is assigned — without a restart. This is the one proof that ties T-CAP + T-ATOMIC + the heartbeat pull together.

---

## Out of scope / YAGNI (v1)

- Encryption at rest / a KMS — operator accepted plaintext-on-disk; stated, not built.
- Auto round-robin / least-loaded auto-distribution — assignment is explicit per host (the pool+auto option was declined).
- Per-key `GOOGLE_CLOUD_LOCATION` override — keep the `"global"` default; add later if a key needs a region.
- Rotating/expiring keys, audit log of who-assigned-what — future (`fleet-api-2`).

## Resolved defaults

1. **Auto-extract `project_id` + `client_email`** from the uploaded JSON — approved (removes the operator's most likely error).
2. **Non-destructive unassign** as the default, **plus** an explicit `scrub`/revoke endpoint — approved.
