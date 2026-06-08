from datetime import datetime, timedelta, timezone

from app.repositories.workers import is_online


def _t(seconds_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


def test_fresh_heartbeat_is_online():
    assert is_online(_t(10), stale_after_seconds=90) is True


def test_stale_heartbeat_is_offline():
    assert is_online(_t(120), stale_after_seconds=90) is False


def test_boundary_is_inclusive_online():
    assert is_online(_t(89), stale_after_seconds=90) is True


def test_none_heartbeat_is_offline():
    assert is_online(None, stale_after_seconds=90) is False
