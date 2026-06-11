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
