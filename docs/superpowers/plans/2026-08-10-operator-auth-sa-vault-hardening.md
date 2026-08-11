# Operator Auth and SA-Key Vault Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the guessable/open operator-auth states and make every service-account key file private, atomic, and hostile-path-safe without deleting or reassigning any Vertex credential.

**Architecture:** A pure operator-auth policy validates process configuration at the first executable line of both server lifecycles, while request dependencies continue to support query auth only for the non-vault surfaces that require it. The complete `/sa-keys` router receives one strict header-only dependency. A focused `sa_key_vault` service owns all SA-key filesystem access and is called by upload, download, local pull, active-key replacement, deletion, scrub, head startup, and standalone-worker startup. Cross-resource deletion is an explicit same-vault quarantine protocol: the verified UUID file is renamed before the locked DB mutation; every COMMIT exception retains that exact quarantine without request-time guessing; head startup resolves only exact recognized quarantines from settled DB state before enforcing the final inventory.

**Tech Stack:** Python 3.14, FastAPI dependencies/lifespan, Pydantic settings, POSIX modes and fsync, Windows security descriptors/write-through replace via conditional `pywin32`, pytest/pytest-asyncio/httpx, PostgreSQL scratch integration tests.

## Global Constraints

- **Plan-only branch gate:** planned from `origin/Nggaev-v2@d6b1c9f65e13ea5a6c2abd21b8a592303ece784b` in `/Users/macmini5/Documents/HCGA-operator-auth-hardening` on `plan/operator-auth-sa-vault-hardening`. The mandatory scan found no equivalent open PR. The gate was repeated read-only at `c9cc63e`: base remained `d6b1c9f`, the owned worktree was clean, and open PRs #108/#117/#118 still had no auth/vault overlap. PRs #108/#117/#118 must not be modified. #117/#118 partially overlap only the finish-doc paths (`CLAUDE.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md`, `docs/HOW_IT_WORKS.md`, and memory tails): if either merges first, rebase this lane and preserve both meanings; if this lane merges first, their owners must rebase. Never resolve that overlap by editing their branches. Historical model/structured/lease branches were already merged; re-run the gate before implementation and before PR.
- **Integration order:** this lane is independent of solver/source-integrity behavior. It must merge and deploy before generation is unpaused and before any 4→40 fleet soak. If `origin/Nggaev-v2` moves, rebase first; `main.py`, `app/services/worker.py`, and `app/config.py` must be composed against the new tip rather than copied from this plan's anchor.
- **Credential preservation:** no migration and no deployment-time mutation of production `sa_keys` or `sa_key_assignments`. Preserve all six stored Vertex key objects byte-for-byte and preserve Host-59's current non-scrubbed assignment. Do not assign, unassign, scrub, relabel, rotate, or delete a Vertex key in this lane.
- **Operator-token policy:** `AUTH_TOKEN` has no default. A normal head or standalone worker refuses startup when it is unset, empty, contains the old `123`, contains any other weak member, or mixes a weak member with a strong one. Every configured member must be strong.
- **Explicit local development:** `ALLOW_INSECURE_LOCAL_AUTH=true` permits only the exact empty-token state for local development. It never makes `/sa-keys` accessible without a header. It never legalizes `123` or any other weak configured token.
- **Strength contract:** each comma-delimited operator token is parsed without trimming, at least 32 characters, contains only the ASCII URL-safe alphabet `[A-Za-z0-9_-]`, has at least eight distinct characters, and is not in the case-insensitive deny-list `{123, password, changeme, change-me, secret, admin, test, dev, development}`. Leading/trailing whitespace, Unicode, controls, commas/empty segments, and duplicates are invalid. Presented header/query values are also exact (never stripped); malformed/non-ASCII input is an ordinary authentication miss, never a `TypeError`/500. The runbook generates tokens with `secrets.token_urlsafe(48)`; the structural checks are a misconfiguration floor, not a claim that arbitrary human text has measurable entropy.
- **Comparison and disclosure:** request matching UTF-8-encodes both sides and calls `hmac.compare_digest` for every candidate even after a match; malformed presented values return false. Exceptions, HTTP details, and logs identify only the failing rule/member index; they never include a configured/presented token or any service-account JSON bytes.
- **Vault contract:** `<VAR_DIR>/sa_keys` is `0700` on POSIX and grants full control only to the current process-token SID on Windows. Every existing/new/stale-temp/delete-quarantine/UUID/`active.json` regular file is `0600` on POSIX and has the same protected, exactly-one-ACE Windows DACL. POSIX operations remain anchored to one verified open vault-directory fd for their whole check/use sequence. Windows ACL application and inspection use `SetSecurityInfo`/`GetSecurityInfo` on the already-open `CreateFileW(..., FILE_FLAG_OPEN_REPARSE_POINT)` handle; no authorization, ACL write, ACL verification, credential read/hash, temp write/flush/rehash, or quarantine verification re-resolves the child by path. The Windows wrapper has explicit operation profiles: every handle includes `READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES`, read/hash adds `GENERIC_READ`, temp write/flush/rehash adds `GENERIC_WRITE | GENERIC_READ`, and any future handle-based delete/rename adds `DELETE`; held handles use compatible `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE` where publication/quarantine needs rename/delete. An access-denied result fails closed and never triggers a path-based reopen. After the protected DACL is installed, path publication additionally re-verifies the held vault identity immediately before/after `MoveFileExW`. The Windows contract rejects pre-existing/replaced reparse/hardlink objects and other identities; it does not claim to defend against an administrator or malicious process already running as the same SID (that principal can read/change the credential regardless). Because Task Scheduler launches the worker under that process identity, the scheduled-task account remains readable/writable; a worker launched under a different account fails closed rather than widening the ACL. Symlinks, Windows reparse points, hardlinks, directories, FIFOs, sockets, and devices in the vault fail closed.
- **Durability:** writes first open/create and harden the vault themselves, then use a same-directory exclusive `0600` temp, file flush+fsync, pre-publication regular/single-link verification, atomic replacement, destination permission verification, and vault-directory-fd fsync on POSIX (Windows uses write-through replacement). A failed replacement leaves the old destination unchanged and cleans the new temp when the process is still alive.
- **DB/filesystem consistency:** a filesystem and PostgreSQL commit cannot be one physical transaction. Upload dedup is database-atomic and filesystem refusal attempts rollback before any successful publication claim. Any upload COMMIT exception keeps the exact canonical bytes: eventual commit is coherent, while eventual rollback leaves an orphan UUID file that head startup inventory rejects for manual resolution. Delete takes a key-row lock, rechecks assignments, atomically renames only the exact hash-matching UUID file to a private same-vault quarantine, then mutates/commits the DB. Commit success removes it; every COMMIT exception retains it. Head startup alone restores an exact quarantine when the row/SHA exists or discards it when the row is absent, then fails closed on any unresolved quarantine or missing, mismatched, or orphan UUID key file. Assignment locks the key and verifies canonical bytes against the row SHA before touching host state. This is an honest fail-closed + startup-reconciliation contract, not a false cross-resource atomicity claim.
- **Startup order:** auth validation, then vault hardening, occur before prompts, DB sessions/reconciliation, version-floor stamping, LISTEN, worker construction, heartbeat, or claim activity. Security validation remains out of module import/app construction so tests and tooling can import safely; the head's pre-existing import-time logging configuration is not falsely claimed to be behind the lifespan gate.
- **Test-safe startup:** tests opt into anonymous local mode explicitly in `tests/conftest.py`; there is no `PYTEST_CURRENT_TEST`/environment-name bypass in production code. Tests exercising rejection turn the opt-in off and prove all startup side-effect seams remain untouched.
- **No paid acceptance:** this lane changes security/startup/filesystem behavior, not generation. Acceptance is unit + real POSIX permission checks + scratch-Postgres API tests + full suite. No model/API call, production DB write, live fleet mutation, or real SA-byte upload is part of implementation acceptance.
- **Deployment ownership:** automation may prepare code and `.env` values on workers, but it must not kill or restart the user-owned head process. The operator performs the head restart and authorizes worker restarts under the global pause.

## Approach & key decisions

1. **Chosen: process-start validation plus route-scoped vault auth.** A global header-only change would break SSE and source-PDF clients that intentionally use the general auth contract. Strictness belongs on the credential-vault router, while startup validation removes the open/guessable production states. Header and general-query values are still exact and untrimmed.
2. **Chosen: hard rotation from `123`.** The new startup validator rejects *every* weak member, so `AUTH_TOKEN=123,<strong>` cannot be used as a bridge. Under a global pause the head switches first, then workers; old in-memory workers temporarily cannot authenticate to the new head until restarted. That mismatch is deliberate and safe because claiming is paused and active Vertex files/DB assignments remain intact.
3. **Chosen: one SA-specific vault service and explicit delete quarantine.** Scattered `chmod` calls do not close direct-write, torn-write, symlink-follow, special-file, stale-temp, read, delete, or Windows ACL gaps. All file operations route through one module. API delete stages an exact file in the same vault and commits under the key-row/assignment guard. Successful commit discards it; any COMMIT exception leaves it untouched for head-startup reconciliation after DB state settles. No request-time fresh read guesses transaction outcome.
4. **Rejected: deleting/re-uploading the six keys.** The filesystem hardener changes metadata only, proves byte hashes unchanged, and never touches the assignment tables. Host-59 remains assigned to its current Vertex object.
5. **Rejected: validating at module import.** Import-time refusal would make unit tests, Alembic tooling, and read-only inspection depend on production secrets. Validation belongs at executable startup before side effects.
6. **Rejected: silently repairing symlinks/nonregular entries.** A path substitution can point outside the vault; startup and I/O raise a generic vault error so the operator resolves the hazard explicitly.

## File map

