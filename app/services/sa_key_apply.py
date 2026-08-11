"""Worker-side: fetch + apply an assigned SA key live (no restart).

Pure-ish units (file/env/http) the worker orchestrates. The capability-global
rebind lives in worker.py to avoid a worker<->this circular import."""
from __future__ import annotations

from pathlib import Path
from typing import MutableMapping

import httpx

from app.config import settings
from app.services import sa_key_vault, storage

_PULL_TIMEOUT = 30.0


def write_active_key(key_bytes: bytes, dest: Path) -> None:
    """Atomically place `key_bytes` at `dest` (same-dir temp + os.replace), so a
    concurrent reader (an agent spawn assembling child_env) never sees a torn file."""
    sa_key_vault.atomic_write(dest, key_bytes)


def set_credentials_env(env: MutableMapping, creds_path: str, project_id: str) -> None:
    """Point ``env`` at the Vertex service-account pair.

    Also pops any leftover ``GEMINI_API_KEY`` — an explicit SA-key
    assignment WINS over a stale env-file key (BE-16 task 5, codex-review
    #7 — behavior change, flagged for gate). Without this, a host that once
    had ``GEMINI_API_KEY`` set would keep billing/fingerprinting off that
    old key (``_gemini_client``/``credential_id.credential_for`` both check
    ``GEMINI_API_KEY`` FIRST), silently ignoring this assignment — billing,
    limiter identity, and the operator panel would then all disagree.
    """
    env["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    env["GOOGLE_CLOUD_PROJECT"] = project_id
    env.pop("GEMINI_API_KEY", None)


def clear_credentials_env(env: MutableMapping) -> None:
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    env.pop("GOOGLE_CLOUD_PROJECT", None)


def pull_key_bytes(key_id: str) -> bytes:
    """Read the key bytes: straight from disk on the head (no fleet_head_url), else
    HTTP GET the head's download endpoint with the Bearer token (book_fetch idiom)."""
    head = settings.fleet_head_url.strip()
    if not head:
        return sa_key_vault.read_bytes(storage.sa_key_path(key_id))
    token = settings.auth_token.split(",")[0].strip()
    url = f"{head.rstrip('/')}/api/v1/sa-keys/{key_id}/download"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=_PULL_TIMEOUT) as http:
        resp = http.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"head returned HTTP {resp.status_code}")
        if not resp.content:
            raise RuntimeError("head returned empty body")
        return resp.content


def upsert_env_file(env_path: Path, updates: dict[str, "str | None"]) -> None:
    """Set/replace each KEY=value in `env_path`, preserving all other lines
    byte-for-byte (UTF-8). A value of None removes that key's line. Creates the
    file if absent."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if ("=" in line and not line.lstrip().startswith("#")) else None
        if key in remaining:
            val = remaining.pop(key)
            if val is not None:
                out.append(f"{key}={val}")
            # None -> drop the line
        else:
            out.append(line)
    for key, val in remaining.items():
        if val is not None:
            out.append(f"{key}={val}")
    env_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def env_file_has_credentials(env_path: Path) -> bool:
    """True when `env_path` has a non-comment `GOOGLE_APPLICATION_CREDENTIALS=`
    or `GOOGLE_CLOUD_PROJECT=` line. False for a missing file. Same
    line-parsing idiom as `upsert_env_file` — used by the worker's scrub
    residue gate so a restarted process (in-memory state lost) can still see
    a leftover credential line the old sha-only guard could never detect."""
    if not env_path.exists():
        return False
    lines = env_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        key = line.split("=", 1)[0].strip() if ("=" in line and not line.lstrip().startswith("#")) else None
        if key in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
            return True
    return False
