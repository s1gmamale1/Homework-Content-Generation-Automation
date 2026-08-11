# Operator Auth and SA-Key Vault Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the guessable/open operator-auth states and make every service-account key file private, atomic, and hostile-path-safe without deleting or reassigning any Vertex credential.

**Architecture:** A pure operator-auth policy validates process configuration at the first executable line of both server lifecycles, while request dependencies continue to support query auth only for the non-vault surfaces that require it. The complete `/sa-keys` router receives one strict header-only dependency. A focused `sa_key_vault` service owns all SA-key filesystem access and is called by upload, download, local pull, active-key replacement, deletion, scrub, head startup, and standalone-worker startup.

**Tech Stack:** Python 3.14, FastAPI dependencies/lifespan, Pydantic settings, POSIX modes and fsync, Windows security descriptors/write-through replace via conditional `pywin32`, pytest/pytest-asyncio/httpx, PostgreSQL scratch integration tests.

## Global Constraints

- **Plan-only branch gate:** planned from `origin/Nggaev-v2@d6b1c9f65e13ea5a6c2abd21b8a592303ece784b` in `/Users/macmini5/Documents/HCGA-operator-auth-hardening` on `plan/operator-auth-sa-vault-hardening`. The mandatory scan found no equivalent open PR. PRs #108/#117/#118 are unrelated and must not be modified. Historical model/structured/lease branches were already merged; re-run the gate before implementation and before PR.
- **Integration order:** this lane is independent of solver/source-integrity behavior. It must merge and deploy before generation is unpaused and before any 4→40 fleet soak. If `origin/Nggaev-v2` moves, rebase first; `main.py`, `app/services/worker.py`, and `app/config.py` must be composed against the new tip rather than copied from this plan's anchor.
- **Credential preservation:** no migration and no repository mutation of `sa_keys` or `sa_key_assignments`. Preserve all six stored Vertex key objects byte-for-byte and preserve Host-59's current non-scrubbed assignment. Do not assign, unassign, scrub, relabel, rotate, or delete a Vertex key in this lane.
- **Operator-token policy:** `AUTH_TOKEN` has no default. A normal head or standalone worker refuses startup when it is unset, empty, contains the old `123`, contains any other weak member, or mixes a weak member with a strong one. Every configured member must be strong.
- **Explicit local development:** `ALLOW_INSECURE_LOCAL_AUTH=true` permits only the exact empty-token state for local development. It never makes `/sa-keys` accessible without a header. It never legalizes `123` or any other weak configured token.
- **Strength contract:** each comma-delimited operator token is parsed without trimming, at least 32 characters, contains no whitespace/control/comma, has at least eight distinct characters, and is not in the case-insensitive deny-list `{123, password, changeme, change-me, secret, admin, test, dev, development}`. Leading/trailing whitespace, empty segments, and duplicates are invalid. The runbook generates tokens with `secrets.token_urlsafe(48)`; the structural checks are a misconfiguration floor, not a claim that arbitrary human text has measurable entropy.
- **Comparison and disclosure:** request matching uses `hmac.compare_digest`. Exceptions, HTTP details, and logs identify only the failing rule/member index; they never include a configured/presented token or any service-account JSON bytes.
- **Vault contract:** `<VAR_DIR>/sa_keys` is `0700` on POSIX and grants full control only to the current process-token SID on Windows. Every existing/new/stale-temp/UUID/`active.json` regular file is `0600` on POSIX and has the same protected, one-SID Windows DACL. Because Task Scheduler launches the worker under that process identity, the scheduled-task account remains readable/writable; a worker launched under a different account fails closed rather than widening the ACL. Symlinks, Windows reparse points, hardlinks, directories, FIFOs, sockets, and devices in the vault fail closed.
- **Durability:** writes use a same-directory exclusive `0600` temp, file flush+fsync, atomic replacement, destination permission verification, and parent-directory fsync on POSIX (Windows uses write-through replacement). A failed replacement leaves the old destination unchanged and cleans the new temp when the process is still alive.
- **Startup order:** auth validation, then vault hardening, occur before prompts, DB sessions/reconciliation, version-floor stamping, LISTEN, worker construction, heartbeat, or claim activity. Module import/app construction remains side-effect-free so tests and tooling can import code safely.
- **Test-safe startup:** tests opt into anonymous local mode explicitly in `tests/conftest.py`; there is no `PYTEST_CURRENT_TEST`/environment-name bypass in production code. Tests exercising rejection turn the opt-in off and prove all startup side-effect seams remain untouched.
- **No paid acceptance:** this lane changes security/startup/filesystem behavior, not generation. Acceptance is unit + real POSIX permission checks + scratch-Postgres API tests + full suite. No model/API call, production DB write, live fleet mutation, or real SA-byte upload is part of implementation acceptance.
- **Deployment ownership:** automation may prepare code and `.env` values on workers, but it must not kill or restart the user-owned head process. The operator performs the head restart and authorizes worker restarts under the global pause.