- Create `app/services/operator_auth.py` — pure token parsing, strength validation, startup-mode decision, and constant-time matching.
- Modify `app/config.py` — empty `AUTH_TOKEN` default and explicit `ALLOW_INSECURE_LOCAL_AUTH=false` setting.
- Modify `app/auth.py` — fail-closed general empty-token behavior, local-dev opt-in, constant-time matching, and strict query-token rejection.
- Modify `app/api/v1/__init__.py` — apply strict auth to every SA-key route.
- Modify `app/api/v1/sa_keys.py` — remove the redundant download-only dependency and route all file I/O through the vault.
- Modify `app/repositories/sa_keys.py` — add race-safe `ON CONFLICT` upload ownership, key-row locking for upload/assign/delete, and inventory reads without changing unrelated call sites.
- Create `app/services/sa_key_vault.py` — permissions/handle ACLs, startup hardening, safe read/remove, atomic crash-safe writes, and exact delete quarantine/restore/discard/reconciliation.
- Modify `pyproject.toml` and `uv.lock` — add conditional Windows-only `pywin32` for exact DACL replacement and inspection.
- Create `.github/workflows/sa-vault-permissions.yml` — mandatory Windows security-descriptor acceptance for pull requests touching the vault/security startup.
- Modify `app/services/sa_key_apply.py` — active/local-pull reads and writes delegate to the vault.
- Modify `app/services/worker.py` and `main.py` — first-line executable security preflight and secure scrub removal.
- Modify `tests/conftest.py` plus focused auth/SA tests — explicit test local-dev mode and regression coverage.
- Create `docs/runbooks/operator-token-rotation.md`; modify `.env.example`, `README.md`, `CLAUDE.md`, `docs/DEPLOY.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md`, and `docs/fleet/worker-pc-setup.md` — exact production behavior and hard-cut rollout.

---

### Task 1: Pure operator-auth policy and explicit local-dev mode

**Files:**
- Create: `app/services/operator_auth.py`
- Modify: `app/config.py`
- Modify: `app/auth.py`
- Modify: `tests/conftest.py`
- Create: `tests/services/test_operator_auth.py`
- Modify: `tests/test_auth_strict.py`

**Interfaces:**
- Produces: `OperatorAuthConfigurationError`, `parse_strong_tokens(raw: str) -> tuple[str, ...]`, `require_startup_auth(raw: str, *, allow_insecure_local: bool) -> Literal["token", "local-dev"]`, and `constant_time_token_match(provided: str, candidates: Iterable[str]) -> bool`.
- Produces settings: `settings.auth_token` default `""`; `settings.allow_insecure_local_auth` default `False` (`ALLOW_INSECURE_LOCAL_AUTH`).
- Consumed by: Task 2 request dependencies and Task 5 process startup.

- [ ] **Step 1: Write the policy RED tests**

Create `tests/services/test_operator_auth.py` with fixed fake values, never an operational token:

```python
import pytest

from app.services import operator_auth


STRONG_A = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"
STRONG_B = "mD8vQ2kL7xN4pR1sT6wY9cA3fH5jU0zE-BgC"


@pytest.mark.parametrize(
    "raw",
    ["123", "password", "short-token", "a" * 32, " ",
     f" {STRONG_A}", f"{STRONG_A} ",
     "has whitespace " + "x" * 32,
     f"{STRONG_A},123", f"{STRONG_A},", f"{STRONG_A},{STRONG_A}"],
)
def test_startup_rejects_every_weak_or_ambiguous_member(raw):
    with pytest.raises(operator_auth.OperatorAuthConfigurationError) as caught:
        operator_auth.require_startup_auth(raw, allow_insecure_local=False)
    assert raw not in str(caught.value)


def test_explicit_local_dev_accepts_only_an_empty_token():
    assert operator_auth.require_startup_auth(
        "", allow_insecure_local=True
    ) == "local-dev"
    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        operator_auth.require_startup_auth("123", allow_insecure_local=True)


def test_multiple_strong_tokens_are_valid_for_future_strong_to_strong_rotation():
    assert operator_auth.parse_strong_tokens(f"{STRONG_A},{STRONG_B}") == (
        STRONG_A, STRONG_B
    )


def test_token_match_uses_every_candidate_without_plain_membership(monkeypatch):
    calls = []
    real = operator_auth.hmac.compare_digest

    def tracked(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(operator_auth.hmac, "compare_digest", tracked)
    assert operator_auth.constant_time_token_match(STRONG_B, (STRONG_A, STRONG_B))
    assert calls == [
        (STRONG_B.encode(), STRONG_A.encode()),
        (STRONG_B.encode(), STRONG_B.encode()),
    ]
```

Extend `tests/test_auth_strict.py` to prove: no token + opt-in false gives 503; no token + opt-in true gives anonymous on `get_current_user`; strict auth still gives 503 under local-dev opt-in; configured token matching is constant-time; diagnostics never contain presented/configured values.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/services/test_operator_auth.py tests/test_auth_strict.py -q
```

Expected: FAIL because the policy module and setting do not exist and empty auth currently opens unconditionally.

- [ ] **Step 3: Implement the pure policy**

Create `app/services/operator_auth.py` with these load-bearing rules:

```python
from __future__ import annotations

import hmac
import string
import unicodedata
from collections.abc import Iterable
from typing import Literal


MIN_TOKEN_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 8
_TOKEN_ALPHABET = frozenset(string.ascii_letters + string.digits + "_-")
_DENYLIST = frozenset(
    {"123", "password", "changeme", "change-me", "secret",
     "admin", "test", "dev", "development"}
)


class OperatorAuthConfigurationError(RuntimeError):
    """Operator auth cannot safely start; never carries token material."""


def parse_strong_tokens(raw: str) -> tuple[str, ...]:
    if raw == "":
        return ()
    parts = raw.split(",")
    parsed: list[str] = []
    for index, part in enumerate(parts, start=1):
        token = part.strip()
        invalid = (
            not token
            or token != part
            or len(token) < MIN_TOKEN_LENGTH
            or not token.isascii()
            or any(character not in _TOKEN_ALPHABET for character in token)
            or any(
                character.isspace()
                or unicodedata.category(character).startswith("C")
                for character in token
            )
            or len(set(token)) < MIN_DISTINCT_CHARACTERS
            or token.casefold() in _DENYLIST
        )
        if invalid:
            raise OperatorAuthConfigurationError(
                f"AUTH_TOKEN member {index} is structurally weak or malformed"
            )
        if token in parsed:
            raise OperatorAuthConfigurationError(
                f"AUTH_TOKEN member {index} duplicates an earlier member"
            )
        parsed.append(token)
    return tuple(parsed)


def require_startup_auth(
    raw: str, *, allow_insecure_local: bool
) -> Literal["token", "local-dev"]:
    tokens = parse_strong_tokens(raw)
    if tokens:
        return "token"
    if allow_insecure_local:
        return "local-dev"
    raise OperatorAuthConfigurationError(
        "AUTH_TOKEN is required unless ALLOW_INSECURE_LOCAL_AUTH=true"
    )


def constant_time_token_match(
    provided: str, candidates: Iterable[str]
) -> bool:
    # Bytes avoid compare_digest(str, str)'s non-ASCII TypeError. Startup
    # policy makes configured values ASCII; UTF-8 still makes malformed
    # presented/candidate values a safe non-match in direct request tests.
    provided_bytes = provided.encode("utf-8")
    matched = False
    for candidate in candidates:
        matched = hmac.compare_digest(
            provided_bytes, candidate.encode("utf-8")
        ) or matched
    return matched
```

In `app/config.py`, set `auth_token: str = ""` and add `allow_insecure_local_auth: bool = False`. Keep `valid_auth_tokens()` as the request-time parser of already-started configuration so legacy unit tests can inject short fake tokens; startup strength is enforced only by `require_startup_auth`.

In `app/auth.py`, use `settings.allow_insecure_local_auth` for the anonymous branch; otherwise empty auth returns 503. Replace `provided not in valid` with `constant_time_token_match(provided, sorted(valid))`. Strict auth never consults the local-dev switch. Both dependencies use `_bearer_value` for headers and the general dependency uses `_query_value` below; neither path calls `.strip()`:

```python
def _presented_value(value: str | None) -> str | None:
    if not value or any(character.isspace() for character in value):
        return None
    return value


def _query_value(token: str | None) -> str | None:
    return _presented_value(token)
```

Add RED cases proving a 32-character Cyrillic configured token is rejected at startup, a Cyrillic presented token returns 401 rather than 500, header/query values with leading/trailing whitespace never authenticate, and a tracked `compare_digest` receives one `(bytes, bytes)` call for every candidate even when the first matches.

In `tests/conftest.py`, add the explicit, test-only opt-in before importing app code:

```python
os.environ.setdefault("AUTH_TOKEN", "")
os.environ.setdefault("ALLOW_INSECURE_LOCAL_AUTH", "true")
```

Do not add a `PYTEST_CURRENT_TEST`, hostname, debug, or environment-name bypass to application code.

- [ ] **Step 4: Run GREEN and existing auth/viewer tests**

```bash
uv run pytest tests/services/test_operator_auth.py tests/test_auth_strict.py \
  tests/api/test_viewer_auth.py tests/api/test_viewer_app.py -q
```

Expected: PASS. Viewer-token separation stays unchanged; `AUTH_TOKEN` strength is a process-start rule, not a new viewer-token policy.

- [ ] **Step 5: Mutation-proof the weak+strong rule**

Temporarily change `require_startup_auth` to accept when *any* member is strong. Re-run `test_startup_rejects_every_weak_or_ambiguous_member`; the `STRONG_A,123` case must fail. Revert the mutation and rerun GREEN.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/services/operator_auth.py app/config.py app/auth.py \
  tests/conftest.py tests/services/test_operator_auth.py tests/test_auth_strict.py
git commit -m "fix(auth): fail closed on weak operator tokens"
```

### Task 2: Make the complete SA-key router header-only and fail-closed

**Files:**
- Modify: `app/auth.py`
- Modify: `app/api/v1/__init__.py`
- Modify: `app/api/v1/sa_keys.py`
- Create: `tests/api/test_sa_keys_auth_surface.py`
- Modify: `tests/api/test_sa_keys_download.py`

**Interfaces:**
- Consumes: `constant_time_token_match` and `valid_auth_tokens` from Task 1.
- Produces: `get_current_user_strict(authorization, token)` as the single dependency on every `/api/v1/sa-keys*` route.
- Preserves: general `get_current_user` header/query behavior for non-vault routes, including SSE and source-PDF consumers.

- [ ] **Step 1: Write route-enumeration and request RED tests**

Create `tests/api/test_sa_keys_auth_surface.py`. Enumerate the actual `api_v1_router` routes whose path starts `/api/v1/sa-keys`; assert every route's dependency tree contains `get_current_user_strict` and does not contain `get_current_user`. Parameterize these concrete requests:

```python
CASES = [
    ("POST", "/api/v1/sa-keys", {"files": {"file": ("k.json", b"{}")}}),
    ("GET", "/api/v1/sa-keys", {}),
    ("DELETE", "/api/v1/sa-keys/00000000-0000-0000-0000-000000000001", {}),
    ("PATCH", "/api/v1/sa-keys/00000000-0000-0000-0000-000000000001",
     {"json": {"max_concurrent_calls": 1}}),
    ("GET", "/api/v1/sa-keys/00000000-0000-0000-0000-000000000001/download", {}),
    ("GET", "/api/v1/sa-keys/assignments", {}),
    ("PUT", "/api/v1/sa-keys/assignments/Host-01",
     {"json": {"key_id": "00000000-0000-0000-0000-000000000001"}}),
    ("DELETE", "/api/v1/sa-keys/assignments/Host-01", {}),
    ("POST", "/api/v1/sa-keys/assignments/Host-01/scrub", {}),
]
```

For every case prove: valid `?token=` alone returns 401; valid header plus any `?token=` also returns 401; missing header returns 401; empty configured tokens return 503 even with `ALLOW_INSECURE_LOCAL_AUTH=true`. Override `get_session` with a dependency that raises `AssertionError` and prove it is never entered on rejection.

Update `tests/api/test_sa_keys_download.py`: the normal `/sa-keys?token=` request must now be 401, not 200. Retain one non-SA endpoint assertion proving general query auth remains available.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/api/test_sa_keys_auth_surface.py \
  tests/api/test_sa_keys_download.py -q
```

Expected: route enumeration and all non-download query-token cases fail because the parent router currently installs permissive `get_current_user`.

- [ ] **Step 3: Install the strict dependency once**

Use `Annotated` so direct unit calls receive `None`, not a FastAPI `Query` object:

```python
from typing import Annotated, Optional
from fastapi import Header, Query

async def get_current_user_strict(
    authorization: Annotated[Optional[str], Header()] = None,
    token: Annotated[Optional[str], Query(include_in_schema=False)] = None,
) -> dict:
    valid = valid_auth_tokens()
    if not valid:
        raise HTTPException(status_code=503, detail="SA-key vault auth is unavailable")
    if token is not None:
        raise HTTPException(status_code=401, detail="query auth is not accepted")
    provided = _bearer_value(authorization)
    if not provided or not constant_time_token_match(provided, sorted(valid)):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    return {"user_id": "authenticated", "auth": "token"}
