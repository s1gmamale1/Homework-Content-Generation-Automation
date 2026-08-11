"""Test bootstrap.

The app's ``Settings`` class requires ``DATABASE_URL`` and ``GEMINI_API_KEY``
at import time (via pydantic-settings). Tests don't talk to a real DB or
Gemini, so we inject sentinel values into ``os.environ`` *before* anything
imports ``app.config`` / ``app.db``.

Tests that need DB writes mock at the ``usage_repo.create`` layer; no
real database is wired up here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Project root on sys.path so ``import app...`` resolves regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Sentinel env so Settings() doesn't blow up on import. These values are
# never used because nothing in the test path actually opens a connection.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test_db",
)
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
os.environ.setdefault("AUTH_TOKEN", "")
os.environ.setdefault("ALLOW_INSECURE_LOCAL_AUTH", "true")
# The extract-completeness check makes a REAL model call. Default it OFF for the
# suite so no test can reach a spawn through pipeline's extract branch; the tests
# that exercise it re-enable it explicitly (test_pipeline_extract_coverage.py).
os.environ.setdefault("EXTRACT_COVERAGE_CHECK_ENABLED", "false")


# ── real-DB tests must never be pointed at production ────────────────────
# The `RUN_DB_INTEGRATION=1` tests CREATE books, batches and jobs. They take
# whatever `DATABASE_URL` is in the environment, so running them with a normal
# operator env writes that seed data straight into the live database. Nothing
# stopped that before; this refuses at collection time instead.
#
# Two independent tripwires, because the two ways to get here are different:
#   * a PRODUCTION database name (`edu_copy` is the live fleet DB), and
#   * a NON-LOCAL host — the trap that actually catches people. A git worktree
#     has no `.env` of its own, so `load_dotenv` walks UP to the parent
#     directory's `.env`, which points at the remote head. Deriving a "scratch"
#     URL inside a worktree therefore silently aims at a real fleet host. That
#     has now caught two separate people on this project.
#
# Deliberately a DENYLIST, not an allowlist: requiring the name to contain
# "scratch" would break anyone running these against a normal local dev DB
# (`edu_homework`), which is legitimate.
_PROD_DB_NAMES = {"edu_copy"}


def _guard_db_integration_target() -> None:
    if os.getenv("RUN_DB_INTEGRATION") != "1":
        return
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
        name = (parsed.path or "").lstrip("/")
        host = parsed.hostname or ""
    except Exception:  # noqa: BLE001 — a URL we cannot parse is not our business
        return
    if name in _PROD_DB_NAMES:
        raise RuntimeError(
            f"REFUSING to run RUN_DB_INTEGRATION tests against database {name!r} — "
            "that is PRODUCTION. These tests create books/batches/jobs. Point "
            "DATABASE_URL at a scratch database."
        )
    if host and host not in ("127.0.0.1", "localhost", "::1", ""):
        raise RuntimeError(
            f"REFUSING to run RUN_DB_INTEGRATION tests against host {host!r} — "
            "these tests write, and a non-local host is a real fleet machine. "
            "If you derived this URL inside a git worktree, note the worktree has "
            "no .env of its own and load_dotenv walks up to the parent one. Pin "
            "127.0.0.1 explicitly."
        )


_guard_db_integration_target()


@pytest.fixture(autouse=True)
def _loopback_events_bus(request, monkeypatch):
    """Unit tests never open a DB connection (module docstring above) — but
    the NOTIFY-backed events bus would. Route ``_notify`` (the ENCODED wire
    payload, post-``_encode``) straight into the local dispatcher: old
    in-process delivery semantics preserved, the real encode → wire-bytes →
    dispatch path still exercised. ``@pytest.mark.real_events_bus`` opts out
    (integration tests that need real pg_notify semantics); the cross-process
    test's publisher is a subprocess outside pytest and is unaffected anyway."""
    if request.node.get_closest_marker("real_events_bus"):
        yield
        return
    from app.services import events_bus

    async def _loopback(payload: str) -> None:
        events_bus._dispatch(payload)

    monkeypatch.setattr(events_bus, "_notify", _loopback)
    yield