## Approach & key decisions

1. **Chosen: process-start validation plus route-scoped vault auth.** A global header-only change would break SSE and source-PDF clients that intentionally use the general auth contract. Strictness belongs on the credential-vault router, while startup validation removes the open/guessable production states.
2. **Chosen: hard rotation from `123`.** The new startup validator rejects *every* weak member, so `AUTH_TOKEN=123,<strong>` cannot be used as a bridge. Under a global pause the head switches first, then workers; old in-memory workers temporarily cannot authenticate to the new head until restarted. That mismatch is deliberate and safe because claiming is paused and active Vertex files/DB assignments remain intact.
3. **Chosen: one SA-specific vault service.** Scattered `chmod` calls do not close direct-write, torn-write, symlink-follow, special-file, stale-temp, read, delete, or Windows ACL gaps. All file operations route through one module.
4. **Rejected: deleting/re-uploading the six keys.** The filesystem hardener changes metadata only, proves byte hashes unchanged, and never touches the assignment tables. Host-59 remains assigned to its current Vertex object.
5. **Rejected: validating at module import.** Import-time refusal would make unit tests, Alembic tooling, and read-only inspection depend on production secrets. Validation belongs at executable startup before side effects.
6. **Rejected: silently repairing symlinks/nonregular entries.** A path substitution can point outside the vault; startup and I/O raise a generic vault error so the operator resolves the hazard explicitly.

## File map

- Create `app/services/operator_auth.py` — pure token parsing, strength validation, startup-mode decision, and constant-time matching.
- Modify `app/config.py` — empty `AUTH_TOKEN` default and explicit `ALLOW_INSECURE_LOCAL_AUTH=false` setting.
- Modify `app/auth.py` — fail-closed general empty-token behavior, local-dev opt-in, constant-time matching, and strict query-token rejection.
- Modify `app/api/v1/__init__.py` — apply strict auth to every SA-key route.
- Modify `app/api/v1/sa_keys.py` — remove the redundant download-only dependency and route all file I/O through the vault.
- Create `app/services/sa_key_vault.py` — permissions/ACLs, startup hardening, safe read/remove, atomic crash-safe writes.
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
    assert calls == [(STRONG_B, STRONG_A), (STRONG_B, STRONG_B)]
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
import unicodedata
from collections.abc import Iterable
from typing import Literal


MIN_TOKEN_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 8
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
    matched = False
    for candidate in candidates:
        matched = hmac.compare_digest(provided, candidate) or matched
    return matched
```

In `app/config.py`, set `auth_token: str = ""` and add `allow_insecure_local_auth: bool = False`. Keep `valid_auth_tokens()` as the request-time parser of already-started configuration so legacy unit tests can inject short fake tokens; startup strength is enforced only by `require_startup_auth`.

In `app/auth.py`, use `settings.allow_insecure_local_auth` for the anonymous branch; otherwise empty auth returns 503. Replace `provided not in valid` with `constant_time_token_match(provided, sorted(valid))`. Strict auth never consults the local-dev switch.

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
    if any(character.isspace() for character in value):
        return None
    return value
```

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
- Produces: `SAKeyVaultError`, `harden_vault() -> None`, `atomic_write(path: Path, body: bytes) -> None`, `read_bytes(path: Path) -> bytes`, `remove(path: Path, *, missing_ok: bool = False) -> None`.
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

