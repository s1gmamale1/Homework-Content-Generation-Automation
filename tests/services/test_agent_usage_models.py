import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.repositories import agent_usage as au


def test_stats_by_provider_model_groups_and_sums_duration():
    # First execute() -> the GROUP BY aggregate rows; second -> (provider, model, duration) rows.
    agg = [
        SimpleNamespace(provider="claude", model_name="claude-opus-4-8", calls=2,
                        prompt_tokens=900, output_tokens=320, cached_tokens=300, success_count=2),
        SimpleNamespace(provider="claude", model_name="claude-haiku-4-5", calls=1,
                        prompt_tokens=300, output_tokens=90, cached_tokens=80, success_count=1),
    ]
    dur = [
        ("claude", "claude-opus-4-8", "1.5s"),
        ("claude", "claude-opus-4-8", "500ms"),
        ("claude", "claude-haiku-4-5", "2s"),
    ]
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: agg),
        SimpleNamespace(all=lambda: dur),
    ]))
    out = asyncio.run(au.stats_by_provider_model(session, since=datetime.now(timezone.utc)))

    opus = next(r for r in out if r["model_name"] == "claude-opus-4-8")
    assert opus["calls"] == 2
    assert opus["prompt_tokens"] == 900 and opus["output_tokens"] == 320 and opus["cached_tokens"] == 300
    assert opus["success_count"] == 2
    assert opus["duration_secs"] == 2.0   # 1.5s + 500ms
    haiku = next(r for r in out if r["model_name"] == "claude-haiku-4-5")
    assert haiku["duration_secs"] == 2.0


def test_stats_by_provider_transport_groups_by_auth_mode():
    # GROUP BY (provider, model_name, auth_mode) so callers can split cli vs api.
    agg = [
        SimpleNamespace(provider="claude", model_name="claude-opus-4-8", auth_mode="api",
                        calls=2, prompt_tokens=900, output_tokens=320, cached_tokens=300, success_count=2),
        SimpleNamespace(provider="claude", model_name="claude-opus-4-8", auth_mode="cli",
                        calls=1, prompt_tokens=100, output_tokens=40, cached_tokens=0, success_count=1),
    ]
    dur = [
        ("claude", "claude-opus-4-8", "api", "1.5s"),
        ("claude", "claude-opus-4-8", "cli", "2s"),
    ]
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: agg),
        SimpleNamespace(all=lambda: dur),
    ]))
    out = asyncio.run(au.stats_by_provider_transport(session, since=datetime.now(timezone.utc)))

    api = next(r for r in out if r["auth_mode"] == "api")
    assert api["provider"] == "claude" and api["model_name"] == "claude-opus-4-8"
    assert api["calls"] == 2 and api["prompt_tokens"] == 900 and api["output_tokens"] == 320
    assert api["cached_tokens"] == 300 and api["duration_secs"] == 1.5
    cli = next(r for r in out if r["auth_mode"] == "cli")
    assert cli["calls"] == 1 and cli["duration_secs"] == 2.0
