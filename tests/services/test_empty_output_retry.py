"""Markdown phases retry once on an empty CLI body (harvested from PR #1).

Some CLIs intermittently return rc=0 with an empty body (e.g. gemini's
transient INVALID_STREAM on SVG-heavy prompts). Stored as-is that becomes a
blank phase. ``run_phase`` now treats an empty markdown body as a failure and
retries once before failing loudly.
"""

from __future__ import annotations

import pytest

from app.services import agent


def _usage() -> dict:
    return {"prompt_tokens": 1, "output_tokens": 1, "cached_tokens": 0,
            "total_tokens": 2, "raw": {}}


def _fake_spawn_sequence(*bodies):
    """Return an async _spawn stub that yields the given bodies in order
    (each as a successful rc=0 response)."""
    calls = {"n": 0}

    async def _spawn(*, provider, model, prompt, attachments):
        i = calls["n"]
        calls["n"] += 1
        body = bodies[i] if i < len(bodies) else bodies[-1]
        return 0, body, _usage(), ""

    return _spawn, calls


@pytest.mark.asyncio
async def test_markdown_retries_once_on_empty_then_succeeds(monkeypatch) -> None:
    spawn, calls = _fake_spawn_sequence("", "Real markdown body.")
    monkeypatch.setattr(agent, "_spawn", spawn)

    result = await agent.run_phase(
        provider="claude", model=None,
        phase_prompt="x", phase_name="reflection",
        homework_job_id=None, phase_output_id=None,
        lesson_context="ctx", schema=None,
    )
    assert result.text == "Real markdown body."
    assert calls["n"] == 2  # retried exactly once


@pytest.mark.asyncio
async def test_markdown_raises_after_two_empty_bodies(monkeypatch) -> None:
    spawn, calls = _fake_spawn_sequence("", "   ")
    monkeypatch.setattr(agent, "_spawn", spawn)

    with pytest.raises(RuntimeError):
        await agent.run_phase(
            provider="claude", model=None,
            phase_prompt="x", phase_name="reflection",
            homework_job_id=None, phase_output_id=None,
            lesson_context="ctx", schema=None,
        )
    assert calls["n"] == 2  # tried twice, then gave up


@pytest.mark.asyncio
async def test_markdown_non_empty_first_try_does_not_retry(monkeypatch) -> None:
    spawn, calls = _fake_spawn_sequence("Good body.")
    monkeypatch.setattr(agent, "_spawn", spawn)

    result = await agent.run_phase(
        provider="claude", model=None,
        phase_prompt="x", phase_name="reflection",
        homework_job_id=None, phase_output_id=None,
        lesson_context="ctx", schema=None,
    )
    assert result.text == "Good body."
    assert calls["n"] == 1  # no retry needed
