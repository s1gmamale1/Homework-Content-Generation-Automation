import asyncio
from unittest.mock import AsyncMock, patch

import app.api.v1.jobs as jobs_mod


def test_get_agent_stats_nests_models_with_computed_success_pct():
    prov_rows = [{
        "provider": "claude", "calls": 3, "duration_secs": 2.0,
        "prompt_tokens": 1200, "output_tokens": 410, "cached_tokens": 380,
        "success_count": 3,
    }]
    model_rows = [
        {"provider": "claude", "model_name": "claude-opus-4-8", "calls": 4,
         "duration_secs": 1.5, "prompt_tokens": 900, "output_tokens": 320,
         "cached_tokens": 300, "success_count": 3},
        {"provider": "claude", "model_name": None, "calls": 1,
         "duration_secs": 0.5, "prompt_tokens": 10, "output_tokens": 5,
         "cached_tokens": 0, "success_count": 1},
    ]
    series = {"calls": [], "tokens": [], "duration_secs": [], "success_pct": []}
    with patch.object(jobs_mod.agent_usage_repo, "stats_by_provider",
                      AsyncMock(return_value=prov_rows)), \
         patch.object(jobs_mod.agent_usage_repo, "stats_by_provider_model",
                      AsyncMock(return_value=model_rows)), \
         patch.object(jobs_mod.agent_usage_repo, "series_by_window",
                      AsyncMock(return_value=series)):
        resp = asyncio.run(jobs_mod.get_agent_stats(session=None))

    claude_24h = resp["providers"]["claude"]["24h"]
    assert "models" in claude_24h
    names = {m["model_name"] for m in claude_24h["models"]}
    assert "claude-opus-4-8" in names
    assert "(default)" in names   # NULL model_name bucketed
    opus = next(m for m in claude_24h["models"] if m["model_name"] == "claude-opus-4-8")
    assert opus["success_pct"] == 75.0   # 3/4, computed from success_count
