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
# The extract-completeness check makes a REAL model call. Default it OFF for the
# suite so no test can reach a spawn through pipeline's extract branch; the tests
# that exercise it re-enable it explicitly (test_pipeline_extract_coverage.py).
os.environ.setdefault("EXTRACT_COVERAGE_CHECK_ENABLED", "false")


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