Also prove: temp is `0600` before replacement; successful replacement fsyncs file and directory; symlink vault/destination and FIFO destination are rejected without reading/writing their target; hardlink count >1 is rejected; a destination outside the vault is rejected; read rejects a path swapped to a symlink; and Windows replacement requests write-through.

On a real Windows runner (not an argv mock), create a temp vault under the same
identity used to run the scheduled worker, seed a directory/file with inherited and
explicit grants for the well-known Everyone SID, harden it, and inspect the resulting security
descriptor. Assert: protected DACL; exactly one allow ACE; ACE SID equals the current
process-token SID; full-control mask; directory ACE carries object+container inheritance;
file ACE carries neither; the current process can reopen/read/write both; the second SID
has no ACE. This test is Windows-only and is mandatory in the implementation PR's Windows
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
import os
import stat
from pathlib import Path
from uuid import uuid4

from app.services import storage

if os.name == "nt":  # pragma: no cover - imported only on the Windows CI leg
    import win32api
    import win32con
    import win32security


_IS_WINDOWS = os.name == "nt"


class SAKeyVaultError(RuntimeError):
    """A vault path or operation is unsafe; never includes file contents."""


def _assert_direct_child(path: Path) -> Path:
    vault = storage.sa_key_dir()
    if path.parent != vault:
        raise SAKeyVaultError("SA-key path is outside the vault")
    return vault


def _reject_unsafe_lstat(path: Path, *, directory: bool) -> None:
    info = path.lstat()
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


def _set_private_windows_dacl(path: Path, *, directory: bool) -> None:
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
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.Initialize()
    descriptor.SetSecurityDescriptorDacl(True, dacl, False)
    descriptor.SetSecurityDescriptorControl(
        win32security.SE_DACL_PROTECTED,
        win32security.SE_DACL_PROTECTED,
    )
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )
```

Immediately call `_verify_private_windows_dacl(path, directory=...)`. It retrieves the
descriptor with `GetFileSecurity`, requires `SE_DACL_PROTECTED`, one and only one
`ACCESS_ALLOWED_ACE_TYPE`, `EqualSid(ace_sid, _windows_process_sid())`, a
`FILE_ALL_ACCESS` mask, and the exact inheritance flags above. Any mismatch raises a
generic `SAKeyVaultError`. The production check and Windows acceptance test inspect the
actual security descriptor, not command text. This also proves the scheduled-task
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

Implement `harden_vault()` in this order: create with `mode=0o700`; lstat/reparse/type-check the directory; apply directory privacy; iterate every direct entry; reject nonregular/reparse/symlink/hardlink entries; apply file privacy. Do not delete stale temps and do not inspect/log file bytes.

Implement `atomic_write` with an `O_CREAT|O_EXCL` same-directory temp at `0o600`, `fchmod(0o600)` on POSIX, write/flush/fsync, private ACL application, `_replace_write_through`, final type/mode verification, and POSIX directory fsync. `_replace_write_through` uses `os.replace` + directory fsync on POSIX and `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)` on Windows. Before replacement, lstat an existing destination and reject unsafe types. Clean the temp on a live exception without following links.

Pin the replacement seam so its write-through behavior is reviewable and testable:

```python
def _replace_write_through(source: Path, destination: Path) -> None:
    if not _IS_WINDOWS:
        os.replace(source, destination)
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

After `_replace_write_through`, POSIX `atomic_write` opens the vault directory with
`O_RDONLY | O_DIRECTORY`, fsyncs it, and closes it. The Windows call above owns the
write-through guarantee; do not perform a second non-write-through `os.replace`.

Implement `read_bytes` with `O_RDONLY|O_NOFOLLOW` where available, then `fstat` regular/single-link verification before reading. Implement `remove` with direct-child and lstat validation before `unlink`; symlinks/nonregular paths raise rather than being silently removed.

