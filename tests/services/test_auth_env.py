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


def test_compute_capabilities_anthropic_only_env():
    """Task 4: credential-only shape — {can_claude_api, can_gemini_api}.
    anthropic-only worker: claude side up, gemini side down."""
    from app.services import worker

    caps = worker._compute_capabilities({"ANTHROPIC_API_KEY": "a"})
    assert caps["can_claude_api"] is True
    assert caps["can_gemini_api"] is False
    assert set(caps.keys()) == {"can_claude_api", "can_gemini_api"}


def test_compute_capabilities_gemini_vertex_only_env():
    """gemini-only via Vertex SA pair (fleet-api-6): claude unreachable, gemini up."""
    from app.services import worker

    caps = worker._compute_capabilities(
        {"GOOGLE_APPLICATION_CREDENTIALS": "/sa.json", "GOOGLE_CLOUD_PROJECT": "p"},
    )
    assert caps["can_claude_api"] is False
    assert caps["can_gemini_api"] is True


def test_compute_capabilities_half_vertex_does_not_count():
    """Half-configured Vertex (creds without project) is NOT a gemini capability."""
    from app.services import worker

    caps = worker._compute_capabilities({"GOOGLE_APPLICATION_CREDENTIALS": "/sa.json"})
    assert caps["can_gemini_api"] is False


def test_claude_api_missing_key_raises_typed_auth_env_error():
    """Phase 4.1 §5a: credential mispredictions must raise the TYPED
    AuthEnvError so auth classification is isinstance-based, not substring."""
    env = _base_env()
    del env["ANTHROPIC_API_KEY"]
    with pytest.raises(agent.AuthEnvError):
        agent._auth_env("claude", "api", env)


def test_gemini_api_neither_key_nor_vertex_raises_typed_auth_env_error():
    env = _vertex_env()
    del env["GOOGLE_APPLICATION_CREDENTIALS"]
    with pytest.raises(agent.AuthEnvError):
        agent._auth_env("gemini", "api", env)


def test_api_unsupported_provider_raises_typed_auth_env_error():
    with pytest.raises(agent.AuthEnvError):
        agent._auth_env("kimi", "api", _base_env())


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


# ─── GCP project/SA leak into cli spawns (the 2-PC-test 403, 2026-06-12) ────
# GOOGLE_CLOUD_PROJECT does NOT select an auth type, but it RE-SCOPES the
# OAuth/Code-Assist call to that GCP project — 403 ("Cloud Code Private API
# has not been used in project …") when the project lacks the API. Proven
# live: bare gemini OK; same call + GOOGLE_CLOUD_PROJECT=dummy → exit 403.
# The cli baseline must scrub ALL Vertex/GCP vars; only the api Vertex
# branch re-grants them.

def _sa_env() -> dict[str, str]:
    return {
        "ANTHROPIC_API_KEY": "a",
        "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-vertex-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
        "PYTHONIOENCODING": "utf-8",
    }


def test_gemini_cli_scrubs_gcp_project_and_sa_vars():
    result = agent._auth_env("gemini", "cli", _sa_env())
    assert "GOOGLE_CLOUD_PROJECT" not in result
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in result
    assert "GOOGLE_CLOUD_LOCATION" not in result
    assert result["GOOGLE_GENAI_USE_GCA"] == "true"


def test_nongemini_cli_scrubs_gcp_project_too():
    result = agent._auth_env("kimi", "cli", _sa_env())
    assert "GOOGLE_CLOUD_PROJECT" not in result
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in result


def test_gemini_api_key_mode_scrubs_sa_vars():
    env = {**_sa_env(), "GEMINI_API_KEY": "g"}
    result = agent._auth_env("gemini", "api", env)
    assert result["GEMINI_API_KEY"] == "g"
    assert "GOOGLE_CLOUD_PROJECT" not in result
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in result


def test_gemini_api_vertex_branch_regrants_sa_vars():
    result = agent._auth_env("gemini", "api", _sa_env())
    assert result["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert result["GOOGLE_APPLICATION_CREDENTIALS"] == "/sa.json"
    assert result["GOOGLE_CLOUD_PROJECT"] == "my-vertex-project"
    # explicit location from the operator's env is preserved
    assert result["GOOGLE_CLOUD_LOCATION"] == "us-central1"


def test_gemini_api_vertex_branch_defaults_location_global():
    env = _sa_env()
    del env["GOOGLE_CLOUD_LOCATION"]
    result = agent._auth_env("gemini", "api", env)
    assert result["GOOGLE_CLOUD_LOCATION"] == "global"


def test_compute_capabilities_credential_shape_is_exactly_two_keys():
    """Task 4: _compute_capabilities returns ONLY can_claude_api + can_gemini_api.
    Per-role keys (judge_api_ok, judge_fallback_api_ok, extract_api_ok, judge_pair,
    settings_judge_provider, settings_extract_provider) are gone — the claim gate
    now reads these from stamped job columns, not the worker capabilities dict.

    BITE: adding any extra key breaks this assertion.
    """
    from app.services import worker

    caps = worker._compute_capabilities({"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"})
    assert set(caps.keys()) == {"can_claude_api", "can_gemini_api"}, (
        f"_compute_capabilities must return exactly 2 keys; got {set(caps.keys())}"
    )
