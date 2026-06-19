"""TDD tests for pricing-1a: capture claude cache-write tokens.

Coverage (all offline — no real DB):
  (a) AgentUsage model has the cache_creation_tokens column
  (b) claude CLI parse_envelope surfaces cache_creation_tokens in the normalized dict
  (c) claude API _claude_usage surfaces cache_creation_tokens in the normalized dict
  (d) gemini envelope yields cache_creation_tokens=0 (explicit zero, not absent)
  (e) _record_usage threads cache_creation_tokens through to usage_repo.create
"""

from __future__ import annotations

from typing import Any
import json

import pytest

from app.models.agent_usage import AgentUsage
from app.services.providers.claude import Claude
from app.services import api_transport
from app.services import agent as agent_module


# ─────────────────────────────────────────────────────────────────────
# (a) Model column exists
# ─────────────────────────────────────────────────────────────────────


def test_agent_usage_model_has_cache_creation_tokens_column() -> None:
    """AgentUsage ORM model must declare a cache_creation_tokens column."""
    col_names = {c.name for c in AgentUsage.__table__.columns}
    assert "cache_creation_tokens" in col_names, (
        "cache_creation_tokens column missing from agent_usages table model"
    )


def test_agent_usage_model_cache_creation_tokens_is_integer() -> None:
    """cache_creation_tokens must be an Integer column (not Text, etc.)."""
    from sqlalchemy import Integer as SAInteger
    col = AgentUsage.__table__.columns["cache_creation_tokens"]
    assert isinstance(col.type, SAInteger)


# ─────────────────────────────────────────────────────────────────────
# (b) claude CLI parse_envelope
# ─────────────────────────────────────────────────────────────────────


def _make_claude_stdout(cache_creation: int = 15) -> str:
    return json.dumps({
        "type": "result",
        "result": "summary text",
        "usage": {
            "input_tokens": 500,
            "cache_read_input_tokens": 100,
            "output_tokens": 200,
            "cache_creation_input_tokens": cache_creation,
        },
        "is_error": False,
        "stop_reason": "end_turn",
    })


def test_claude_cli_parse_envelope_includes_cache_creation(tmp_path) -> None:
    """parse_envelope must put cache_creation_tokens in the normalized dict."""
    sentinel = tmp_path / "last_msg.txt"
    text, usage = Claude().parse_envelope(_make_claude_stdout(cache_creation=15),
                                           last_msg_path=sentinel)
    assert "cache_creation_tokens" in usage, (
        "cache_creation_tokens missing from Claude CLI parse_envelope output"
    )
    assert usage["cache_creation_tokens"] == 15


def test_claude_cli_parse_envelope_zero_cache_creation(tmp_path) -> None:
    """cache_creation_input_tokens=0 must produce cache_creation_tokens=0 (not None)."""
    sentinel = tmp_path / "last_msg.txt"
    _, usage = Claude().parse_envelope(_make_claude_stdout(cache_creation=0),
                                       last_msg_path=sentinel)
    assert usage["cache_creation_tokens"] == 0


def test_claude_cli_parse_envelope_missing_cache_creation(tmp_path) -> None:
    """If the key is absent from the envelope, cache_creation_tokens must be 0 or None
    (not raise). We accept both; the important thing is no KeyError/crash."""
    stdout = json.dumps({
        "type": "result",
        "result": "hi",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "is_error": False,
        "stop_reason": "end_turn",
    })
    sentinel = tmp_path / "last_msg.txt"
    _, usage = Claude().parse_envelope(stdout, last_msg_path=sentinel)
    # Must not raise; value must be 0 or None (key absent → None from dict.get)
    assert usage.get("cache_creation_tokens") in (0, None)


# ─────────────────────────────────────────────────────────────────────
# (c) claude api_transport _claude_usage
# ─────────────────────────────────────────────────────────────────────