- [ ] **Step 4: Run GREEN and inspect actual modes**

```bash
uv run pytest tests/services/test_sa_key_vault.py -q
```

Expected on this POSIX head: every numeric mode/preservation/durability/hazard test passes. Windows-specific command tests pass with subprocess/replace seams mocked.

- [ ] **Step 5: Mutation-proof the path and mode guards**

Temporarily remove `O_NOFOLLOW`/the post-open `fstat` check: the swap-to-symlink test must fail. Temporarily create temps with `0o666`: the pre-replace mode test must fail. Revert both and rerun GREEN.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/services/sa_key_vault.py tests/services/test_sa_key_vault.py \
  pyproject.toml uv.lock .github/workflows/sa-vault-permissions.yml
git commit -m "feat(sa-keys): add private crash-safe vault primitives"
```

### Task 4: Route every SA-key read/write/remove through the vault

**Files:**
- Modify: `app/api/v1/sa_keys.py`
- Modify: `app/services/sa_key_apply.py`
- Modify: `app/services/worker.py`
- Modify: `tests/services/test_sa_key_apply_core.py`
- Modify: `tests/services/test_worker_sa_key_sync.py`
- Modify: `tests/api/test_sa_keys_api.py`
- Modify: `tests/api/test_sa_keys_assign_api.py`
- Modify: `tests/api/test_sa_keys_download.py`

**Interfaces:**
- Consumes Task 3: `atomic_write`, `read_bytes`, `remove`.
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
- API delete and worker scrub call `remove`;
- a vault error returns generic HTTP 503 without body/token bytes;
- the existing six-file preservation test remains byte-identical.

Run:

```bash
uv run pytest tests/services/test_sa_key_apply_core.py \
  tests/services/test_worker_sa_key_sync.py tests/api/test_sa_keys_api.py \
  tests/api/test_sa_keys_assign_api.py tests/api/test_sa_keys_download.py -q
```

Expected: new wiring tests fail on the current direct `write_bytes`/`read_bytes`/`unlink` calls.

- [ ] **Step 3: Replace every direct operation**

In upload, keep validation/dedup and move the atomic write before commit so a filesystem refusal rolls back the uncommitted metadata row:

```python
row = await repo.create_or_get(
    session,
    original_filename=file.filename or "key.json",
    project_id=project_id,
    client_email=client_email,
    sha256=sha,
    byte_size=len(body),
)
try:
    sa_key_vault.atomic_write(storage.sa_key_path(row.id), body)
except sa_key_vault.SAKeyVaultError as exc:
    raise HTTPException(503, "SA-key vault is unavailable") from exc
await session.commit()
```

Use generic 503 handling for download/read/remove hazards. Do not include exception text in the response or logs. Replace direct operations as follows:

```python
# download/local pull
body = sa_key_vault.read_bytes(storage.sa_key_path(key_id))

# active apply
sa_key_vault.atomic_write(dest, key_bytes)

# delete/scrub
sa_key_vault.remove(path, missing_ok=True)
```

Keep `sa_key_apply.write_active_key` as the public compatibility function, but make it a one-line delegate to `sa_key_vault.atomic_write`. Keep HTTP pull header behavior unchanged. Do not log key bytes, JSON, private-key fields, or tokens.

- [ ] **Step 4: Run GREEN focused tests**

```bash
uv run pytest tests/services/test_sa_key_vault.py \
  tests/services/test_sa_key_apply_core.py tests/services/test_worker_sa_key_sync.py \
  tests/api/test_sa_keys_auth_surface.py tests/api/test_sa_keys_api.py \
  tests/api/test_sa_keys_assign_api.py tests/api/test_sa_keys_download.py -q
```

Expected: unit tests pass; real-DB modules skip without `RUN_DB_INTEGRATION=1`.

- [ ] **Step 5: Run authenticated scratch-Postgres API acceptance**

```bash
RUN_DB_INTEGRATION=1 \
DATABASE_URL='postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc' \
uv run pytest tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py -q
```

Expected: upload/list/patch/download/assign/scrub/unassign/delete all pass with headers; query-only auth never succeeds; uploaded bytes use private permissions. Scratch fixtures use synthetic JSON only.

- [ ] **Step 6: Commit Task 4**

```bash
git add app/api/v1/sa_keys.py app/services/sa_key_apply.py app/services/worker.py \
  tests/services/test_sa_key_apply_core.py tests/services/test_worker_sa_key_sync.py \
  tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py
