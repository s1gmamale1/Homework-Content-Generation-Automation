"""_run_with_failover emits typed signals (queue-correctness-1).

RED-proofs: today a timing-out run_fn raises bare asyncio.TimeoutError with
str()=='' (blank error_message bug), and a slot-exhaustion RuntimeError is
classified like any wall/hard error instead of raising SlotSaturation."""
import asyncio

import pytest

from app.services.errors import PhaseAttemptTimeout, SlotSaturation
from app.services.pipeline import _run_with_failover


def test_persistent_timeout_raises_typed_nonblank_error(monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline.settings, "per_attempt_timeout_seconds", 0.02)

    async def hung_run_fn(provider, model):
        await asyncio.sleep(5)

    with pytest.raises(PhaseAttemptTimeout) as ei:
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=hung_run_fn, transport="api",
        ))
    assert str(ei.value)                       # never blank
    assert "timeout" in str(ei.value).lower()
    assert "gemini" in str(ei.value)


def test_slot_saturation_raises_immediately_no_retry():
    calls = []

    async def saturated_run_fn(provider, model):
        calls.append(provider)
        raise RuntimeError(
            "gemini api call failed rc=1: 429 fleet credential slot wait "
            "exhausted (credential=gemini:p, budget=120s)"
        )

    with pytest.raises(SlotSaturation):
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=saturated_run_fn, transport="api",
        ))
    assert calls == ["gemini"]     # exactly one attempt — park, don't grind
