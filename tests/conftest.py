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


import pytest


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": "Bearer test-token"}