git commit -m "fix(sa-keys): route all credential files through vault"
```

### Task 5: Fail startup before side effects and harden both head and worker vaults

**Files:**
- Modify: `main.py`
- Modify: `app/services/worker.py`
- Create: `tests/services/test_operator_security_startup.py`
- Modify: `tests/services/test_worker_startup_applies_key.py`

**Interfaces:**
- Consumes Task 1 `require_startup_auth` and Task 3 `harden_vault`.
- Produces the same ordered synchronous preflight at the first executable line of `main.lifespan` and `worker.run_standalone`.
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
    monkeypatch.setattr(worker, "build_worker_from_settings",
                        lambda: touched.append("worker"))
    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        await worker.run_standalone()
    assert touched == []
```

Add import-safety tests: with empty token/opt-in false, `import main` and `import app.services.worker` do not raise. Add ordered spies proving valid auth calls `harden_vault` before the first later seam. Add a local-dev test with empty token + opt-in true that reaches the next mocked seam but strict SA auth remains unavailable.

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
  tests/services/test_worker_startup_applies_key.py
git commit -m "fix(startup): validate auth and harden key vault first"
```

### Task 6: Document and rehearse the hard-cut `123` rotation without restarting the head

**Files:**
- Create: `docs/runbooks/operator-token-rotation.md`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/DEPLOY.md`
- Modify: `docs/HOW_IT_WORKS.md`
- Modify: `docs/CODE_MAP.md`
- Modify: `docs/DATABASE.md`
- Modify: `docs/fleet/worker-pc-setup.md`
- Create: `tests/docs/test_operator_token_runbook.py`

**Interfaces:**
- Produces an operator-owned deployment/rollback contract; it is documentation and a command-shape test, not an automatic service restart.
- Preserves the six key files and Host-59 assignment by explicit pre/post fingerprints and read-only DB checks.

- [ ] **Step 1: Write runbook contract RED tests**

Create `tests/docs/test_operator_token_runbook.py` and assert the runbook contains, in order:

1. set fleet pause reason `operator-auth-rotation`;
2. wait until scoped/live running job count is zero;
3. read-only six-key count and Host-59 assignment snapshot;
4. SHA-256 snapshot of UUID-named key files;
5. generate `secrets.token_urlsafe(48)` without printing it into logs/history;
6. stage the same strong value into head and every worker `.env`;
7. explicit prohibition on `AUTH_TOKEN=123,<new>`;
8. explicit `DO NOT restart/kill the head from automation`; operator restarts head;
9. worker rolling restarts only after the head is healthy;
10. post-check hashes, six DB rows, Host-59 key_id/scrub state, file permissions/ACLs, heartbeats/capabilities, auth 401/200 matrix;
11. unpause only with `WHERE api_paused_reason='operator-auth-rotation'`;
12. rollback uses another strong token or old code with the new strong token, never `123`.

The test must fail if “temporarily allow 123”, “restart head automatically”, or an unscoped `UPDATE budget_state ... SET ... NULL` appears.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/docs/test_operator_token_runbook.py -q
```

Expected: FAIL because the runbook does not exist.

- [ ] **Step 3: Write the exact rotation runbook**

The runbook must use the current database pause primitive with owner-scoped SQL:

```sql
UPDATE budget_state
SET api_paused_at = now(), api_paused_reason = 'operator-auth-rotation'
WHERE id = 1
  AND (api_paused_at IS NULL OR api_paused_reason = 'operator-auth-rotation');