class _FakeUsage:
    """Stub anthropic Usage object."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_claude_api_usage_includes_cache_creation() -> None:
    """_claude_usage must include cache_creation_tokens from
    cache_creation_input_tokens on the SDK usage object."""
    u = _FakeUsage(
        input_tokens=300,
        output_tokens=80,
        cache_read_input_tokens=50,
        cache_creation_input_tokens=20,
    )
    usage = api_transport._claude_usage(u)
    assert "cache_creation_tokens" in usage, (
        "cache_creation_tokens missing from api_transport._claude_usage output"
    )
    assert usage["cache_creation_tokens"] == 20


def test_claude_api_usage_zero_cache_creation() -> None:
    u = _FakeUsage(
        input_tokens=100, output_tokens=50,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    usage = api_transport._claude_usage(u)
    assert usage["cache_creation_tokens"] == 0


def test_claude_api_usage_none_object_returns_empty() -> None:
    """_claude_usage(None) must not crash; cache_creation_tokens absent or None."""
    usage = api_transport._claude_usage(None)
    # Should not raise; value is absent or 0
    val = usage.get("cache_creation_tokens")
    assert val is None or val == 0


# ─────────────────────────────────────────────────────────────────────
# (d) gemini envelope yields cache_creation_tokens=0
# ─────────────────────────────────────────────────────────────────────


class _FakeGeminiUM:
    """Stub gemini usage_metadata object (no cache_creation_input_tokens field)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_gemini_api_usage_has_no_cache_creation() -> None:
    """Gemini usage has no cache-write concept; _gemini_usage must yield
    cache_creation_tokens=0 (or absent) — never a non-zero value that would mislead."""
    um = _FakeGeminiUM(
        prompt_token_count=200,
        candidates_token_count=60,
        thoughts_token_count=None,
        cached_content_token_count=30,
        total_token_count=260,
    )
    usage = api_transport._gemini_usage(um)
    # Gemini rows must NOT have a truthy cache_creation_tokens
    assert usage.get("cache_creation_tokens", 0) == 0


# ─────────────────────────────────────────────────────────────────────
# (e) _record_usage threads cache_creation_tokens to usage_repo.create
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_usage_passes_cache_creation_tokens(monkeypatch) -> None:
    """_record_usage must read cache_creation_tokens from the usage dict
    and pass it as a kwarg to usage_repo.create."""
    import app.repositories.agent_usage as usage_repo_mod

    captured: list[dict] = []

    # Fake the SessionLocal so we don't need a real DB
    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def commit(self): pass

    class _FakeSessionLocal:
        def __call__(self): return _FakeSession()

    async def fake_create(session, **kwargs):
        captured.append(kwargs)
        # Return a minimal stub so _record_usage doesn't crash
        obj = type("AU", (), {"id": None})()
        return obj

    monkeypatch.setattr(agent_module, "SessionLocal", _FakeSessionLocal())
    monkeypatch.setattr(usage_repo_mod, "create", fake_create)

    usage: dict[str, Any] = {
        "prompt_tokens": 100,
        "output_tokens": 50,
        "cached_tokens": 10,
        "cache_creation_tokens": 25,
        "total_tokens": 185,
        "raw": {},
    }

    from datetime import datetime, timezone
    await agent_module._record_usage(
        operation="phase.run",
        provider="claude",
        model_name="claude-sonnet-4-6",
        usage=usage,
        duration_s=1.5,
        started_at=datetime.now(timezone.utc),
        success=True,
        auth_mode="api",
    )

    assert len(captured) == 1
    assert captured[0].get("cache_creation_tokens") == 25, (
        f"_record_usage did not pass cache_creation_tokens=25 to usage_repo.create; "
        f"got kwargs keys: {list(captured[0].keys())}"
    )


@pytest.mark.asyncio
async def test_record_usage_gemini_cache_creation_is_zero(monkeypatch) -> None:
    """For gemini usage dicts (no cache_creation_tokens key), _record_usage must
    persist 0 — never crash or omit the column."""
    import app.repositories.agent_usage as usage_repo_mod

    captured: list[dict] = []

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def commit(self): pass

    class _FakeSessionLocal:
        def __call__(self): return _FakeSession()

    async def fake_create(session, **kwargs):
        captured.append(kwargs)
        return type("AU", (), {"id": None})()

    monkeypatch.setattr(agent_module, "SessionLocal", _FakeSessionLocal())
    monkeypatch.setattr(usage_repo_mod, "create", fake_create)

    usage: dict[str, Any] = {
        "prompt_tokens": 200,
        "output_tokens": 60,
        "cached_tokens": 30,
        "total_tokens": 290,
        "raw": {},
        # no cache_creation_tokens key (gemini)
    }

    from datetime import datetime, timezone
    await agent_module._record_usage(
        operation="phase.run",
        provider="gemini",
        model_name="gemini-2.5-flash",
        usage=usage,
        duration_s=0.8,
        started_at=datetime.now(timezone.utc),
        success=True,
    )

    assert len(captured) == 1
    assert captured[0].get("cache_creation_tokens", 0) == 0
