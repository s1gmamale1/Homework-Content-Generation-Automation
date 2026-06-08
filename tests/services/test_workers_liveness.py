from datetime import datetime, timedelta, timezone

from app.repositories.workers import is_online


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _t(seconds_ago: float) -> datetime:
    return _now() - timedelta(seconds=seconds_ago)


def test_fresh_heartbeat_is_online():
    assert is_online(_t(10), now=_now(), stale_after_seconds=90) is True


def test_stale_heartbeat_is_offline():
    assert is_online(_t(120), now=_now(), stale_after_seconds=90) is False


def test_boundary_is_inclusive_online():
    ref = _now()
    assert is_online(ref - timedelta(seconds=89), now=ref, stale_after_seconds=90) is True


def test_none_heartbeat_is_offline():
    assert is_online(None, now=_now(), stale_after_seconds=90) is False
