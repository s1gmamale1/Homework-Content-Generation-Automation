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