```

Factor `_bearer_value` so both auth dependencies use the same exact Bearer parsing. It must reject non-Bearer schemes, empty remainders, and remainders containing whitespace.

Use this exact helper rather than `split(...).strip()`, which would silently accept
leading/trailing whitespace in the presented credential:

```python
def _bearer_value(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not value:
        return None
    return _presented_value(value)
```

For the general dependency only, use `_query_value(token)` when no header is
present. This deliberately preserves query authentication for SSE/source downloads
while making it exact: `?token=%20<valid>` and `<valid>%20` are authentication misses,
not silently trimmed successes. Add those cases to `tests/test_auth_strict.py`.

In `app/api/v1/__init__.py`:

```python
from app.auth import get_current_user, get_current_user_strict

api_v1_router.include_router(
    sa_keys.router, dependencies=[Depends(get_current_user_strict)]
)
```

Remove the download endpoint's duplicate `_user=Depends(get_current_user_strict)` and its now-unused auth import. Do not add strict auth globally.

- [ ] **Step 4: Run GREEN**

```bash
uv run pytest tests/api/test_sa_keys_auth_surface.py \
  tests/api/test_sa_keys_download.py tests/test_auth_strict.py -q
```

Expected: PASS; rejected requests never enter a DB dependency.

- [ ] **Step 5: Mutation-proof the router scope**

Temporarily restore `Depends(get_current_user)` on only the SA router. The route-enumeration test and at least eight query-auth cases must fail. Revert and rerun GREEN.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/auth.py app/api/v1/__init__.py app/api/v1/sa_keys.py \
  tests/api/test_sa_keys_auth_surface.py tests/api/test_sa_keys_download.py
git commit -m "fix(sa-keys): require header auth on every vault route"
```

### Task 3: Build private, atomic, hostile-path-safe vault primitives

**Files:**
- Create: `app/services/sa_key_vault.py`
- Create: `tests/services/test_sa_key_vault.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `.github/workflows/sa-vault-permissions.yml`

**Interfaces:**
- Produces: `SAKeyVaultError`; frozen `DeleteQuarantine(key_id: UUID, sha256: str, original_name: str, quarantine_name: str)`; `harden_vault() -> None`; `atomic_write(path: Path, body: bytes) -> None`; `read_bytes(path: Path) -> bytes`; `remove(path: Path, *, missing_ok: bool = False) -> None`; `quarantine_for_delete(path: Path, *, expected_sha256: str) -> DeleteQuarantine`; `restore_quarantined_delete(ticket: DeleteQuarantine) -> None`; `discard_quarantined_delete(ticket: DeleteQuarantine) -> None`; `reconcile_delete_quarantines(expected_sha256: Mapping[str, str]) -> None`; and `verify_uuid_inventory(expected_sha256: Mapping[str, str]) -> None`.
- These functions accept only direct children of `storage.sa_key_dir()`; caller-supplied arbitrary paths are rejected.
- Consumed by: API/worker I/O in Task 4 and startup in Task 5.

- [ ] **Step 1: Write real permission and preservation RED tests**

Create `tests/services/test_sa_key_vault.py`. On POSIX, create six UUID JSON files, `active.json`, and one stale temp with distinct byte payloads; force the directory/files to `0777`/`0666`; hash all bytes; run `harden_vault`; assert hashes unchanged, directory mode exactly `0o700`, and every file mode exactly `0o600`.

Add these independent tests:

```python
@pytest.mark.skipif(os.name == "nt", reason="numeric modes are POSIX")
def test_harden_preserves_six_keys_and_active_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    vault = storage.sa_key_dir()
    vault.mkdir(mode=0o777)
    paths = [storage.sa_key_path(uuid4()) for _ in range(6)]
    paths += [storage.sa_key_active_path(), vault / ".active.json.crash.tmp"]
    for index, path in enumerate(paths):
        path.write_bytes(f"private-{index}".encode())
        path.chmod(0o666)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    sa_key_vault.harden_vault()
    assert stat.S_IMODE(vault.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    assert before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def test_atomic_failure_keeps_old_destination_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    dest = storage.sa_key_active_path()
    sa_key_vault.atomic_write(dest, b"old")
    monkeypatch.setattr(sa_key_vault, "_replace_write_through",
                        lambda source, target: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(dest, b"new")
    assert dest.read_bytes() == b"old"
    assert list(dest.parent.glob("*.tmp")) == []
```

Also prove: every public function creates/hardens a missing vault before touching a credential; temp is `0600` before replacement; successful replacement fsyncs file and the held directory fd; symlink vault/destination and FIFO destination are rejected without reading/writing their target; hardlink count >1 is rejected both before and after temp publication; a destination outside the vault is rejected; and a path swap after validation cannot redirect POSIX read/write/remove/quarantine/restore/discard because the operation stays anchored to the original directory fd. `quarantine_for_delete` accepts only a UUID filename whose bytes match `expected_sha256`, uses a fresh exact grammar `.<uuid>.json.<sha256>.<nonce>.delete-quarantine`, renames within the held vault, and fsyncs the directory. Restore/discard accept only the frozen ticket, re-open and re-hash that exact private file, reject a wrong UUID/SHA/name/link/type, never replace a different canonical file, and fsync after rename/unlink. `verify_uuid_inventory` must accept the six matching UUID files plus `active.json`/ordinary stale write temps only after there are zero delete quarantines; it fails generically for one missing UUID file, one mismatched SHA, one UUID file absent from the expected map, any unresolved delete quarantine, or an unsafe entry.

On a real Windows runner (not an argv mock), create a temp vault under that runner's
actual process identity (the identical code derives the real scheduled-task SID in
production), seed a directory/file with inherited and
explicit grants for the well-known Everyone SID, harden it, and inspect the resulting security
descriptor. Assert: protected DACL; `GetAceCount() == 1`; that sole ACE is allow; ACE SID equals the current
process-token SID; full-control mask; directory ACE carries object+container inheritance;
file ACE carries neither; the current process can reopen/read/write both; the second SID
has no ACE. On that same real runner, create a file symlink, directory junction/reparse
point, and hardlink and prove harden/read/write/remove all refuse without touching targets.
Drive the actual production functions—not a raw `open()` substitute—through every access
profile: `read_bytes` returns exact bytes through its held READ handle;
`atomic_write` creates the temp through its held READ_WRITE handle, calls the real
`FlushFileBuffers`, rewinds/rehashes through that same handle, publishes, and survives a
fresh production `read_bytes`; `quarantine_for_delete` hashes through its held READ handle
and the production restore/discard paths complete without a path-based data reopen. Spy on
the `CreateFileW` wrapper and assert each operation requested its exact profile plus the
common ACL/attribute rights and compatible share flags. An injected `ERROR_ACCESS_DENIED`
must raise `SAKeyVaultError`; patch `Path.open`, built-in `open`, and path-based byte helpers
to raise so no fallback can bypass a deficient held handle.
Add an exact handle-anchoring race: open a permissive regular child through the production
`CreateFileW` wrapper, pause at a test seam after handle/type/identity validation but before
ACL application, rename that child and put a distinct permissive replacement at the original
name, then resume. Inspect with `GetSecurityInfo` on the still-held original handle and prove
that object alone received the protected one-ACE DACL; inspect the replacement's separate
handle and prove it did not. The same-process swap is only a deterministic simulation of a
different identity that had inherited access before hardening, not an expansion of the stated
same-SID threat boundary. Replacing handle calls with `SetFileSecurity`/`GetFileSecurity`
must turn this test RED.
This test is Windows-only and is mandatory in the implementation PR's Windows
job. POSIX CI cannot substitute an argv-shape mock for that acceptance.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/services/test_sa_key_vault.py -q
```

Expected: FAIL because `sa_key_vault` does not exist.

- [ ] **Step 3: Implement the vault module**

Create `app/services/sa_key_vault.py` with these exact boundaries:

```python
from __future__ import annotations

import ctypes
import enum
import os
import stat
from pathlib import Path
from uuid import uuid4

from app.services import storage

if os.name == "nt":  # pragma: no cover - imported only on the Windows CI leg
    import win32api
    import win32con
    import win32file
    import win32security


_IS_WINDOWS = os.name == "nt"


class SAKeyVaultError(RuntimeError):
    """A vault path or operation is unsafe; never includes file contents."""


def _assert_direct_child(path: Path) -> tuple[Path, str]:
    vault = storage.sa_key_dir()
    if path.parent != vault or path.name in {"", ".", ".."}:
        raise SAKeyVaultError("SA-key path is outside the vault")
    return vault, path.name


def _reject_unsafe_stat(info: os.stat_result, *, directory: bool) -> None:
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if reparse or stat.S_ISLNK(info.st_mode) or not expected:
        raise SAKeyVaultError("SA-key vault contains an unsafe path type")
    if not directory and info.st_nlink != 1:
        raise SAKeyVaultError("SA-key vault file has multiple hard links")
```

Call `_reject_unsafe_stat` only on `fstat`/handle-derived metadata. A preliminary
`lstat` may be used for a clearer refusal, but it is never the authorization for a
later path-based open.

Add the dependency with a platform marker so Linux/macOS environments do not install it:

```bash
uv add "pywin32>=311; sys_platform == 'win32'"
```

Implement POSIX permissions with `chmod(0o700/0o600,
follow_symlinks=False)`. On Windows, do **not** use `icacls /grant:r`: Microsoft
documents that it replaces grants for only the named SID and therefore can leave other
explicit ACEs behind. Build a fresh DACL through `win32security` instead:

```python
def _windows_process_sid():
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    sid, _attributes = win32security.GetTokenInformation(
        token, win32security.TokenUser
    )
    return sid


def _set_private_windows_dacl(handle, *, directory: bool) -> None:
    sid = _windows_process_sid()
    inheritance = 0
    if directory:
        inheritance = (
            win32security.OBJECT_INHERIT_ACE
            | win32security.CONTAINER_INHERIT_ACE
        )
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        inheritance,
        win32con.FILE_ALL_ACCESS,
        sid,
    )
    win32security.SetSecurityInfo(
        handle,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )
```

The `handle` above is the already-open `CreateFileW`/PyHANDLE object whose reparse tag,
regular/directory type, link count, volume serial, and file index were validated; do not
close and reopen by name between validation, ACL application, and verification. Immediately
call `_verify_private_windows_dacl(handle, directory=...)`. It retrieves the descriptor
with `win32security.GetSecurityInfo(handle, win32security.SE_FILE_OBJECT,
win32security.DACL_SECURITY_INFORMATION)`, requires `SE_DACL_PROTECTED`, one and only one
`ACCESS_ALLOWED_ACE_TYPE`, `EqualSid(ace_sid, _windows_process_sid())`, a
`FILE_ALL_ACCESS` mask, and the exact inheritance flags above. Any mismatch raises a
generic `SAKeyVaultError`. The production check and Windows acceptance test inspect the
actual security descriptor on the same held object, not command text or a re-resolved path.
`SetFileSecurity` and `GetFileSecurity` are forbidden in the module and caught by the
security scan. This also proves the scheduled-task
identity retains access, because the SID is taken from the running worker process token.

Create `.github/workflows/sa-vault-permissions.yml` so the real Windows assertion is
not optional or hidden behind an environment flag:

```yaml
name: SA vault permissions

on:
  pull_request:
    branches: [Nggaev-v2]
    paths:
      - "app/services/sa_key_vault.py"
      - "app/services/sa_key_apply.py"
      - "app/services/worker.py"
      - "app/api/v1/sa_keys.py"
      - "main.py"
      - "pyproject.toml"
      - "uv.lock"
      - "tests/services/test_sa_key_vault.py"
      - ".github/workflows/sa-vault-permissions.yml"
  workflow_dispatch:

jobs:
  windows-security-descriptor:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.13"
          enable-cache: true
      - run: uv sync --extra dev
      - run: uv run pytest tests/services/test_sa_key_vault.py -q
```

The PR gate must show `windows-security-descriptor` green. If repository branch
protection cannot mark it required, the independent reviewer treats a missing,
cancelled, or skipped run as a blocker and does not approve/merge.

Implement `harden_vault()` in this order: create with `mode=0o700`; open the directory without following links/reparse points; verify the opened object; apply and verify directory privacy through that same fd/handle; iterate every direct entry through the held directory handle/fd; reject nonregular/reparse/symlink/hardlink entries; apply and verify file privacy through each same verified fd/handle. Do not delete stale write temps or delete quarantines and do not inspect/log file bytes except through one-way SHA-256 comparisons required by the quarantine/inventory contracts.

On POSIX introduce a context manager `_open_posix_vault_fd()` that opens
`storage.sa_key_dir()` once with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`, verifies
`fstat` is a directory, and holds that fd until the operation completes. All child
operations use names plus that `dir_fd`: `os.open(..., dir_fd=vault_fd)`,
`os.unlink(..., dir_fd=vault_fd)`, and
`os.replace(..., src_dir_fd=vault_fd, dst_dir_fd=vault_fd)`. Use `fstat`/`fchmod`
on opened files, never `Path.chmod` after a separate `lstat`. Fsync the same held
directory fd after publication/removal. A rename of the pathname while an operation
is in flight therefore cannot redirect credential bytes.

On Windows, wrap `CreateFileW` with an explicit operation profile rather than one
metadata-only mask:

```python
class _WindowsHandleUse(enum.Enum):
    ACL_ONLY = "acl-only"
    READ_OR_HASH = "read-or-hash"
    TEMP_READ_WRITE = "temp-read-write"
    HANDLE_DELETE_OR_RENAME = "handle-delete-or-rename"


_WINDOWS_COMMON_ACCESS = (
    win32con.READ_CONTROL
    | win32con.WRITE_DAC
    | win32con.FILE_READ_ATTRIBUTES
)
_WINDOWS_SHARE_ALL = (
    win32con.FILE_SHARE_READ
    | win32con.FILE_SHARE_WRITE
    | win32con.FILE_SHARE_DELETE
)


def _windows_desired_access(use: _WindowsHandleUse) -> int:
    access = _WINDOWS_COMMON_ACCESS
    if use is _WindowsHandleUse.READ_OR_HASH:
        access |= win32con.GENERIC_READ
    elif use is _WindowsHandleUse.TEMP_READ_WRITE:
        # GENERIC_READ is deliberate: the still-held temp is rewound and
        # rehashed after the real write+FlushFileBuffers, before publish.
        access |= win32con.GENERIC_READ | win32con.GENERIC_WRITE
    elif use is _WindowsHandleUse.HANDLE_DELETE_OR_RENAME:
        access |= win32con.DELETE
    return access
```

`_open_windows_handle(path, *, use, directory, disposition)` calls `CreateFileW` with
that exact desired-access value, `FILE_FLAG_OPEN_REPARSE_POINT` (plus
`FILE_FLAG_BACKUP_SEMANTICS` for a directory), `_WINDOWS_SHARE_ALL` whenever a held
handle must coexist with atomic publication/quarantine, and `OPEN_EXISTING` versus
`CREATE_NEW` explicitly. It rejects `INVALID_HANDLE_VALUE`, then inspects handle
metadata/reparse tags and link count before access. ACL hardening may use `ACL_ONLY`;
`read_bytes`, UUID/quarantine hashing, and inventory hashing must use `READ_OR_HASH`;
the exclusive temp must use `TEMP_READ_WRITE`; if an implementation changes the
path-based `MoveFileExW`/delete design to a handle-based operation, that handle must use
`HANDLE_DELETE_OR_RENAME`. Do not request file-data rights for the directory handle.
Do not catch `ERROR_ACCESS_DENIED` and reopen with Python `open`, `Path.open`,
`read_bytes`, `write_bytes`, or any path-resolved fallback: translate it to a generic
`SAKeyVaultError` and stop.

Keep the vault
handle open and record its volume serial + file index; immediately before and after
the path-based `MoveFileExW` publish, reopen the named vault with the same flags and
require the same identity. The protected one-SID DACL prevents other identities from
mutating children. State in the module docstring that an administrator or malicious
process already running as that same SID is outside the boundary; do not call the
Windows path publish handle-relative or adversary-proof. DACL installation/inspection
itself is handle-anchored: pass the original verified handle to
`SetSecurityInfo`/`GetSecurityInfo`; never pass its path to
`SetFileSecurity`/`GetFileSecurity`.

Implement `atomic_write` by first opening/creating and hardening the vault, then creating an `O_CREAT|O_EXCL` same-directory temp at `0o600`, `fchmod(0o600)` on POSIX, writing/flushing/fsyncing, applying the private ACL, and verifying the still-open temp is regular with link count exactly one before `_replace_write_through`. On Windows, create with the `TEMP_READ_WRITE` profile, write all bytes with `win32file.WriteFile` (loop until complete), call the real `win32file.FlushFileBuffers` on that same handle, rewind and SHA-256 re-read it through the same held handle, and require the hash of bytes actually written to match `body` before publication. Verify an existing destination through an opened no-follow/`ACL_ONLY` handle, publish relative to the held POSIX fd (or through the identity-checked Windows path), verify the final object/mode/DACL, and fsync the POSIX vault fd. Clean the temp on a live exception through the held directory handle/fd without following links.

Pin the replacement seam so its write-through behavior is reviewable and testable:

```python
def _replace_write_through(
    source: Path, destination: Path, *, vault_fd: int | None = None
) -> None:
    if not _IS_WINDOWS:
        if vault_fd is None:
            raise SAKeyVaultError("verified vault handle is required")
        os.replace(
            source.name,
            destination.name,
            src_dir_fd=vault_fd,
            dst_dir_fd=vault_fd,
        )
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move_file_ex.restype = ctypes.c_int
    replace_existing = 0x1
    write_through = 0x8
    if not move_file_ex(
        str(source), str(destination), replace_existing | write_through
    ):
        raise OSError(ctypes.get_last_error(), "write-through replacement failed")
```

After `_replace_write_through`, POSIX `atomic_write` fsyncs the already-held vault fd.
The Windows call above owns the write-through guarantee; do not perform a second
non-write-through `os.replace`.

Implement POSIX `read_bytes` with `O_RDONLY|O_NOFOLLOW` plus `dir_fd`, then `fstat` regular/single-link verification before reading. Windows `read_bytes` opens once with `READ_OR_HASH`, validates that held object, and reads to EOF with `win32file.ReadFile` through the same handle. UUID/quarantine/inventory SHA helpers hash chunks from that same production held-handle reader. Implement POSIX `remove` with a verified no-follow child handle and `os.unlink(name, dir_fd=vault_fd)`; Windows uses the verified reparse-point handle and rechecks vault identity around path deletion. Symlinks/nonregular paths raise rather than being silently removed. No Windows read/hash/write/flush path reopens by name after validation or access denial.

Implement delete quarantine as a second atomic-rename primitive, not as a special case of
best-effort `remove`. A `DeleteQuarantine` name encodes its exact UUID and SHA plus a random
nonce; parsers reject every noncanonical name. `quarantine_for_delete` verifies the source
handle and SHA, verifies no destination exists, renames within the same held vault, and fsyncs
the directory/write-through move. `restore_quarantined_delete` re-verifies the ticket/file;
if the canonical destination is absent it renames back, while if the canonical destination
already exists with the same UUID/SHA it discards only the byte-identical quarantine. Any
different/unsafe canonical object leaves the quarantine untouched and raises.
`discard_quarantined_delete` removes only the exact ticket whose private file still has the
expected SHA. Neither helper accepts a bare path or glob.

`reconcile_delete_quarantines(expected_sha256)` enumerates through the held vault, groups exact
quarantine names by UUID, and fails closed on malformed or multiple tickets. For one exact
ticket: DB inventory contains the same UUID/SHA => restore (or remove only an independently
verified byte-identical duplicate); UUID absent from DB inventory => discard; UUID present
with a different SHA or any I/O uncertainty => keep and raise. It touches no ordinary UUID
file except that ticket's exact canonical peer. `verify_uuid_inventory` runs only afterward,
hashes UUID-named regular children through `read_bytes`, compares the exact expected
name-to-SHA map, ignores only `active.json` and recognized ordinary write temps, and rejects
every remaining quarantine or other unexpected entry without deleting evidence.

- [ ] **Step 4: Run GREEN and inspect actual modes**

```bash
uv run pytest tests/services/test_sa_key_vault.py -q
```

Expected on this POSIX head: every numeric mode/preservation/durability/hazard test passes and Windows-only cases skip. On the mandatory real Windows workflow, the same test module must drive production `read_bytes`, `atomic_write` + real flush/rehash, quarantine/restore/discard, DACL swap, junction, symlink, and hardlink behavior; an argv/access-mask mock is not acceptance.

- [ ] **Step 5: Mutation-proof the path and mode guards**

Temporarily replace the POSIX `dir_fd` open/rename with path-based calls: the directory-swap test must fail. Temporarily change Windows ACL apply/inspect to `SetFileSecurity`/`GetFileSecurity`: the real Windows name-swap test must fail. Temporarily remove `GENERIC_READ` from `READ_OR_HASH`, `GENERIC_WRITE` from `TEMP_READ_WRITE`, the real `FlushFileBuffers`, or replace production held-handle reads/writes with a path reopen: the Windows production-operation tests must fail. Temporarily remove the Windows reparse-handle check: the real Windows junction test must fail. Temporarily create temps with `0o666`, omit the pre-publish link-count check, or make quarantine discard accept a bare path/wrong SHA: the mode/hardlink/quarantine tests must fail. Revert all mutations and rerun GREEN.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/services/sa_key_vault.py tests/services/test_sa_key_vault.py \
  pyproject.toml uv.lock .github/workflows/sa-vault-permissions.yml
git commit -m "feat(sa-keys): add private crash-safe vault primitives"
```

### Task 4: Route every SA-key read/write/remove through the vault

**Files:**
- Modify: `app/api/v1/sa_keys.py`
- Modify: `app/repositories/sa_keys.py`
- Modify: `app/services/sa_key_apply.py`
- Modify: `app/services/worker.py`
- Modify: `tests/services/test_sa_key_apply_core.py`
- Modify: `tests/services/test_worker_sa_key_sync.py`
- Modify: `tests/api/test_sa_keys_api.py`
- Modify: `tests/api/test_sa_keys_assign_api.py`
- Modify: `tests/api/test_sa_keys_download.py`
- Create: `tests/integration/test_sa_key_upload_atomicity.py`
- Create: `tests/integration/test_sa_key_delete_atomicity.py`

**Interfaces:**
- Consumes Task 3: `atomic_write`, `read_bytes`, `remove`, and the typed delete-quarantine functions.
- Produces `create_or_get_for_upload(...) -> tuple[SAKey, bool]` (boolean = this transaction inserted the metadata row), `lock_key_for_assignment(...) -> SAKey | None`, `lock_unassigned_key_for_delete(...) -> tuple[SAKey | None, Literal["ready", "not_found", "assigned"]]`, and `uuid_hash_inventory(session) -> dict[str, str]`. Assignment also reads the locked row's canonical vault bytes and requires their SHA to equal `row.sha256` before any host mutation.
- Preserves API response shapes, SHA dedup, assignment locks, and worker capability behavior.
- Does not change repository/table contents except the same explicit API mutations already requested by tests/operators.

- [ ] **Step 1: Update SA API fixtures to authenticate strongly**

In all three real-DB SA API modules define one fake test header and attach it to every `/sa-keys` request:

```python
_TOKEN = "T8r2Vw9_Mp4xC7kN1qZ6sH3dL5yF0aJgB-Ue"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}

# each fixture:
monkeypatch.setattr(config.settings, "auth_token", _TOKEN)
```

Never set `auth_token=""` in an SA API success test after Task 2. Add a helper wrapper if needed so cleanup DELETEs are authenticated too.

- [ ] **Step 2: Write I/O wiring RED tests**

Add tests that monkeypatch direct `Path.read_bytes`, `Path.write_bytes`, and `Path.unlink` to raise if the API/worker paths invoke them. Spy on the Task-3 functions and assert:

- upload calls `atomic_write(path, exact_body)` before `session.commit`;
- download and local-head `pull_key_bytes` call `read_bytes`;
- active-key apply calls `atomic_write`;
- worker scrub calls `remove`; API key deletion calls `quarantine_for_delete` before the DB DELETE/commit and never directly unlinks the canonical UUID file;
- a vault error returns generic HTTP 503 without body/token bytes;
- the existing six-file preservation test remains byte-identical.
- two concurrent identical uploads resolve to one row/file without `IntegrityError`;
- filesystem refusal rolls back newly inserted metadata;
- a forced real-Postgres upload commit failure flushes then raises and returns the generic 503 without `MissingGreenlet`; every COMMIT exception keeps the exact canonical upload bytes because even a normally-returning rollback may be a no-op; eventual commit is immediately coherent, while eventual rollback leaves an orphan that Task 5 inventory rejects for manual resolution;
- existing dedup rows/files are never removed on commit ambiguity, and missing/wrong-hash bytes are repaired only from a validated body whose SHA equals the row SHA;
- assigned delete returns 409 before quarantine; concurrent assign/delete and dedup-upload/delete executions serialize on the key row and finish in one of the two valid whole states, never a dangling assignment, missing file for a live row, or UUID file for an absent row;
- assignment returns generic 503 and performs no host mutation when the locked row's canonical vault object is missing, unsafe, or hashes differently—covering a visible row whose bytes remain quarantined after an ambiguous delete;
- delete failure before DB mutation leaves the row/file unchanged; commit success removes it; every DELETE/commit exception retains the exact quarantine for Task 5 startup reconciliation, regardless of rollback's return or fresh-session visibility;
- real two-session unresolved-transaction tests pause an uncommitted insert/delete after commit fails and rollback either raises or returns as a no-op, prove no fresh-session visibility is treated as authoritative, then drive both eventual outcomes: upload commit leaves row+canonical consistent, upload rollback retains the orphan as fail-closed evidence, delete commit lets startup discard the quarantine, and delete rollback lets startup restore it;
- crash-boundary fixtures stop after quarantine with the row present and after committed DB deletion with the row absent; Task 5 startup reconciliation restores/removes respectively, and a neighboring row/file plus all assignments remain byte/row-identical.

Run:

```bash
uv run pytest tests/services/test_sa_key_apply_core.py \
  tests/services/test_worker_sa_key_sync.py tests/api/test_sa_keys_api.py \
  tests/api/test_sa_keys_assign_api.py tests/api/test_sa_keys_download.py \
  tests/integration/test_sa_key_upload_atomicity.py \
  tests/integration/test_sa_key_delete_atomicity.py -q
```

Expected: new wiring tests fail on the current direct `write_bytes`/`read_bytes`/`unlink` calls.

- [ ] **Step 3: Make upload ownership race-safe and replace every direct operation**

Add a repository helper without changing the existing `create_or_get` signature used by integration fixtures:

```python
async def create_or_get_for_upload(
    session: AsyncSession, **values
) -> tuple[SAKey, bool]:
    inserted_id = await session.scalar(
        pg_insert(SAKey)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["sha256"])
        .returning(SAKey.id)
    )
    if inserted_id is not None:
        return await session.get(SAKey, inserted_id), True
    row = await session.scalar(
        select(SAKey)
        .where(SAKey.sha256 == values["sha256"])
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("SA-key upload conflict did not resolve")
    return row, False
```

PostgreSQL waits for a competing uncommitted unique-key insert before resolving
`ON CONFLICT`, so the SELECT observes the committed owner. The `FOR UPDATE` on the
dedup row serializes repair against deletion. The assign path must use
`lock_key_for_assignment` (`SELECT ... FOR KEY SHARE`) before its existing exclusive
host lock; delete uses `SELECT ... FOR UPDATE`, checks `SAKeyAssignment` while that lock
is held, and issues the DELETE in the same transaction. Thus an assignment that wins is
visible and blocks delete, while a delete that wins makes assignment return 404; neither
path relies on a stale count. Keep the lock order key row before host lock in assignment,
and key row before assignment scan in delete, to avoid introducing an AB-BA cycle.
Add
`uuid_hash_inventory(session)` as one SELECT returning `{f"{id}.json": sha256}`
for Task 5.

In upload, keep validation, use the ownership helper, and write before commit so a
filesystem refusal can roll back the uncommitted row. For an existing row, hash the
current file: matching bytes are a no-op; missing or mismatched bytes are repaired
atomically only after asserting this validated body hashes to `row.sha256`:

```python
row, created = await repo.create_or_get_for_upload(
    session,
    original_filename=file.filename or "key.json",
    project_id=project_id,
    client_email=client_email,
    sha256=sha,
    byte_size=len(body),
)
row_id = row.id
row_sha256 = row.sha256
created_by_this_tx = bool(created)
try:
    sa_key_vault.atomic_write(storage.sa_key_path(row_id), body)
except sa_key_vault.SAKeyVaultError as exc:
    await session.rollback()
    raise HTTPException(503, "SA-key vault is unavailable") from exc
try:
    await session.commit()
except Exception:
    await _best_effort_rollback(session)
    raise HTTPException(503, "SA-key upload did not commit") from None
```

`row_id`, `row_sha256`, and `created_by_this_tx` above are plain immutable values copied
before the first filesystem operation, commit, or rollback. No exception path may read an
ORM attribute after `rollback()`; the real-DB test must retain normal SQLAlchemy rollback
expiration so a regression produces `MissingGreenlet` and turns RED.

`_best_effort_rollback` only attempts to release request locks; its return is never treated as
proof of transaction resolution. A COMMIT exception is always ambiguous. Upload keeps canonical
bytes untouched: eventual commit produces a coherent row/file, while eventual rollback leaves an
orphan UUID object that Task 5 inventory rejects fail-closed for manual resolution. Delete keeps
its already-created exact quarantine so Task 5 can restore/discard it after DB state settles.

Implement API key deletion as this exact state machine; do not reuse the worker scrub's
simple `remove`:

1. `lock_unassigned_key_for_delete` obtains the key-row `FOR UPDATE` lock, returns 404 if
   absent and 409 if any assignment exists, and leaves the eligible row locked.
2. Copy `row_id` and `row_sha256` to plain values. Call
   `quarantine_for_delete(storage.sa_key_path(row_id), expected_sha256=row_sha256)` before
   issuing the repository DELETE. A missing/mismatched/unsafe canonical file is 503 and the
   DB transaction rolls back untouched.
3. Issue `DELETE ... WHERE id=:row_id` under the same lock and require rowcount exactly one.
   Commit. On success call `discard_quarantined_delete(ticket)`; if final discard fails,
   return generic 503 and retain the exact quarantine for startup reconciliation—the DB
   deletion is already authoritative.
4. On any DELETE/commit exception, attempt rollback only to release locks, retain the exact
   quarantine without a fresh lookup, and return generic 503. Task 5 startup alone restores when
   the row/SHA exists or discards when the row is absent after DB state has settled. Never infer
   commit outcome from the exception type or from rollback returning normally.

Upload repair obtains the same key-row `FOR UPDATE` lock before inspecting/writing an existing
dedup row. Assignment obtains `FOR KEY SHARE`, then reads the exact canonical UUID file and checks
its SHA against the locked row before taking the host lock or writing `SAKeyAssignment`; a missing,
unsafe, or mismatched object returns generic 503 with no host mutation.

Use generic 503 handling for download/read/remove hazards. Worker apply/scrub catches
`SAKeyVaultError` separately and logs a fixed message without `logger.exception`, so
an OS exception/path is not emitted through a traceback. Replace direct operations:

```python
# download/local pull
body = sa_key_vault.read_bytes(storage.sa_key_path(key_id))

# active apply
sa_key_vault.atomic_write(dest, key_bytes)

# worker scrub only (API key delete uses quarantine protocol above)
sa_key_vault.remove(path, missing_ok=True)
```

Keep `sa_key_apply.write_active_key` as the public compatibility function, but make it a one-line delegate to `sa_key_vault.atomic_write`. Keep HTTP pull header behavior unchanged. Do not log key bytes, JSON, private-key fields, or tokens.

- [ ] **Step 4: Run GREEN focused tests**

```bash
uv run pytest tests/services/test_sa_key_vault.py \
  tests/services/test_sa_key_apply_core.py tests/services/test_worker_sa_key_sync.py \
  tests/api/test_sa_keys_auth_surface.py tests/api/test_sa_keys_api.py \
  tests/api/test_sa_keys_assign_api.py tests/api/test_sa_keys_download.py \
  tests/integration/test_sa_key_upload_atomicity.py \
  tests/integration/test_sa_key_delete_atomicity.py -q
```

Expected: unit tests pass; real-DB modules skip without `RUN_DB_INTEGRATION=1`.

- [ ] **Step 5: Run authenticated scratch-Postgres API acceptance**

```bash
RUN_DB_INTEGRATION=1 \
DATABASE_URL='postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc' \
uv run pytest tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py tests/integration/test_sa_key_upload_atomicity.py \
  tests/integration/test_sa_key_delete_atomicity.py -q
```

Expected: upload/list/patch/download/assign/scrub/unassign/delete all pass with headers; query-only auth never succeeds; uploaded bytes use private permissions; forced upload rollback proves pinned compensation data; delete/assign/upload races and every delete failure boundary preserve one coherent DB/file state. Scratch fixtures use synthetic JSON only.

- [ ] **Step 6: Commit Task 4**

```bash
git add app/api/v1/sa_keys.py app/repositories/sa_keys.py \
  app/services/sa_key_apply.py app/services/worker.py \
  tests/services/test_sa_key_apply_core.py tests/services/test_worker_sa_key_sync.py \
  tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py tests/integration/test_sa_key_upload_atomicity.py \
  tests/integration/test_sa_key_delete_atomicity.py
git commit -m "fix(sa-keys): route all credential files through vault"
```

### Task 5: Fail startup before side effects and harden both head and worker vaults

**Files:**
- Modify: `main.py`
- Modify: `app/services/worker.py`
- Create: `tests/services/test_operator_security_startup.py`
- Modify: `tests/services/test_worker_startup_applies_key.py`
- Modify: `tests/integration/test_sa_key_delete_atomicity.py`

**Interfaces:**
- Consumes Task 1 `require_startup_auth` and Task 3 `harden_vault`.
- Produces the same ordered synchronous preflight at the first executable line of `main.lifespan` and `worker.run_standalone`.
- Head only: after the filesystem preflight but before job reconciliation/version-floor/LISTEN/worker construction, reads `uuid_hash_inventory(session)`, calls `reconcile_delete_quarantines(expected)`, then `verify_uuid_inventory(expected)`; only exact recognized quarantines are restored/discarded by authoritative DB state, and any unresolved quarantine or missing/mismatched/orphan UUID file aborts startup.
- Does not validate at import and does not alter `viewer_main.py`'s separate dashboard-token startup.

- [ ] **Step 1: Write fail-before-side-effect RED tests**

Create `tests/services/test_operator_security_startup.py`:

```python
@pytest.mark.asyncio
async def test_head_rejects_bad_auth_before_prompts_db_listener_or_worker(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "123")
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    touched = []
    monkeypatch.setattr(main, "load_prompts", lambda: touched.append("prompts"))
    monkeypatch.setattr(main.events_bus, "start_listener",
                        lambda: touched.append("listener"))
    monkeypatch.setattr(main, "build_worker_from_settings",
                        lambda: touched.append("worker"))
    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        async with main.app.router.lifespan_context(main.app):
            pass
    assert touched == []


@pytest.mark.asyncio
async def test_standalone_rejects_before_logging_prompts_worker_or_db(monkeypatch):
    monkeypatch.setattr(settings, "auth_token", "123")
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    touched = []
    monkeypatch.setattr(app_log, "configure",
                        lambda: touched.append("logging"))
    monkeypatch.setattr(prompts, "load_all",
                        lambda: touched.append("prompts"))
    monkeypatch.setattr(worker, "build_worker_from_settings",
                        lambda: touched.append("worker"))
    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        await worker.run_standalone()
    assert touched == []
```

The local imports in `run_standalone` resolve `app.log.configure` and
`app.services.prompts.load_all`, so patch those source modules as above; a test that
spies only `build_worker_from_settings` is insufficient. Also patch `SessionLocal`,
the registry heartbeat seam, and `Worker.run`, proving neither authentication failure
nor vault failure reaches logging, prompts, worker construction, heartbeat, or DB.

Add import-safety tests: with empty token/opt-in false, `import main` and `import app.services.worker` do not raise. Add ordered spies proving valid auth calls `harden_vault` before the first later seam. For head startup, prove the inventory query, quarantine reconciliation, and final inventory check occur in that order after `harden_vault` but before `_reconcile_on_startup`, version-floor stamping, LISTEN, or worker construction. Seed missing/mismatch/orphan cases and assert none of those later seams runs.

Add real scratch-DB + real vault restart tests for every delete crash boundary: (a) exact row/SHA exists and only its one quarantine exists => startup restores the canonical UUID file, removes the quarantine, then inventory passes; (b) row is absent and its exact quarantine exists => startup removes only that quarantine, then inventory passes; (c) same UUID exists with a different SHA, malformed/multiple quarantine names, wrong quarantine bytes, unsafe canonical target, or DB/read uncertainty => startup preserves all evidence and aborts before later seams. Each fixture seeds a neighboring key/file and assignment rows and proves none change. A quarantine for Host-59's key while its assignment exists is never synthesized as a valid delete state: assignment-guard tests in Task 4 must prevent it before the file moves.

Add a local-dev test with empty token + opt-in true that reaches the next mocked seam but strict SA auth remains unavailable. Do not claim head logging is behind the lifecycle preflight: `main.py` intentionally configures logging at import; the security guarantee begins at the first lifespan line and precedes every credential/job/DB-network side effect.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/services/test_operator_security_startup.py \
  tests/services/test_worker_startup_applies_key.py -q
```

Expected: rejection tests fail because both lifecycle functions currently proceed to prompts/DB/worker setup.

- [ ] **Step 3: Add the ordered preflight to both entrypoints**

At the top of `main.lifespan`, before `load_prompts()`:

```python
operator_auth.require_startup_auth(
    settings.auth_token,
    allow_insecure_local=settings.allow_insecure_local_auth,
)
sa_key_vault.harden_vault()
```

Then, in the first `SessionLocal` transaction, before `_reconcile_on_startup`:

```python
expected = await sa_keys_repo.uuid_hash_inventory(session)
sa_key_vault.reconcile_delete_quarantines(expected)
sa_key_vault.verify_uuid_inventory(expected)
await _reconcile_on_startup(session)
```

This head-only reconcile+audit is the crash-recovery boundary for Task 4. It mutates
only exact, hash-verified, canonical delete-quarantine names: same UUID/SHA in DB means
restore (or discard an independently verified duplicate), absent UUID means discard,
and mismatch/uncertainty means keep evidence and refuse service. It then refuses service
when DB metadata, UUID files, or residual quarantines disagree. `active.json` and
recognized same-vault ordinary write temps are not metadata rows; any unexpected UUID
JSON or remaining quarantine is an orphan/hazard and blocks startup. Standalone workers
run `harden_vault` but not the
head inventory comparison because their local vault normally contains only
`active.json`.

At the top of `worker.run_standalone`, before configuring logging/loading prompts/building a worker, add the identical calls. Let failures propagate; do not catch-and-warn. Error strings remain generic and contain no token/path contents.

Do not call either function at module import, `FastAPI(...)` construction, `Worker.__init__`, or viewer startup. `viewer_main.py` retains its independent `DASHBOARD_TOKEN` empty/overlap checks.

- [ ] **Step 4: Run GREEN and ordering regression tests**

```bash
uv run pytest tests/services/test_operator_security_startup.py \
  tests/services/test_worker_startup_applies_key.py \
  tests/services/test_events_bus.py tests/services/test_version_floor_stamp.py -q
```

Expected: PASS; auth/vault preflight precedes DB/listener/worker behavior and existing lifecycle wiring remains present.

- [ ] **Step 5: Mutation-proof test-only semantics**

Temporarily add a `PYTEST_CURRENT_TEST` bypass or move validation after `load_prompts`; the explicit no-bypass/order tests must fail. Revert and rerun GREEN.

- [ ] **Step 6: Commit Task 5**

```bash
git add main.py app/services/worker.py \
  tests/services/test_operator_security_startup.py \
  tests/services/test_worker_startup_applies_key.py \
  tests/integration/test_sa_key_delete_atomicity.py
git commit -m "fix(startup): validate auth and harden key vault first"
```

### Task 6: Fence every claim and rehearse the hard-cut `123` rotation

**Files:**
- Modify: `app/services/operator_auth.py`
- Modify: `app/services/worker.py`
- Modify: `app/services/sa_key_vault.py`
- Modify: `tests/services/test_operator_auth.py`
- Modify: `tests/services/test_worker_version_gate.py`
- Modify: `tests/services/test_worker_capabilities.py`
- Modify: `tests/services/test_worker_capability_rebind.py`
- Modify: `tests/services/test_operator_security_startup.py`
- Modify: `tests/services/test_sa_key_vault.py`
- Create: `docs/runbooks/operator-token-rotation.md`
- Modify: `.env.example`, `README.md`, `CLAUDE.md`, `docs/DEPLOY.md`,
  `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md`, and
  `docs/fleet/worker-pc-setup.md`
- Create: `tests/docs/test_operator_token_runbook.py`

**Interfaces:**
- Produces `runtime_token_set_fingerprint(raw, *, allow_insecure_local) -> str | None`:
  a domain-separated SHA-256 over the sorted validated token set; exact local-dev
  marker; `None` for invalid configuration. It never returns token material.
- Extends every worker `CAPABILITY_BLOB`/heartbeat with
  `auth_token_fingerprint`, refreshed at standalone startup and every live
  capability rebind.
- Produces `sa_key_vault.snapshot_uuid_inventory() -> dict[str, str]`, using the
  same held-fd/held-handle scan/hash path as startup verification and failing on
  unsafe, unknown, or quarantined entries.
- Produces `rotation_version_floors(...) -> (final_floor, temporary_floor)`:
  checked signed-Integer arithmetic; final is at least target/prior, temporary
  strictly dominates target/prior plus every reported effective version and
  configured `WORKER_CODE_VERSION` override.
- Produces an operator-owned runbook. It never restarts/kills the head and never
  mutates credentials during implementation.

- [ ] **Step 1: Write RED tests for runtime evidence and the safe vault snapshot**

Pin the fingerprint to a hand-derived literal, prove multi-token order
independence, raw-token absence, explicit `local-dev`, and invalid→`None`.
Prove startup rebinds it, live rebind refreshes it, and the actual registry
heartbeat carries it. Add a POSIX mutation test that patches `Path.read_bytes`
and `Path.iterdir` to fail while `snapshot_uuid_inventory` still succeeds; add
unsafe-entry/quarantine failures and keep the real Windows handle acceptance.
Pin the concrete prior=953/target=1000 result `(1000,1001)`, a high override
that raises the temporary floor, signed-Integer overflow rejection, and the real
worker claim gate blocking a process that starts at the known override before
attestation.

- [ ] **Step 2: Implement the runtime evidence and checked-fence helpers**

The fingerprint canonical bytes are:

```python
_FINGERPRINT_DOMAIN = b"hcga.operator-auth-token-set.v1\x00"
canonical = b"\x00".join(token.encode("ascii") for token in sorted(tokens))
return "sha256:" + hashlib.sha256(_FINGERPRINT_DOMAIN + canonical).hexdigest()
```

Capability construction may return `None` before executable startup rejects an
invalid config; it must not move validation to module import. Standalone startup
rebinds only after auth + vault hardening. The vault snapshot reuses the exact
verified inventory collector; `verify_uuid_inventory` compares its normalized
DB expectation to that snapshot rather than maintaining a second scan.

`rotation_version_floors` accepts only real non-negative integers in
`0..2_147_483_647`, refuses an unbounded member, and refuses a maximum member
because no representable temporary fence can exceed it. It returns
`final=max(prior or 0,target)` and `temporary=max(final, reported,
overrides)+1`.

- [ ] **Step 3: Write structural runbook RED tests**

Tests must require:

1. snapshot prior pause + floor metadata; acquire the API pause only if unpaused;
2. inventory every online/retained/offline-known process plus each protected
   service's configured `WORKER_CODE_VERSION`; reject unexpected/ahead/unreadable
   overrides; an unreachable unbounded host is an unconditional stop unless an
   independently verified outside-worker park exists (SA tombstone alone is
   insufficient), or exact SHA proves both local gates and its override is
   readable/bounded;
3. calculate checked `final_floor=max(prior or 0,target)` and a temporary floor
   above every prior/target/reported/override version, then install it with
   expected pause/floor predicates and stamped owner;
4. one drain per online process ID, supervisor stop, head embedded-worker drain,
   `WORKER_CONCURRENCY=0`, zero `running|cancelling`, zero credential slots, and
   OS/process-level zero worker tasks (covers post-done Notion work);
5. six-key/Host-59 snapshots and only production vault snapshot APIs—no direct
   path enumeration/reads;
6. private token generation plus the exact expected runtime fingerprint;
7. user-owned head restart, head valid-token 200, then worker restarts while the
   temporary floor remains above target;
8. per-process exact fingerprint/version/concurrency/capability heartbeat;
9. one final transaction that sets the final floor and clears only an owned
   `operator-auth-rotation` pause; an inherited foreign pause keeps the temporary
   floor until its owner explicitly accepts a recorded floor-fence handoff;
10. offline pre-target workers remain stale and retain any independently
   enforced external park after reopen; rollback never restores `123` or selects
   a build lacking Tasks 1–6 hardening.

Extract every SQL clearing block and require the complete owner + temporary-floor
predicate. An appended unscoped clear, API-pause-only fence, hostname-only drain,
direct `Path.read_bytes`/`iterdir`, missing cancelling/slot check, missing runtime
fingerprint, or early floor lower must turn RED.

- [ ] **Step 4: Write the exact all-claim hard-cut runbook**

State why `api_paused_at` is insufficient (CLI claims pass). Inventory all
registry/process versions and every service-file override first; an unreachable
host with no proven bound is an unconditional stop unless durable supervisor,
network/DB, or old-code-proof head-side isolation exists. A worker-local SA
tombstone never qualifies by itself; exact-binary proof additionally requires a
readable/bounded override. Keep any external park after reopen. Keep the checked
temporary floor through drain, head restart, worker restart, and attestation.
Stop/restart
worker processes, not hostnames; require the head at `WORKER_CONCURRENCY=0`.
Use `status IN ('running','cancelling')`, `credential_slots=0`, and runtime task
exit. Do not treat terminal jobs as proof because Notion archival is post-done.
Use `harden_vault` + `snapshot_uuid_inventory`; generate the token into a `0600`
temp; calculate the fingerprint through `runtime_token_set_fingerprint`; prove
the head accepts it and every worker heartbeat publishes it. Preserve the six
Vertex objects, Host-59 assignment, plain-key posture, and offline fences.

For an owned pause, set the stamped final floor (never below the target) and
clear the pause in one expected-state transaction. For a foreign pause, clear
nothing and lower no floor until the foreign owner explicitly accepts the
handoff; its later floor-only transaction predicates on the exact foreign pause
and temporary-floor owner. Default rollback rotates to a sealed strong token on
current hardened code; code fallback is allowed only to a predesignated,
reviewed ref that preserves Tasks 1–6. Automation never restarts/kills the head.

- [ ] **Step 5: De-stale the live docs**

Document strong startup policy, header-only SA routes, fingerprint evidence,
safe vault snapshot, all-claim floor, per-process drain/attestation, foreign
handoff, and exact real startup order:
auth → harden → prompts → DB inventory/reconcile → version stamp → listener →
optional worker. Do not edit historical worklogs/plans.

- [ ] **Step 6: Run focused + canonical GREEN and commit**

```bash
uv run pytest tests/services/test_operator_auth.py \
  tests/services/test_worker_capabilities.py \
  tests/services/test_worker_version_gate.py \
  tests/services/test_worker_capability_rebind.py \
  tests/services/test_operator_security_startup.py \
  tests/services/test_sa_key_vault.py \
  tests/docs/test_operator_token_runbook.py -q
uv run pytest -q
git add app/services/operator_auth.py app/services/worker.py \
  app/services/sa_key_vault.py tests/services/test_operator_auth.py \
  tests/services/test_worker_capabilities.py \
  tests/services/test_worker_version_gate.py \
  tests/services/test_worker_capability_rebind.py \
  tests/services/test_operator_security_startup.py \
  tests/services/test_sa_key_vault.py docs/runbooks/operator-token-rotation.md \
  .env.example README.md CLAUDE.md docs/DEPLOY.md docs/HOW_IT_WORKS.md \
  docs/CODE_MAP.md docs/DATABASE.md docs/fleet/worker-pc-setup.md \
  tests/docs/test_operator_token_runbook.py \
  docs/superpowers/plans/2026-08-10-operator-auth-sa-vault-hardening.md
git commit -m "fix(security): fence operator token rotation"
```

### Task 7: Full acceptance, security scans, and external gate handoff

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md`
- Modify: `docs/memory/INDEX.md`
- Modify: `docs/memory/ROADMAP.md` if this P0 item has a live entry; otherwise do not invent a duplicate
- Move: `docs/superpowers/plans/2026-08-10-operator-auth-sa-vault-hardening.md` → `docs/superpowers/plans/shipped/2026-08-10-operator-auth-sa-vault-hardening.md`

**Interfaces:**
- Produces the final evidence packet and deploy gate. No deployment occurs in this task.

- [ ] **Step 1: Run focused security and lifecycle tests**

```bash
uv run pytest tests/services/test_operator_auth.py tests/test_auth_strict.py \
  tests/api/test_sa_keys_auth_surface.py tests/services/test_sa_key_vault.py \
  tests/services/test_sa_key_apply_core.py tests/services/test_worker_sa_key_sync.py \
  tests/services/test_operator_security_startup.py \
  tests/services/test_worker_startup_applies_key.py \
  tests/docs/test_operator_token_runbook.py -q
```

Expected: all pass, including real POSIX mode assertions. The implementation PR is not gateable until the separate mandatory Windows workflow also passes the production held-handle read/write/flush/hash/quarantine cases and the ACL name-swap/reparse/hardlink cases.

- [ ] **Step 2: Run scratch-Postgres SA API acceptance**

```bash
RUN_DB_INTEGRATION=1 \
DATABASE_URL='postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc' \
uv run pytest tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py tests/integration/test_sa_key_upload_atomicity.py \
  tests/integration/test_sa_key_delete_atomicity.py \
  tests/integration/test_assignment_writer_locks.py -q
```

Expected: authenticated SA CRUD/assignment behavior passes; strict query rejection and assignment locking remain intact; forced upload rollback never dereferences expired ORM state; delete quarantine, assignment/upload races, commit ambiguity, and both startup crash-recovery states pass without touching neighboring keys. No production DB is contacted.

- [ ] **Step 3: Run the canonical suite**

```bash
uv run python -m pytest tests/ -q
```

Expected: green against the post-rebase baseline. No model call occurs.

- [ ] **Step 4: Run security and residue scans**

```bash
rg -n 'auth_token: str = "123"|AUTH_TOKEN.*default.*123' app README.md docs .env.example
rg -n 'include_router\(sa_keys\.router.*get_current_user\)' app
rg -n 'sa_key_path\([^)]*\)\.(read_bytes|write_bytes|unlink)' app
rg -n 'sa_key_active_path\([^)]*\)\.(read_bytes|write_bytes|unlink)' app
rg -n 'SetFileSecurity|GetFileSecurity' app
rg -n 'logger\.(debug|info|warning|error|exception).*\b(body|key_bytes|private_key|auth_token)\b' app
git diff --check origin/Nggaev-v2...
```

Expected: no unsafe default/router/direct-I/O/path-based-Windows-ACL/key-material-log hits; diff check clean. Separately search `AUTH_TOKEN=123` and classify historical shipped-plan/worklog examples rather than rewriting history; all live docs/config/runbooks must be clean.

- [ ] **Step 5: Re-run the mandatory collision gate**

```bash
git fetch --all --prune
git worktree list --porcelain
gh pr list --state open --limit 100 \
  --json number,title,headRefName,baseRefName,author,isDraft,mergeStateStatus
git log HEAD..origin/Nggaev-v2 --oneline
git diff --name-status origin/Nggaev-v2...
```

If the base moved, rebase, resolve composition on `config.py`/`main.py`/`worker.py`, and rerun Steps 1–4. Re-read #117/#118: if either merged, preserve both its structured-output documentation and this lane's security truth; if still open, leave its branch/PR untouched and record that its owner must rebase after this lane. Never edit a project-manager-owned PR/branch.

- [ ] **Step 6: Write finish records and archive the plan**

Reserve the next worklog ID using the repository's counter convention, record
exact test counts and the `$0` acceptance, and state “not deployed; `123`
rotation remains an operator-owned hard cut behind the temporary all-claim
version floor and process drain.” Close only the matching P0 roadmap item. Move
this plan with `git mv` into `plans/shipped/`.

- [ ] **Step 7: Commit the finish**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md \
  docs/memory/ROADMAP.md docs/superpowers/plans/shipped/2026-08-10-operator-auth-sa-vault-hardening.md
git commit -m "docs(memory): record operator auth and SA vault hardening"
```

- [ ] **Step 8: Request independent review; do not deploy or self-merge**

Use `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch`. Open the implementation PR for external gate. The PR body must call out the hard-cut mismatch window, six-key/Host-59 preservation, no migration, no model call, and the fact that the user owns the eventual head restart. Do not alter `.env`, pause production, restart any process, run the rotation, push a token, or merge without the separate operator authorization.

## Deployment and rollback gate (post-merge only)

The executable authority is `docs/runbooks/operator-token-rotation.md`; this
summary must not be used as a shorter substitute.

1. **Own the current state and install an all-claim fence.** Snapshot the exact
   pause and version-floor metadata. Acquire `operator-auth-rotation` only when
   unpaused; otherwise preserve the foreign pause and mark `pause_owned=false`.
   Inventory every registry/process effective version and every service-file
   override. An unreachable unbounded host stops the operation unless durable
   outside-worker isolation is independently proven; worker-local SA tombstones
   alone never qualify. Install a checked temporary floor strictly above every
   proven value using full expected-state predicates.
2. **Drain every model-calling process, not merely jobs or hostnames.** Disable
   supervisor restarts, drain/stop every online worker process (including any
   embedded head worker), stage `WORKER_CONCURRENCY=0` on the head, and require
   zero `running|cancelling` jobs, zero credential slots, and zero OS/runtime
   worker tasks twice. This process-level proof includes post-done Notion work.
3. **Preserve facts through production-safe surfaces.** Record six DB key rows,
   Host-59's exact non-scrubbed assignment, and the six UUID-file digests using
   `harden_vault()` plus `snapshot_uuid_inventory()` only. No credential or
   assignment is changed.
4. **Generate and stage one strong primary token privately.** Calculate its
   domain-separated expected token-set fingerprint. Pull final code and stage
   the same token plus `ALLOW_INSECURE_LOCAL_AUTH=false` everywhere while every
   worker remains stopped. Never print or retain `123` beside it.
5. **The operator restarts the head behind the unchanged temporary floor.** The
   head remains at `WORKER_CONCURRENCY=0`. Prove a real authenticated request
   returns 200, invalid/missing auth returns 401, the vault/DB snapshots are
   unchanged, and no embedded worker task exists. Automation never restarts or
   kills the head.
6. **Restart and attest every worker process behind the same floor.** Require
   each full process-ID heartbeat to publish the exact expected token-set
   fingerprint, target version/SHA, concurrency, and unchanged credential
   posture. Offline hosts remain floor-fenced and retain any independently
   enforced external park.
7. **Reopen only with exact ownership.** When `pause_owned=true`, one
   expected-state transaction sets the stamped final floor to
   `max(prior,target)` and clears only this operation's pause. When a foreign
   pause was inherited, clear nothing and lower no floor until that owner
   explicitly accepts the recorded handoff; its floor-only transition predicates
   on the exact foreign pause and temporary-floor owner. Offline pre-target
   workers remain stale, and externally parked hosts remain isolated.
8. **Rollback remains equally fenced.** Keep the temporary all-claim floor and
   zero-task drain. Default to a sealed strong replacement on current hardened
   code; use a code fallback only when a predesignated reviewed build retains
   Tasks 1–6. Repeat process-level attestations and the same owner-scoped final
   gesture. Never restore `123`, alter Host-59, or delete/re-upload stored
   Vertex keys.

## Plan self-review

- **Spec coverage:** all-SA-route header auth (Task 2); ASCII URL-safe exact tokens, malformed-safe constant-time matching, default/open/weak startup refusal + explicit local mode (Tasks 1/2); POSIX dir-fd anchoring, Windows operation-specific handle rights, production held-handle read/hash/write/flush/rehash, same-handle ACL apply/inspect + reparse checks + exact one-ACE DACL, existing/new/temp/quarantine/active permissions, durability, access-denied fail-closed behavior, and no bytes in diagnostics (Tasks 3/4/7); race-safe PG dedup, pinned upload compensation data, key-row/assignment serialization, delete quarantine with fresh-state ambiguity handling, and fail-closed head quarantine/inventory reconciliation (Tasks 4/5); real head+standalone side-effect ordering (Task 5); six keys/Host-59/plain-key preservation, foreign-pause ownership, version-floor/all-process attestation, offline fencing, user-owned restart, mismatch window, and rollback (Tasks 6/7/deployment gate); #117/#118 doc integration (global/Task 7); no generation/paid action (global constraints/Task 7).
- **Type consistency:** Task 1 exports are consumed under the same names by Tasks 2/5. Task 3's vault functions and frozen `DeleteQuarantine` are consumed under the same names by Tasks 4/5. Task 4's `create_or_get_for_upload -> (SAKey, bool)`, key-lock helpers, and `uuid_hash_inventory -> dict[str, str]` are consumed exactly by upload/assign/delete/head startup. Upload/delete exception paths consume only pinned scalars/frozen tickets after rollback. `ALLOW_INSECURE_LOCAL_AUTH` maps only to `settings.allow_insecure_local_auth`.
- **Placeholder scan:** no TBD/TODO/“similar to”/undefined helper remains. All commands, routes, statuses, token rules, modes, files, and operational order are explicit.
- **Scope check:** no encryption-at-rest, KMS, schema, SA assignment change, token-management UI, general-query-auth removal, live rollout, or model generation is included.
