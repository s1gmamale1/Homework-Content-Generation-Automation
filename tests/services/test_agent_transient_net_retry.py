"""Tests: _spawn retries transient network errors; terminal errors do not retry.

fleet-net-1 code-half — task 1 of cluster-5-fleet-resilience.

Coverage
--------
- ``_is_transient_net``: each term in ``_TRANSIENT_NET_TERMS`` returns True;
  auth/truncation/session-limit strings return False (unit level).
- ``_spawn`` retry loop: each net-error term causes >1 calls to ``_spawn_once``
  (retry path exercised).
- ``_spawn`` terminal cases: auth (401/PERMISSION_DENIED), truncation
  (MAX_TOKENS/prompt too long) → exactly one call, no sleep.
- DEFENSIVE: verbatim Claude session-limit string → exactly one call, no sleep.
  Tasks 2-5 depend on this string bubbling up so higher layers can auto-pause.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import agent as agent_module
from app.services.agent import _TRANSIENT_NET_TERMS, _is_transient_net


class _StubProvider:
    """Minimal stand-in for a Provider — ``_spawn`` only reads ``.name``."""

    name = "gemini"


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``asyncio.sleep`` with a no-op that records each delay."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)
    return sleeps


# ─── unit: _is_transient_net ─────────────────────────────────────────────────


@pytest.mark.parametrize("term", list(_TRANSIENT_NET_TERMS))
def test_is_transient_net_true(term: str) -> None:
    """Every listed term (lower or upper case) returns True."""
    assert _is_transient_net(term) is True
    assert _is_transient_net(term.upper()) is True


def test_is_transient_net_false_empty() -> None:
    assert _is_transient_net("") is False


@pytest.mark.parametrize(
    "text",
    [
        "401 UNAUTHENTICATED",
        "403 PERMISSION_DENIED",
        "MAX_TOKENS exceeded",
        "prompt is too long",
        # The defensive case: verbatim Claude session-limit string
        "You've hit your session limit · resets 12:50am (America/Chicago)",
    ],
)
def test_is_transient_net_false_terminal(text: str) -> None:
    """Auth, truncation, and session-limit strings must NOT match net terms."""
    assert _is_transient_net(text) is False


# ─── _spawn: net errors retry ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("net_text", list(_TRANSIENT_NET_TERMS))
async def test_spawn_retries_on_transient_net_error(
    monkeypatch: pytest.MonkeyPatch,
    net_text: str,
) -> None:
    """Each net-error term causes _spawn to call _spawn_once more than once."""
    outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (1, "", {"raw": {}}, net_text),
        (0, "ok", {"raw": {}}, ""),
    ]
    calls: dict[str, int] = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return outputs.pop(0)

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    _patch_sleep(monkeypatch)

    rc, text, _usage, _stderr = await agent_module._spawn(
        provider=_StubProvider(),
        model=None,
        prompt="x",
        attachments=[],
        transport="cli",
    )

    assert rc == 0
    assert text == "ok"
    assert calls["n"] > 1, (
        f"Expected retry for net term {net_text!r}, got {calls['n']} call(s)"
    )


# ─── _spawn: terminal errors do NOT retry ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "err_text,label",
    [
        ("401 UNAUTHENTICATED", "auth-401"),
        ("403 PERMISSION_DENIED", "auth-403"),
        ("MAX_TOKENS exceeded", "truncation-max-tokens"),
        ("prompt is too long", "truncation-too-long"),
    ],
)
async def test_spawn_does_not_retry_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
    err_text: str,
    label: str,
) -> None:
    """Auth and truncation errors must NOT retry — they never self-heal."""
    calls: dict[str, int] = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return (1, "", {"raw": {}}, err_text)

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, _text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider(),
        model=None,
        prompt="x",
        attachments=[],
        transport="cli",
    )

    assert rc == 1
    assert calls["n"] == 1, (
        f"Terminal error {label!r} must not retry; got {calls['n']} call(s)"
    )
    assert sleeps == []


# ─── DEFENSIVE: session-limit string must NEVER retry ────────────────────────

_SESSION_LIMIT_STR = (
    "You've hit your session limit · resets 12:50am (America/Chicago)"
)


@pytest.mark.asyncio
async def test_spawn_does_not_retry_session_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verbatim Claude session-limit string must propagate unchanged with
    exactly one call to ``_spawn_once`` — no retry, no sleep.

    Tasks 2-5 depend on this string reaching higher layers unmodified so they
    can classify it as a session-limit event and trigger auto-pause logic.
    """
    calls: dict[str, int] = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return (1, "", {"raw": {}}, _SESSION_LIMIT_STR)

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, _text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider(),
        model=None,
        prompt="x",
        attachments=[],
        transport="cli",
    )

    assert rc == 1
    assert _SESSION_LIMIT_STR in stderr
    assert calls["n"] == 1, (
        f"Session-limit string must NOT cause a retry; got {calls['n']} call(s). "
        "A term in _TRANSIENT_NET_TERMS matched it — narrow the term."
    )
    assert sleeps == []
