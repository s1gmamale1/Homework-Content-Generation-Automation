"""Tests for idempotency cache helpers in app/api/v1/jobs.py."""
import time
import uuid
import pytest

import app.api.v1.jobs as jobs_module
from app.api.v1.jobs import _idempotency_get, _idempotency_set, _IDEMPOTENCY_MAX_ENTRIES


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the in-process cache between tests."""
    jobs_module._IDEMPOTENCY_CACHE.clear()
    yield
    jobs_module._IDEMPOTENCY_CACHE.clear()


class TestIdempotencyGet:
    def test_returns_none_for_unknown_key(self):
        assert _idempotency_get("no-such-key") is None

    def test_returns_stored_job_id(self):
        job_id = uuid.uuid4()
        _idempotency_set("k1", job_id)
        assert _idempotency_get("k1") == job_id

    def test_expired_entry_returns_none(self):
        job_id = uuid.uuid4()
        # Inject an already-expired entry directly.
        jobs_module._IDEMPOTENCY_CACHE["expired-key"] = (job_id, time.time() - 1)
        assert _idempotency_get("expired-key") is None

    def test_expired_entry_removed_from_cache(self):
        job_id = uuid.uuid4()
        jobs_module._IDEMPOTENCY_CACHE["stale"] = (job_id, time.time() - 1)
        _idempotency_get("stale")
        assert "stale" not in jobs_module._IDEMPOTENCY_CACHE

    def test_live_entry_not_removed(self):
        job_id = uuid.uuid4()
        _idempotency_set("live", job_id)
        _idempotency_get("live")
        assert "live" in jobs_module._IDEMPOTENCY_CACHE


class TestIdempotencySet:
    def test_set_and_get_roundtrip(self):
        job_id = uuid.uuid4()
        _idempotency_set("roundtrip", job_id)
        assert _idempotency_get("roundtrip") == job_id

    def test_overwrite_existing_key(self):
        old_id = uuid.uuid4()
        new_id = uuid.uuid4()
        _idempotency_set("key", old_id)
        _idempotency_set("key", new_id)
        assert _idempotency_get("key") == new_id

    def test_eviction_when_at_limit(self):
        """When cache reaches _IDEMPOTENCY_MAX_ENTRIES, oldest 10% are evicted."""
        # Fill cache to exactly the limit with known timestamps.
        base_time = time.time()
        for i in range(_IDEMPOTENCY_MAX_ENTRIES):
            jobs_module._IDEMPOTENCY_CACHE[f"key-{i}"] = (
                uuid.uuid4(),
                base_time + i,  # ascending timestamps so key-0 is "oldest"
            )
        assert len(jobs_module._IDEMPOTENCY_CACHE) == _IDEMPOTENCY_MAX_ENTRIES

        # Adding one more should trigger eviction.
        _idempotency_set("new-key", uuid.uuid4())
        assert len(jobs_module._IDEMPOTENCY_CACHE) < _IDEMPOTENCY_MAX_ENTRIES + 1

    def test_evicted_entries_are_oldest(self):
        """After eviction, the oldest 10% should be gone."""
        base_time = time.time()
        for i in range(_IDEMPOTENCY_MAX_ENTRIES):
            jobs_module._IDEMPOTENCY_CACHE[f"key-{i}"] = (
                uuid.uuid4(),
                base_time + i,
            )
        _idempotency_set("new-key", uuid.uuid4())
        evict_count = _IDEMPOTENCY_MAX_ENTRIES // 10
        for i in range(evict_count):
            assert f"key-{i}" not in jobs_module._IDEMPOTENCY_CACHE