```

Abort if a foreign pause reason is present. Drain/wait; do not cancel running jobs. Capture these read-only facts without selecting key bytes:

```sql
SELECT count(*) AS stored_vertex_keys FROM sa_keys;
SELECT hostname, key_id, scrub_requested_at
FROM sa_key_assignments WHERE hostname = 'Host-59';
SELECT count(*) AS running_jobs FROM homework_jobs WHERE status = 'running';
```

Require count `6`, one Host-59 row with non-null `key_id` and null `scrub_requested_at`, and zero running jobs before restart. Hash only UUID-named stored files, not JSON output. Stage code + the same new token to head/workers while old processes remain paused. Explain the unavoidable mismatch window: after the operator restarts the head, old workers still have `123` in memory and receive 401 until each is restarted; no job is claimed, assignment rows and `active.json` remain untouched, and the window closes worker-by-worker.

Unpause only after all verifications:

```sql
UPDATE budget_state
SET api_paused_at = NULL, api_paused_reason = NULL
WHERE id = 1 AND api_paused_reason = 'operator-auth-rotation';
```

State explicitly: automation prepares worker files and reports readiness; the user/operator owns the head process and performs its restart. No agent kills/restarts the head without a new explicit instruction.

- [ ] **Step 4: De-stale all live docs and env guidance**

- `.env.example`: `AUTH_TOKEN=<strong-shared-token>`, `ALLOW_INSECURE_LOCAL_AUTH=false`; local-dev example is empty token plus explicit true and warns SA routes stay closed.
- `README`/`HOW_IT_WORKS`/`CODE_MAP`: empty no longer silently opens production; general query auth remains only on normal routes; every SA route is header-only.
- `DEPLOY`: replace default `123`/“strongly recommended” with default empty + “required unless explicit local dev”; link the rotation runbook.
- `DATABASE`: vault path/permission/durability/hazard contract; no schema change.
- fleet setup: workers must share the strong operator token; SA assignment state is preserved through operator-token rotation.
- `CLAUDE.md`: startup security order, local-dev opt-in, and do-not-bypass rules.

- [ ] **Step 5: Run GREEN**

```bash
uv run pytest tests/docs/test_operator_token_runbook.py -q
```

Expected: PASS; no command exposes a real token or service-account JSON.

- [ ] **Step 6: Commit Task 6**

```bash
git add docs/runbooks/operator-token-rotation.md .env.example README.md CLAUDE.md \
  docs/DEPLOY.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md docs/DATABASE.md \
  docs/fleet/worker-pc-setup.md tests/docs/test_operator_token_runbook.py
git commit -m "docs(security): add fail-closed operator token rotation"
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

Expected: all pass, including real POSIX mode assertions.

- [ ] **Step 2: Run scratch-Postgres SA API acceptance**

```bash
RUN_DB_INTEGRATION=1 \
DATABASE_URL='postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc' \
uv run pytest tests/api/test_sa_keys_api.py tests/api/test_sa_keys_assign_api.py \
  tests/api/test_sa_keys_download.py tests/integration/test_assignment_writer_locks.py -q
```

Expected: authenticated SA CRUD/assignment behavior passes; strict query rejection and assignment locking remain intact. No production DB is contacted.

- [ ] **Step 3: Run the canonical suite**

```bash
uv run pytest -q
```

Expected: green against the post-rebase baseline. No model call occurs.

- [ ] **Step 4: Run security and residue scans**

```bash
rg -n 'auth_token: str = "123"|AUTH_TOKEN.*default.*123' app README.md docs .env.example
rg -n 'include_router\(sa_keys\.router.*get_current_user\)' app
rg -n 'sa_key_path\([^)]*\)\.(read_bytes|write_bytes|unlink)' app
rg -n 'sa_key_active_path\([^)]*\)\.(read_bytes|write_bytes|unlink)' app
rg -n 'logger\.(debug|info|warning|error|exception).*\b(body|key_bytes|private_key|auth_token)\b' app
git diff --check origin/Nggaev-v2...
```

Expected: no unsafe default/router/direct-I/O/key-material-log hits; diff check clean. Separately search `AUTH_TOKEN=123` and classify historical shipped-plan/worklog examples rather than rewriting history; all live docs/config/runbooks must be clean.

