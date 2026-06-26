# tests/services/test_session_limit_strategy_failover.py
"""TDD tests for Task 4: session-limit strategy wired into _run_with_failover.

Verbatim session-limit string from the Oliver-worker log (2026-06-23):
  You've hit your session limit · resets 12:50am (America/Chicago)

Strategy=switch → failover to next provider (budget=0, no claude retry).
Strategy=pause  → raise SessionLimitPause with parsed reset_at.
Non-session-limit transient errors still get same-provider retries (regression).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import agent as agent_module
import app.services.pipeline as pipeline_module
from app.services.pipeline import _run_with_failover
from app.services.errors import SessionLimitPause

# Verbatim from the live log (also used in test_session_limit_classify_parse.py)
SESSION_LIMIT_MSG = "You've hit your session limit · resets 12:50am (America/Chicago)"

# Fixed "now" for clock injection — 2026-06-23 22:00 Chicago (CDT = UTC-5 = UTC+(-5))
# → 12:50am Chicago the next calendar day = 2026-06-24 05:50 UTC
FIXED_NOW = datetime(2026, 6, 24, 3, 0, 0, tzinfo=timezone.utc)   # 2026-06-23 22:00 CDT
EXPECTED_RESET = datetime(2026, 6, 24, 5, 50, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _all_clis_installed(monkeypatch):
    """Pin provider_cli_installed=True so failover chain doesn't depend on
    which CLIs happen to be on the test box (same guard as test_failover_driver)."""
    monkeypatch.setattr(agent_module, "provider_cli_installed", lambda name: True)


# ── switch strategy ────────────────────────────────────────────────────────────

def test_switch_advances_to_next_provider_on_session_limit():
    """With strategy=switch, a session-limit error on claude (requested provider)
    must advance to the next provider (codex) with no same-claude retry (budget=0)."""
    calls: list[str] = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError(SESSION_LIMIT_MSG)
        return f"# ok from {provider}", 10, 20

    out, tin, tout, produced = asyncio.run(
        _run_with_failover(
            requested_provider="claude",
            model="claude-sonnet-4-6",
            run_fn=run_fn,
            session_limit_strategy="switch",
        )
    )

    assert produced == "codex", f"expected codex, got {produced!r}"
    assert out == "# ok from codex"
    # Budget=0 for switch → claude is called exactly once, not retried
    assert calls.count("claude") == 1, f"claude called {calls.count('claude')} times, want 1"


def test_switch_does_not_raise_session_limit_pause():
    """switch must NOT raise SessionLimitPause — it should return a result from
    the fallback provider instead."""
    async def run_fn(provider, model):
        if provider == "claude":
            raise RuntimeError(SESSION_LIMIT_MSG)
        return "# fallback ok", 0, 0

    # Must not raise
    result = asyncio.run(
        _run_with_failover(
            requested_provider="claude",
            model="m",
            run_fn=run_fn,
            session_limit_strategy="switch",
        )
    )
    assert result is not None


# ── pause strategy ─────────────────────────────────────────────────────────────

def test_pause_raises_session_limit_pause(monkeypatch):
    """With strategy=pause, a session-limit error must raise SessionLimitPause
    (not continue to the next provider)."""
    monkeypatch.setattr(pipeline_module, "_utcnow", lambda: FIXED_NOW)

    async def run_fn(provider, model):
        raise RuntimeError(SESSION_LIMIT_MSG)

    with pytest.raises(SessionLimitPause) as exc_info:
        asyncio.run(
            _run_with_failover(
                requested_provider="claude",
                model="claude-sonnet-4-6",
                run_fn=run_fn,
                session_limit_strategy="pause",
            )
        )

    pause = exc_info.value
    assert pause.reset_at == EXPECTED_RESET, (
        f"reset_at mismatch: got {pause.reset_at!r}, expected {EXPECTED_RESET!r}"
    )


def test_pause_reset_at_can_be_none(monkeypatch):
    """If the reset time can't be parsed (no clock in the message), reset_at is None.
    SessionLimitPause is still raised (strategy=pause is unconditional on is_session_limit)."""
    monkeypatch.setattr(pipeline_module, "_utcnow", lambda: FIXED_NOW)

    # A session-limit string with a clock (matches is_session_limit) but let's
    # verify the exception is still raised (the parse may return None for unusual clocks).
    # We test this by using the REAL message and confirming reset_at is non-None.
    # Separately, test that even a message with no parseable clock raises the exc.
    # Use a message that matches is_session_limit but has unrecognised clock format.
    # Actually, the is_session_limit regex == _SESSION_LIMIT_RE (resets <clock>),
    # and parse_session_limit_reset uses the same regex — so if is_session_limit=True,
    # parse_session_limit_reset will also succeed. The only way to get reset_at=None
    # would require patching parse_session_limit_reset to return None.
    from unittest.mock import patch
    from app.services import failure_classifier

    with patch.object(failure_classifier, "parse_session_limit_reset", return_value=None):
        async def run_fn(provider, model):
            raise RuntimeError(SESSION_LIMIT_MSG)

        with pytest.raises(SessionLimitPause) as exc_info:
            asyncio.run(
                _run_with_failover(
                    requested_provider="claude",
                    model="m",
                    run_fn=run_fn,
                    session_limit_strategy="pause",
                )
            )

    assert exc_info.value.reset_at is None


def test_pause_does_not_advance_to_next_provider(monkeypatch):
    """With strategy=pause, the chain must NOT advance — the fallback provider
    must never be called."""
    monkeypatch.setattr(pipeline_module, "_utcnow", lambda: FIXED_NOW)
    calls: list[str] = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError(SESSION_LIMIT_MSG)
        return "# codex ok", 0, 0

    with pytest.raises(SessionLimitPause):
        asyncio.run(
            _run_with_failover(
                requested_provider="claude",
                model="m",
                run_fn=run_fn,
                session_limit_strategy="pause",
            )
        )

    assert "codex" not in calls, f"codex was called with pause strategy: calls={calls}"
    assert calls == ["claude"], f"unexpected calls: {calls}"


# ── non-session-limit regression ───────────────────────────────────────────────

def test_non_session_limit_transient_still_retries_same_provider():
    """A transient error (not a session limit) must still get same-provider retries
    per _SAME_RETRY_BUDGET['transient']=2 — regression guard."""
    calls: list[str] = []
    call_count = {"claude": 0}

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            call_count["claude"] += 1
            if call_count["claude"] < 3:
                # Transient: connect error, no session-limit text
                raise RuntimeError("connect timeout — transient")
            return "# claude recovered", 5, 10
        return "# codex ok", 0, 0

    out, _tin, _tout, produced = asyncio.run(
        _run_with_failover(
            requested_provider="claude",
            model="claude-sonnet-4-6",
            run_fn=run_fn,
            session_limit_strategy="pause",   # strategy shouldn't affect non-session-limit
        )
    )

    # Budget=2 means up to 2 retries on the same provider (3 total attempts)
    assert produced == "claude"
    assert call_count["claude"] == 3, f"expected 3 claude attempts, got {call_count['claude']}"


def test_non_session_limit_wall_still_fails_over():
    """A wall error (no clock reset) with strategy=pause must still follow
    failure_classifier.classify → budget=0 → advance to next provider.
    This ensures session-limit detection and wall-class failures are independent."""
    calls: list[str] = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError("weekly usage limit reached")  # wall, no clock = not session-limit
        return "# codex ok", 0, 0

    out, _tin, _tout, produced = asyncio.run(
        _run_with_failover(
            requested_provider="claude",
            model="m",
            run_fn=run_fn,
            session_limit_strategy="pause",  # strategy only applies to session-limit
        )
    )

    assert produced == "codex"
    assert calls.count("claude") == 1  # wall = 0 retries
