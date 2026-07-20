"""_spawn must NOT retry a slot-exhaustion 429 — each retry burns another
120s fleet-slot wait; the pipeline parks the job instead (SlotSaturation).

RED-proof: today _is_rate_limited matches the '429 …' marker text, so the
loop burns all rate_limit_max_retries attempts."""
import asyncio

import pytest

from app.services import agent
from app.services.providers import get_provider


SLOT_TUPLE = (
    1, "", {},
    "429 fleet credential slot wait exhausted (credential=gemini:p, budget=120s)",
)


def test_spawn_returns_slot_exhaustion_after_single_attempt(monkeypatch):
    calls = []

    async def fake_spawn_once(**kwargs):
        calls.append(1)
        return SLOT_TUPLE

    async def no_sleep(_):  # pragma: no cover — must not be reached
        raise AssertionError("slot exhaustion must not back off and retry")

    monkeypatch.setattr(agent, "_spawn_once", fake_spawn_once)
    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)

    rc, text, usage, stderr = asyncio.run(agent._spawn(
        provider=get_provider("gemini"), model="gemini-2.5-flash",
        prompt="x", attachments=[], transport="api",
    ))
    assert calls == [1]
    assert rc == 1 and "fleet credential slot wait exhausted" in stderr


def test_spawn_still_retries_ordinary_429(monkeypatch):
    """Guard: an ordinary provider 429 keeps the existing backoff loop."""
    calls = []

    async def fake_spawn_once(**kwargs):
        calls.append(1)
        return (1, "", {}, "429 RESOURCE_EXHAUSTED")

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(agent, "_spawn_once", fake_spawn_once)
    monkeypatch.setattr(agent.asyncio, "sleep", instant_sleep)

    asyncio.run(agent._spawn(
        provider=get_provider("gemini"), model="gemini-2.5-flash",
        prompt="x", attachments=[], transport="api",
    ))
    assert len(calls) == agent.settings.rate_limit_max_retries + 1