- [ ] **Step 5: Re-run the mandatory collision gate**

```bash
git fetch --all --prune
git worktree list --porcelain
gh pr list --state open --limit 100 \
  --json number,title,headRefName,baseRefName,author,isDraft,mergeStateStatus
git log HEAD..origin/Nggaev-v2 --oneline
git diff --name-status origin/Nggaev-v2...
```

If the base moved, rebase, resolve composition on `config.py`/`main.py`/`worker.py`, and rerun Steps 1–4. Never edit a project-manager-owned PR/branch.

- [ ] **Step 6: Write finish records and archive the plan**

Reserve the next worklog ID using the repository's counter convention, record exact test counts and the `$0` acceptance, and state “not deployed; `123` rotation still operator-owned under global pause.” Close only the matching P0 roadmap item. Move this plan with `git mv` into `plans/shipped/`.

- [ ] **Step 7: Commit the finish**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md \
  docs/memory/ROADMAP.md docs/superpowers/plans/shipped/2026-08-10-operator-auth-sa-vault-hardening.md
git commit -m "docs(memory): record operator auth and SA vault hardening"
```

- [ ] **Step 8: Request independent review; do not deploy or self-merge**

Use `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch`. Open the implementation PR for external gate. The PR body must call out the hard-cut mismatch window, six-key/Host-59 preservation, no migration, no model call, and the fact that the user owns the eventual head restart. Do not alter `.env`, pause production, restart any process, run the rotation, push a token, or merge without the separate operator authorization.

## Deployment and rollback gate (post-merge only)

1. **Do not begin while generation is active.** Set the owner-scoped global pause and wait for `running_jobs=0`.
2. **Preserve facts first.** Record six metadata rows, Host-59's exact `key_id` + null scrub timestamp, hashes of all six stored UUID JSON files, and current file permissions/ACLs.
3. **Generate two strong values offline:** the primary and a sealed rollback replacement. Neither is printed into chat, logs, shell history, git, or artifacts.
4. **Prepare, do not restart:** pull final code and stage the primary value plus `ALLOW_INSECURE_LOCAL_AUTH=false` into head and every worker `.env`. Automation reports host-by-host readiness only.
5. **Operator restarts the head.** Verify startup accepts auth, hardens the vault without hash changes, health is live, query auth fails on every SA route, header auth works, six keys and Host-59 are intact.
6. **Restart workers in guarded batches.** During the paused mismatch window, not-yet-restarted workers use in-memory `123` and cannot pull from the new head; this is expected. Confirm each restarted worker uses the new token, is current, Gemini-capable, and retains its existing Vertex/plain-key posture.
7. **Verify all hosts, then unpause only the owned reason.** A foreign pause is never cleared.
8. **Rollback:** keep the global pause. Roll code back only while retaining a strong token, or hard-cut to the sealed strong replacement and repeat head-then-worker restarts. Never restore `123`; never delete/re-upload stored Vertex keys; never change Host-59's assignment as part of auth rollback.

## Plan self-review

- **Spec coverage:** all-SA-route header auth (Task 2); default/open/weak startup refusal + explicit local mode + test-safe lifecycle (Tasks 1/5); private modes/Windows ACL, existing/new/temp/active, atomic durability, hostile paths, and no bytes in diagnostics (Tasks 3/4/7); head+standalone startup (Task 5); six keys/Host-59 preservation, hard-cut rollout, user-owned restart, mismatch window, and rollback (Tasks 6/7/deployment gate); no generation/paid action (global constraints/Task 7).
- **Type consistency:** Task 1 exports are consumed under the same names by Tasks 2/5. Task 3's four public functions are consumed under the same names by Task 4/5. `ALLOW_INSECURE_LOCAL_AUTH` maps only to `settings.allow_insecure_local_auth`.
- **Placeholder scan:** no TBD/TODO/“similar to”/undefined helper remains. All commands, routes, statuses, token rules, modes, files, and operational order are explicit.
- **Scope check:** no encryption-at-rest, KMS, schema, SA assignment change, token-management UI, general-query-auth removal, live rollout, or model generation is included.
