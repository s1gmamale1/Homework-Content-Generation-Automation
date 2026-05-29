"""Unit tests for provider fallback in ``app.services.agent``.

Phase 1 acceptance criterion: *provider fallback unit-tested*. The single
``run_phase`` path (structured-validate + one repair retry) is already
covered in ``test_agent.py``; here we drive ``run_phase_with_fallback``,
which tries an ordered ``(provider, model)`` chain and advances to the next
CLI when one exhausts its own retry budget.

Mocking mirrors ``test_agent.py``: ``_spawn`` is patched to return canned
``(rc, text, usage, stderr)`` tuples keyed by provider name, and
``_record_usage`` is patched to a no-op capture so no DB is needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from app.services import agent as agent_module
from app.services.agent import ProviderSpec, run_phase_with_fallback


class _Out(BaseModel):
    answer: str


def _usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 10,
        "output_tokens": 5,
        "cached_tokens": 0,
        "total_tokens": 15,
        "raw": {},
    }


_VALID = json.dumps({"answer": "ok"})
_INVALID = "{ not json"


def _patch(monkeypatch: pytest.MonkeyPatch, by_provider: dict[str, list]) -> dict[str, int]:
    """Patch ``_spawn`` (keyed by provider name) and ``_record_usage``.

    ``by_provider`` maps a provider name to a queue of canned spawn outputs.
    Returns a live ``calls`` dict counting spawns per provider so tests can
    assert which CLIs were actually invoked.
    """
    calls: dict[str, int] = {}

    async def fake_spawn(*, provider: Any, model: Any, prompt: str, attachments: list[Any]):
        calls[provider.name] = calls.get(provider.name, 0) + 1
        return by_provider[provider.name].pop(0)

    async def fake_record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)
    return calls


@pytest.mark.asyncio
async def test_fallback_uses_secondary_when_primary_cli_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary CLI exits non-zero (run_phase raises) → fall back to the
    secondary, which succeeds and returns the validated parse."""
    calls = _patch(
        monkeypatch,
        {
            "claude": [(1, "", _usage(), "boom: model not found")],
            "gemini": [(0, _VALID, _usage(), "")],
        },
    )

    result = await run_phase_with_fallback(
        providers=[ProviderSpec("claude"), ProviderSpec("gemini")],
        phase_prompt="x",
        phase_name="fb",
        homework_job_id=None,
        phase_output_id=None,
        schema=_Out,
    )

    assert isinstance(result.parsed, _Out)
    assert result.parsed.answer == "ok"
    assert calls == {"claude": 1, "gemini": 1}


@pytest.mark.asyncio
async def test_fallback_on_validation_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary returns invalid JSON on both attempts (its repair retry is
    spent), then the chain advances to the secondary which validates."""
    calls = _patch(
        monkeypatch,
        {
            "claude": [(0, _INVALID, _usage(), ""), (0, _INVALID, _usage(), "")],
            "gemini": [(0, _VALID, _usage(), "")],
        },
    )

    result = await run_phase_with_fallback(
        providers=[ProviderSpec("claude"), ProviderSpec("gemini")],
        phase_prompt="x",
        phase_name="fb",
        homework_job_id=None,
        phase_output_id=None,
        schema=_Out,
    )

    assert isinstance(result.parsed, _Out)
    assert calls == {"claude": 2, "gemini": 1}  # 2 attempts on primary, 1 on fallback


@pytest.mark.asyncio
async def test_fallback_all_providers_fail_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every provider in the chain errors → a single RuntimeError that names
    the exhausted chain."""
    _patch(
        monkeypatch,
        {
            "claude": [(1, "", _usage(), "boom")],
            "gemini": [(1, "", _usage(), "boom")],
        },
    )

    with pytest.raises(RuntimeError, match="all .* providers failed"):
        await run_phase_with_fallback(
            providers=[ProviderSpec("claude"), ProviderSpec("gemini")],
            phase_prompt="x",
            phase_name="fb",
            homework_job_id=None,
            phase_output_id=None,
            schema=_Out,
        )


@pytest.mark.asyncio
async def test_fallback_single_provider_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-element chain behaves exactly like a bare run_phase call."""
    calls = _patch(monkeypatch, {"gemini": [(0, _VALID, _usage(), "")]})

    result = await run_phase_with_fallback(
        providers=[ProviderSpec("gemini", "gemini-2.5-flash")],
        phase_prompt="x",
        phase_name="fb",
        homework_job_id=None,
        phase_output_id=None,
        schema=_Out,
    )

    assert result.parsed.answer == "ok"
    assert calls == {"gemini": 1}


@pytest.mark.asyncio
async def test_fallback_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        await run_phase_with_fallback(
            providers=[],
            phase_prompt="x",
            phase_name="fb",
            homework_job_id=None,
            phase_output_id=None,
        )
