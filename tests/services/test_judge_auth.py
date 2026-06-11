"""Judge transport=api loud auth-fail vs cli graceful degrade.

An api job that hits an auth/401 error inside the judge must FAIL LOUDLY
(re-raise → job-level failure) rather than silently shipping unjudged via the
"judge-unavailable" swallow. A cli job keeps the existing graceful-degrade.
"""

import asyncio

import pytest

from app.services import agent
from app.services import phase_judge as pj

_AUTH_ERR = "api_error_status: 401 Invalid API key"


def _call_judge(transport: str):
    return asyncio.run(pj.judge(
        subject="biology", phase_name="case-based-preview", output_md="x",
        lesson_context=None, prior_outputs={},
        gen_provider="claude", gen_model="claude-sonnet-4-6",
        transport=transport,
    ))


def test_judge_api_reraises_on_auth_error(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError(_AUTH_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    with pytest.raises(RuntimeError, match="401"):
        _call_judge("api")


def test_judge_cli_degrades_on_auth_error(monkeypatch):
    """Same auth error under cli transport still degrades gracefully."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError(_AUTH_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    out = _call_judge("cli")
    assert out.available is False and out.passed is True
    assert out.warnings == ["judge-unavailable: RuntimeError"]


def test_judge_api_non_auth_error_still_degrades(monkeypatch):
    """An api job with a NON-auth error keeps the graceful degrade — only
    auth/401 errors are escalated to a loud failure."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError("CLI exploded for unrelated reasons")

    monkeypatch.setattr(agent, "run_phase", _boom)

    out = _call_judge("api")
    assert out.available is False and out.passed is True
