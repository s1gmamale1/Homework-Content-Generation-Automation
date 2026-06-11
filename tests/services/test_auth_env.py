"""Unit tests for the per-call auth-env adapter (`agent._auth_env`) and the
`transport` threading through `_spawn`. Pure / DB-free / CLI-free (spec §4)."""

import pytest

from app.services import agent


def _base_env() -> dict[str, str]:
    return {
        "GEMINI_API_KEY": "g",
        "ANTHROPIC_API_KEY": "a",
        "PYTHONIOENCODING": "utf-8",
        "PATH": "/x",
    }


def test_gemini_cli_uses_gca_and_scrubs_keys():
    env = _base_env()
    result = agent._auth_env("gemini", "cli", env)
    assert result["GOOGLE_GENAI_USE_GCA"] == "true"
    assert "GEMINI_API_KEY" not in result
    assert "ANTHROPIC_API_KEY" not in result
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_claude_cli_scrubs_both_keys_no_gca():
    env = _base_env()
    result = agent._auth_env("claude", "cli", env)
    assert "GEMINI_API_KEY" not in result
    assert "ANTHROPIC_API_KEY" not in result
    assert "GOOGLE_GENAI_USE_GCA" not in result
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_gemini_api_keeps_only_gemini_key():
    env = _base_env()
    result = agent._auth_env("gemini", "api", env)
    assert result["GEMINI_API_KEY"] == "g"
    assert "ANTHROPIC_API_KEY" not in result
    assert "GOOGLE_GENAI_USE_GCA" not in result
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_claude_api_keeps_only_anthropic_key():
    env = _base_env()
    result = agent._auth_env("claude", "api", env)
    assert result["ANTHROPIC_API_KEY"] == "a"
    assert "GEMINI_API_KEY" not in result
    assert "GOOGLE_GENAI_USE_GCA" not in result
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_kimi_cli_scrubs_both_keys_preserves_path():
    env = _base_env()
    result = agent._auth_env("kimi", "cli", env)
    assert "GEMINI_API_KEY" not in result
    assert "ANTHROPIC_API_KEY" not in result
    assert result["PATH"] == "/x"
    assert result["PYTHONIOENCODING"] == "utf-8"


def test_claude_api_missing_key_raises():
    env = _base_env()
    del env["ANTHROPIC_API_KEY"]
    with pytest.raises(RuntimeError):
        agent._auth_env("claude", "api", env)


def test_gemini_api_missing_key_raises():
    env = _base_env()
    del env["GEMINI_API_KEY"]
    with pytest.raises(RuntimeError):
        agent._auth_env("gemini", "api", env)


def _vertex_env() -> dict[str, str]:
    """Worker configured for Vertex (service-account) gemini api — no AI-Studio key."""
    return {
        "ANTHROPIC_API_KEY": "a",
        "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/sa.json",
        "GOOGLE_CLOUD_PROJECT": "proj-1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": "/x",
    }


def test_gemini_api_vertex_fallback_when_no_key():
    """fleet-api-6: no GEMINI_API_KEY but SA creds present → Vertex mode.
    Verified against gemini-cli 0.46.0 getAuthTypeFromEnv (GCA > VERTEXAI > key)."""
    result = agent._auth_env("gemini", "api", _vertex_env())
    assert result["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert result["GOOGLE_APPLICATION_CREDENTIALS"] == "/secrets/sa.json"
    assert result["GOOGLE_CLOUD_PROJECT"] == "proj-1"
    # location defaults to global — us-central1 404s for this project class
    assert result["GOOGLE_CLOUD_LOCATION"] == "global"
    assert "GOOGLE_GENAI_USE_GCA" not in result
    assert "GEMINI_API_KEY" not in result
    assert "ANTHROPIC_API_KEY" not in result


def test_gemini_api_vertex_preserves_explicit_location():
    env = _vertex_env()
    env["GOOGLE_CLOUD_LOCATION"] = "europe-west1"
    result = agent._auth_env("gemini", "api", env)
    assert result["GOOGLE_CLOUD_LOCATION"] == "europe-west1"


def test_gemini_api_explicit_key_wins_over_vertex():
    """Deterministic precedence: an AI-Studio key beats SA creds when both exist."""
    env = _vertex_env()
    env["GEMINI_API_KEY"] = "g"
    result = agent._auth_env("gemini", "api", env)
    assert result["GEMINI_API_KEY"] == "g"
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in result


def test_gemini_api_neither_key_nor_vertex_raises():
    env = _vertex_env()
    del env["GOOGLE_APPLICATION_CREDENTIALS"]
    with pytest.raises(RuntimeError):
        agent._auth_env("gemini", "api", env)


def test_cli_scrubs_vertex_selector():
    """cli baseline must scrub GOOGLE_GENAI_USE_VERTEXAI so a vertex-configured
    worker's cli spawns stay on OAuth (GCA is checked first anyway — hygiene)."""
    env = _base_env()
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    result = agent._auth_env("gemini", "cli", env)
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in result
    assert result["GOOGLE_GENAI_USE_GCA"] == "true"


def test_claude_api_vertex_creds_do_not_satisfy_anthropic():
    env = _vertex_env()
    del env["ANTHROPIC_API_KEY"]
    with pytest.raises(RuntimeError):
        agent._auth_env("claude", "api", env)


def test_worker_has_api_keys_accepts_vertex_for_gemini():
    from app.services import worker

    key = {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"}
    vertex = {
        "ANTHROPIC_API_KEY": "a",
        "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
        "GOOGLE_CLOUD_PROJECT": "p",
    }
    assert worker._compute_has_api_keys(key) is True
    assert worker._compute_has_api_keys(vertex) is True
    assert worker._compute_has_api_keys({"ANTHROPIC_API_KEY": "a"}) is False
    assert worker._compute_has_api_keys({"GEMINI_API_KEY": "g"}) is False
    # half-configured vertex (creds without project) does NOT count
    half = {"ANTHROPIC_API_KEY": "a", "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json"}
    assert worker._compute_has_api_keys(half) is False


def test_does_not_mutate_base_env():
    env = _base_env()
    snapshot = dict(env)
    result = agent._auth_env("gemini", "cli", env)
    assert env == snapshot  # base untouched
    assert result is not env  # new dict


def test_run_phase_threads_transport_to_spawn(monkeypatch):
    """Threading proof at the `_spawn` seam: patch `_spawn` to capture kwargs,
    stub `_record_usage`, call `run_phase` with `transport="api"`, assert it
    reached `_spawn`. Kept DB-free."""
    captured: dict[str, object] = {}

    async def fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["provider_name"] = provider.name
        captured["transport"] = transport
        return 0, "ok body", {
            "prompt_tokens": 1,
            "output_tokens": 1,
            "cached_tokens": 0,
            "total_tokens": 2,
            "raw": {},
        }, ""

    async def fake_record_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_spawn", fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", fake_record_usage)

    import asyncio

    asyncio.run(
        agent.run_phase(
            provider="claude",
            model="claude-opus-4-8",
            phase_prompt="p",
            phase_name="test",
            homework_job_id=None,
            phase_output_id=None,
            transport="api",
        )
    )

    assert captured["provider_name"] == "claude"
    assert captured["transport"] == "api"
