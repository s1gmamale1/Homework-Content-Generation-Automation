from app.config import Settings


def test_resilience_defaults_and_invariants():
    # _env_file=None isolates from the local .env (mirrors the notion-config lesson).
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    # Heartbeat MUST be well below the lease TTL, else a live job's claim goes stale.
    assert s.heartbeat_seconds < s.reclaim_stale_seconds
    # per-attempt timeout bounds a hung CLI (e.g. opencode) — must be positive.
    assert s.per_attempt_timeout_seconds > 0
    # claude is reserved for the user's Max allocation — never a fallback target.
    assert "claude" not in s.failover_provider_order
    assert s.failover_provider_order  # non-empty
