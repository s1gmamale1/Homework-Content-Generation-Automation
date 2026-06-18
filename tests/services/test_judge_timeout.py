"""Tests for _judge_with_timeout — the thin per-attempt-timeout wrapper around
phase_judge.judge.  asyncio_mode=auto (pyproject.toml) so no decorator needed.
"""
import asyncio
import uuid

import pytest

from app.services import phase_judge, pipeline
from app.services.phase_judge import JudgeOutcome


# ---------------------------------------------------------------------------
# Shared kwargs that satisfy _judge_with_timeout/**phase_judge.judge signature
# ---------------------------------------------------------------------------
_KWARGS = dict(
    subject="math",
    phase_name="preview",
    output_md="# Some output",
    lesson_context=None,
    prior_outputs={},
    gen_provider="claude",
    gen_model=None,
    judge_provider="claude",
    judge_model=None,
    homework_job_id=None,
    phase_output_id=None,
    transport="cli",
)


# ---------------------------------------------------------------------------
# Slow path: judge hangs past the timeout → unavailable outcome, no exception
# ---------------------------------------------------------------------------
async def test_judge_timeout_returns_unavailable(monkeypatch):
    """When phase_judge.judge sleeps past the timeout, _judge_with_timeout must
    return a JudgeOutcome(available=False, ...) and must NOT propagate an
    asyncio.TimeoutError."""

    async def _slow_judge(**kwargs):
        await asyncio.sleep(1)  # longer than the monkeypatched timeout
        return JudgeOutcome(available=True, passed=True, warnings=[], feedback="")

    monkeypatch.setattr(phase_judge, "judge", _slow_judge)

    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "per_attempt_timeout_seconds", 0.01)

    outcome = await pipeline._judge_with_timeout(**_KWARGS)

    assert outcome.available is False
    assert outcome.passed is True
    assert any("TimeoutError" in w for w in outcome.warnings), outcome.warnings
    assert "judge-unavailable: TimeoutError" in outcome.warnings
    assert outcome.feedback == ""


# ---------------------------------------------------------------------------
# Fast path: judge returns normally → result passes straight through unchanged
# ---------------------------------------------------------------------------
async def test_judge_timeout_passthrough(monkeypatch):
    """When phase_judge.judge returns immediately, the outcome is forwarded as-is."""
    expected = JudgeOutcome(
        available=True, passed=True, warnings=[], feedback="", has_major=False
    )

    async def _fast_judge(**kwargs):
        return expected

    monkeypatch.setattr(phase_judge, "judge", _fast_judge)

    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "per_attempt_timeout_seconds", 5.0)

    outcome = await pipeline._judge_with_timeout(**_KWARGS)

    assert outcome is expected
