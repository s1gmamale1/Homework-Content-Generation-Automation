"""api transport restricts failover to the requested provider only.

An api job has no cross-provider fallback (codex/kimi/opencode have no api
support, and api-claude→api-gemini with model=None violates the explicit-model
rule). Same-provider retry budgets still apply, but the chain is pinned to the
requested provider.
"""

import asyncio

import pytest

from app.services.pipeline import _run_with_failover


def test_api_failover_stays_on_requested_provider():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        raise RuntimeError("claude CLI exited rc=1 :: malformed response envelope")  # hard

    with pytest.raises(RuntimeError):
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=run_fn, transport="api",
        ))

    # Only ever the requested provider — never a fallback target.
    assert set(calls) == {"gemini"}
    # hard = 1 same-provider retry → 2 total attempts, then raise.
    assert calls == ["gemini", "gemini"]


def test_cli_failover_still_crosses_providers():
    """Contrast: cli transport (default) DOES fall over to other providers,
    proving the restriction is api-only."""
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        raise RuntimeError("claude CLI exited rc=1 :: malformed response envelope")  # hard

    with pytest.raises(RuntimeError):
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=run_fn, transport="cli",
        ))

    # The cli chain reaches fallback providers beyond the requested one.
    assert {"codex", "kimi", "opencode"} & set(calls)


def test_api_failover_default_transport_is_cli():
    """Omitting transport defaults to cli → still crosses providers."""
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        raise RuntimeError("claude CLI exited rc=1 :: malformed response envelope")  # hard

    with pytest.raises(RuntimeError):
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash", run_fn=run_fn,
        ))

    assert {"codex", "kimi", "opencode"} & set(calls)
