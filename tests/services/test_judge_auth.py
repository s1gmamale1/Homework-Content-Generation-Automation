"""Judge transport=api loud auth-fail vs cli graceful degrade.

An api job that hits an auth/401 error inside the judge must FAIL LOUDLY
(re-raise → job-level failure) rather than silently shipping unjudged via the
"judge-unavailable" swallow. A cli job keeps the existing graceful-degrade.
"""

import asyncio

import pytest

from app.services import agent
from app.services import model_tiers as mt
from app.services import phase_judge as pj

_AUTH_ERR = "api_error_status: 401 Invalid API key"


def _call_judge(transport: str):
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    return asyncio.run(pj.judge(
        subject="biology", phase_name="case-based-preview", output_md="x",
        lesson_context=None, prior_outputs={},
        gen_provider="claude", gen_model="claude-sonnet-4-6",
        judge_provider=jp, judge_model=jm,
        transport=transport,
    ))


def test_judge_api_reraises_on_auth_error(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p, **kw: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError(_AUTH_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    with pytest.raises(RuntimeError, match="401"):
        _call_judge("api")


def test_judge_cli_degrades_on_auth_error(monkeypatch):
    """Same auth error under cli transport still degrades gracefully."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p, **kw: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError(_AUTH_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    out = _call_judge("cli")
    assert out.available is False and out.passed is True
    assert out.warnings == ["judge-unavailable: RuntimeError"]


def test_judge_api_non_auth_error_still_degrades(monkeypatch):
    """An api job with a NON-auth error keeps the graceful degrade — only
    auth/401 errors are escalated to a loud failure."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p, **kw: "CONTRACT")

    async def _boom(**kwargs):
        raise RuntimeError("CLI exploded for unrelated reasons")

    monkeypatch.setattr(agent, "run_phase", _boom)

    out = _call_judge("api")
    assert out.available is False and out.passed is True


# Raise message from agent._auth_env for the gemini credential misprediction —
# deliberately contains NO _AUTH_SIGNALS substring; only the AuthEnvError TYPE
# can classify it (Phase 4.1 §5a).
_AUTH_ENV_ERR = (
    "transport=api for gemini but GEMINI_API_KEY is unset/empty "
    "and no Vertex service account is configured"
)


def test_judge_api_reraises_on_auth_env_error(monkeypatch):
    """A typed AuthEnvError whose message matches no signal must still be
    classified as an auth error (isinstance, not substring luck) and re-raise
    under transport=api."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p, **kw: "CONTRACT")

    async def _boom(**kwargs):
        raise agent.AuthEnvError(_AUTH_ENV_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    with pytest.raises(agent.AuthEnvError, match="GEMINI_API_KEY"):
        _call_judge("api")


def test_judge_cli_degrades_on_auth_env_error(monkeypatch):
    """The SAME typed error under cli transport keeps the graceful degrade."""
    monkeypatch.setattr(pj, "get_prompt", lambda s, p, **kw: "CONTRACT")

    async def _boom(**kwargs):
        raise agent.AuthEnvError(_AUTH_ENV_ERR)

    monkeypatch.setattr(agent, "run_phase", _boom)

    out = _call_judge("cli")
    assert out.available is False and out.passed is True
    assert out.warnings == ["judge-unavailable: AuthEnvError"]


def test_is_auth_error_covers_vertex_and_gemini_shapes():
    """fleet-api-6 introduced Vertex/gemini auth-failure shapes that the
    original signals ('401' / 'invalid api key' / 'unauthorized') miss."""
    from app.services.phase_judge import _is_auth_error

    for msg in (
        "Error: PERMISSION_DENIED: caller lacks permission",      # vertex 403
        "status: 403, reason: permission denied on resource",
        "API key not valid. Please pass a valid API key.",        # gemini AIza
        "invalid_grant: account not found",                       # SA token mint
        "UNAUTHENTICATED: request had invalid credentials",
    ):
        assert _is_auth_error(RuntimeError(msg)), msg
    # benign errors must NOT match (a false positive fails an api job loudly)
    assert not _is_auth_error(RuntimeError("CLI exploded: connection reset"))
    assert not _is_auth_error(RuntimeError("model produced no parsed Verdict"))
