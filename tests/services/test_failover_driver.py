# tests/services/test_failover_driver.py
import asyncio

import pytest

from app.services.pipeline import _failover_chain, _run_with_failover


def test_chain_requested_first_then_order_no_claude():
    chain = _failover_chain("claude")
    assert chain[0] == "claude"            # requested honored first
    assert "claude" not in chain[1:]       # never a fallback target
    assert chain[1:] == ["codex", "gemini", "kimi", "opencode"]


def test_chain_skips_requested_in_fallbacks():
    assert _failover_chain("gemini") == ["gemini", "codex", "kimi", "opencode"]


def test_failover_switches_provider_on_hard_failure():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError("claude CLI exited rc=1 :: malformed response envelope")  # hard
        return f"# ok from {provider}", 1, 2

    out, tin, tout, produced = asyncio.run(
        _run_with_failover(requested_provider="claude", model="claude-sonnet-4-6", run_fn=run_fn)
    )
    assert produced == "codex"
    assert out == "# ok from codex"
    assert calls.count("claude") == 2 and calls[-1] == "codex"


def test_model_not_found_fails_over_immediately():
    # A phantom / non-existent model returns the SAME error on every retry, so
    # the driver must fail over on the FIRST failure — no wasted same-provider
    # retry. (Regression guard for the phantom gemini-3.5-flash incident.)
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError(
                "claude CLI exited rc=1 :: ModelNotFoundError: Requested entity was not found")
        return f"# ok from {provider}", 1, 2

    out, tin, tout, produced = asyncio.run(
        _run_with_failover(requested_provider="claude", model="bogus-model", run_fn=run_fn)
    )
    assert calls.count("claude") == 1 and produced == "codex"


def test_wall_fails_over_with_no_same_retry():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise RuntimeError("weekly usage limit reached")  # wall
        return "# ok", 0, 0

    asyncio.run(_run_with_failover(requested_provider="claude", model="m", run_fn=run_fn))
    assert calls.count("claude") == 1     # wall = 0 same-provider retries


def test_attempt_timeout_fails_over_immediately():
    calls = []

    async def run_fn(provider, model):
        calls.append(provider)
        if provider == "claude":
            raise asyncio.TimeoutError()       # the per-attempt timeout
        return f"# ok from {provider}", 0, 0

    out, _tin, _tout, produced = asyncio.run(
        _run_with_failover(requested_provider="claude", model="m", run_fn=run_fn)
    )
    assert produced == "codex"
    assert calls.count("claude") == 1


def test_all_providers_exhausted_raises():
    async def run_fn(provider, model):
        raise RuntimeError("weekly usage limit reached")  # wall everywhere

    with pytest.raises(RuntimeError):
        asyncio.run(_run_with_failover(requested_provider="claude", model="m", run_fn=run_fn))
