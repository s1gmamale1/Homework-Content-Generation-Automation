import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.repositories import agent_usage as au


def test_series_by_window_buckets_rows():
    now = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
    since = now - timedelta(hours=12)  # 12 buckets of 1h each

    def row(h, dur, p, o, c, ok):
        return (since + timedelta(hours=h), dur, p, o, c, ok)

    rows = [
        row(0.5, "1s", 10, 5, 2, True),
        row(0.6, "2s", 0, 0, 0, False),   # same bucket 0
        row(11.5, "500ms", 100, 50, 0, True),  # bucket 11
    ]
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    )

    s = asyncio.run(au.series_by_window(session, since=since, now=now, buckets=12))

    assert len(s["calls"]) == 12
    assert s["calls"][0] == 2 and s["calls"][11] == 1
    assert s["tokens"][0] == 17 and s["tokens"][11] == 150
    assert s["success_pct"][0] == 50.0       # 1 of 2 ok
    assert s["duration_secs"][0] == 3.0      # 1s + 2s
    assert sum(s["calls"]) == 3
