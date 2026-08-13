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
from app.services import failure_classifier as fc
from app.services.agent import _TRANSIENT_NET_TERMS, _is_transient_net

# The verbatim production failure (2026-08-13): a `practice-jigsaw` phase died
# on httpx's ConnectError text and the job was marked `failed` at attempts=1 of
# QUEUE_MAX_ATTEMPTS=3 — the host was healthy seconds later.  Neither list knew
# any httpx/httpcore shape, but google-genai speaks httpx.
_PROD_HTTPX_CONNECT_ERROR = (
    "practice-jigsaw: phase.run practice-jigsaw: gemini api call failed rc=1: "
    "All connection attempts failed :: All connection attempts failed"
)

# httpx/httpcore transport shapes that must all be retryable.
_HTTPX_SHAPES = [
    "All connection attempts failed",
    "httpx.ConnectError: All connection attempts failed",
    "httpcore.ConnectError('[Errno 61] Connection refused')",
    "httpx.ConnectTimeout: timed out",
    "httpx.ReadTimeout",
    "httpcore.RemoteProtocolError: Server disconnected without sending a response.",
]


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


def test_terms_are_the_shared_source_of_truth() -> None:
    """One tuple, two classifiers. The 2026-08-13 outage was caused by
    ``agent`` and ``failure_classifier`` keeping SEPARATE transient-network
    lists that drifted — pin that they are now literally the same object and
    that every net term is also a ``failure_classifier`` transient signal."""
    assert _TRANSIENT_NET_TERMS is fc.TRANSIENT_NET_TERMS
    for term in _TRANSIENT_NET_TERMS:
        assert fc.classify(term) == "transient", (
            f"{term!r} retries in _spawn but is not 'transient' to "
            "failure_classifier — the two classifiers have drifted apart again"
        )


def test_is_transient_net_production_httpx_string() -> None:
    """The verbatim production string must be transient — this is the defect."""
    assert _is_transient_net(_PROD_HTTPX_CONNECT_ERROR) is True


@pytest.mark.parametrize("text", _HTTPX_SHAPES)
def test_is_transient_net_httpx_shapes(text: str) -> None:
    """httpx/httpcore error shapes — google-genai's actual HTTP stack."""
    assert _is_transient_net(text) is True


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


@pytest.mark.asyncio
async def test_spawn_retries_production_httpx_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION (2026-08-13): the verbatim production failure must retry with
    a backoff sleep, not return after a single attempt."""
    outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (1, "", {"raw": {}}, _PROD_HTTPX_CONNECT_ERROR),
        (0, "ok", {"raw": {}}, ""),
    ]
    calls: dict[str, int] = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return outputs.pop(0)

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, text, _usage, _stderr = await agent_module._spawn(
        provider=_StubProvider(),
        model=None,
        prompt="x",
        attachments=[],
        transport="api",
    )

    assert (rc, text) == (0, "ok")
    assert calls["n"] == 2, (
        f"httpx ConnectError must retry; got {calls['n']} call(s)"
    )
    assert sleeps and sleeps[0] > 0, "retry must back off, not spin"


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
