"""Worker code-vintage detection (fleet-worker-version-gate-1, worklog 0131).

Every process derives a monotonic code version from git at import time:
`git rev-list --count HEAD` on the linear squash-merge branch is an orderable
integer; `git rev-parse --short HEAD` names the exact vintage. The claim gate
compares CODE_VERSION against the fleet floor (budget_state.min_worker_version);
the heartbeat publishes both values; claimed_by carries the sha.

Env override: WORKER_CODE_VERSION=<int> wins over git for the NUMBER (escape
hatch for non-git deployments); the sha still comes from git when available.
Detection failure is LOUD (logger.error) and yields None — a versionless
worker is blocked whenever a floor is set (fail-closed, never fail-silent).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

# Project root: app/services/code_version.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> Optional[str]:
    """Run one git command against the repo root; None on any failure."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


def detect(env: Optional[dict] = None) -> tuple[Optional[int], Optional[str]]:
    """Return (code_version, git_sha). WORKER_CODE_VERSION env wins for the number."""
    environ = os.environ if env is None else env
    sha = _git("rev-parse", "--short", "HEAD")
    override = environ.get("WORKER_CODE_VERSION")
    if override:
        try:
            return int(override), sha
        except ValueError:
            logger.error(
                f"WORKER_CODE_VERSION={override!r} is not an integer — ignoring override"
            )
    # Shallow-clone guard (gate condition): a shallow clone's rev-list count
    # is the truncated fetch depth, not the true commit count — reporting it
    # would make this box look ancient and idle it permanently with a STALE
    # that no pull fixes. Refuse the bogus number, name the actual fix.
    if _git("rev-parse", "--is-shallow-repository") == "true":
        logger.error(
            "code_version: this checkout is a SHALLOW clone — rev-list count "
            "would be the truncated depth, not the real version. Run "
            "`git fetch --unshallow` (or set WORKER_CODE_VERSION=<int>). "
            "Until then this process is BLOCKED from claiming whenever a "
            "version floor is set"
        )
        return None, sha
    count = _git("rev-list", "--count", "HEAD")
    if count is None:
        logger.error(
            "code_version: cannot detect code version (git unavailable or not a "
            "checkout) — this process will be BLOCKED from claiming whenever a "
            "version floor is set; set WORKER_CODE_VERSION=<int> to override"
        )
        return None, sha
    return int(count), sha


def is_stale(version: Optional[int], floor: Optional[int]) -> bool:
    """True when the claim gate must refuse: a floor exists and this worker is
    below it — or cannot prove its version at all (fail-closed)."""
    if floor is None:
        return False
    if version is None:
        return True
    return version < floor


# Computed once at import. Consumers read the globals; tests call detect().
CODE_VERSION, GIT_SHA = detect()
