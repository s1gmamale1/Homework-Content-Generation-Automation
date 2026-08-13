"""Unit tests for ``app.services.credential_limiter.resolve_limit`` (BE-16
task 4). Pure — a fake session stands in for a real ``AsyncSession`` so
these never touch Postgres (the real-DB bites-proofs for the MIN-over-
duplicate-project-rows behavior live in
``tests/integration/test_credential_limiter.py``).

Coverage:
- fingerprint-form credentials (gemini API-key, claude, clodex) always
  resolve the provider env default, never touching the DB for claude/clodex
  and returning the default when the DB has no matching project for gemini.
- a project-shaped gemini credential picks up a `sa_keys.max_concurrent_calls`
  override when the fake session reports one.
- the ~60s TTL cache: a second call within the window returns the cached
  value even if the underlying "row" changed; advancing the monotonic clock
  past the TTL re-queries and picks up the new value.
- a DB error resolves to the provider default for that call only and is
  NEVER cached — the very next call re-queries.
- ``Settings`` rejects negative/zero-violating values via `Field(ge=...)`
  (pydantic `ValidationError`), the pure guard behind the DB-level CHECK.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.services import credential_limiter


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Duck-types the one ``AsyncSession`` method ``resolve_limit`` calls.

    ``values`` is a queue: each ``execute()`` call pops the next entry —
    an int/None to return as the MIN() result, or an Exception instance to
    raise instead.
    """

    def __init__(self, values: list):
        self._values = list(values)
        self.call_count = 0

    async def execute(self, *_args, **_kwargs):
        self.call_count += 1
        v = self._values.pop(0)
        if isinstance(v, BaseException):
            raise v
        return _FakeResult(v)


@pytest.fixture(autouse=True)
def _clear_cache():
    credential_limiter.clear_limit_cache()
    yield
    credential_limiter.clear_limit_cache()


async def test_claude_credential_resolves_default_without_touching_db():
    session = _FakeSession([])  # empty queue -> AssertionError-via-IndexError if touched
    limit = await credential_limiter.resolve_limit(session, "claude", "claude:deadbeefcafebabe")
    assert limit == settings.credential_max_concurrent_claude
    assert session.call_count == 0


async def test_clodex_credential_resolves_default_without_touching_db():
    session = _FakeSession([])
    limit = await credential_limiter.resolve_limit(session, "clodex", "clodex:deadbeefcafebabe")
    assert limit == settings.credential_max_concurrent_clodex
    assert session.call_count == 0


async def test_gemini_fingerprint_form_resolves_default_no_matching_project():
    # A gemini API-key fingerprint (`gemini:{sha256[:16]}`) is still
    # project-shaped by prefix, so resolve_limit DOES query — but no
    # sa_keys.project_id will ever equal a hex digest, so the query reports
    # no override (None) and the provider default wins.
    session = _FakeSession([None])
    limit = await credential_limiter.resolve_limit(
        session, "gemini", "gemini:deadbeefcafebabe"
    )
    assert limit == settings.credential_max_concurrent_gemini
    assert session.call_count == 1


async def test_gemini_project_credential_picks_up_override():
    session = _FakeSession([3])
    limit = await credential_limiter.resolve_limit(session, "gemini", "gemini:my-project")
    assert limit == 3


async def test_ttl_cache_returns_stale_value_until_expiry_then_refreshes():
    session = _FakeSession([3, 7])

    first = await credential_limiter.resolve_limit(session, "gemini", "gemini:cache-project")
    assert first == 3

    # Still within the TTL window — cached, no second DB call yet even
    # though the queue holds a different value.
    second = await credential_limiter.resolve_limit(session, "gemini", "gemini:cache-project")
    assert second == 3
    assert session.call_count == 1

    # Advance the monotonic clock past the TTL — cache entry now expired.
    real_now = credential_limiter.time.monotonic()
    original_monotonic = credential_limiter.time.monotonic
    credential_limiter.time.monotonic = (
        lambda: real_now + credential_limiter._LIMIT_CACHE_TTL_SECONDS + 1
    )
    try:
        third = await credential_limiter.resolve_limit(session, "gemini", "gemini:cache-project")
    finally:
        credential_limiter.time.monotonic = original_monotonic

    assert third == 7
    assert session.call_count == 2


async def test_db_error_resolves_default_and_is_never_cached():
    boom = RuntimeError("db down")
    session = _FakeSession([boom, 5])

    first = await credential_limiter.resolve_limit(session, "gemini", "gemini:err-project")
    assert first == settings.credential_max_concurrent_gemini

    # Not cached — the second call re-queries and picks up the real value.
    second = await credential_limiter.resolve_limit(session, "gemini", "gemini:err-project")
    assert second == 5
    assert session.call_count == 2


def test_settings_reject_negative_concurrency_defaults():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://x/y",
            _env_file=None,
            credential_max_concurrent_gemini=-1,
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://x/y",
            _env_file=None,
            credential_max_concurrent_claude=-1,
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://x/y",
            _env_file=None,
            credential_max_concurrent_clodex=-1,
        )


def test_settings_reject_sub_one_slot_wait_seconds():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+asyncpg://x/y",
            _env_file=None,
            credential_slot_wait_seconds=0,
        )


def test_settings_defaults(monkeypatch):
    # `_env_file=None` is NOT enough to see the code defaults: app.config calls
    # `load_dotenv(override=False)` at import, so the operator's .env is already in
    # os.environ by the time any test runs, and pydantic-settings reads it from there.
    # Without this the test asserts the deployment's config rather than the default and
    # fails on any configured host (seen: a fleet head with
    # CREDENTIAL_MAX_CONCURRENT_GEMINI=32 turned this into a permanent red).
    for var in (
        "CREDENTIAL_MAX_CONCURRENT_GEMINI",
        "CREDENTIAL_MAX_CONCURRENT_CLAUDE",
        "CREDENTIAL_MAX_CONCURRENT_CLODEX",
        "CREDENTIAL_SLOT_WAIT_SECONDS",
        "PER_ATTEMPT_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.credential_max_concurrent_gemini == 8
    assert s.credential_max_concurrent_claude == 8
    assert s.credential_max_concurrent_clodex == 8
    assert s.credential_slot_wait_seconds == 120
    # Deliberately far below the per-attempt hang timeout — see config.py
    # comment: the pipeline's outer wait_for at ~per_attempt_timeout_seconds
    # would otherwise cancel the slot-wait before the 429-shaped path fires.
    assert s.credential_slot_wait_seconds < s.per_attempt_timeout_seconds
