import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.db import _pool_config


def test_head_process_keeps_large_database_pool() -> None:
    assert _pool_config(worker_concurrency=0) == {
        "pool_size": 20,
        "max_overflow": 30,
    }


def test_worker_pool_default_is_the_safe_fleet_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DEFAULT stays 2+2 — the invariant the old 'does not scale' test
    protected. The worker pool is now operator-controlled, but a host that sets
    nothing must draw exactly what it drew before the setting existed: the
    fleet's connection budget (38 hosts, ~203 of max_connections=250) has no
    room for an accidental fleet-wide raise.
    """
    monkeypatch.delenv("WORKER_DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("WORKER_DB_MAX_OVERFLOW", raising=False)
    s = Settings(_env_file=None)
    assert s.worker_db_pool_size == 2
    assert s.worker_db_max_overflow == 2


def test_standalone_worker_database_pool_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_db_pool_size", 2)
    monkeypatch.setattr(settings, "worker_db_max_overflow", 2)
    assert _pool_config(worker_concurrency=1) == {
        "pool_size": 2,
        "max_overflow": 2,
    }


def test_worker_pool_is_operator_controlled_not_concurrency_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaces test_worker_pool_does_not_scale_with_pipeline_concurrency.

    The pool still never scales itself off ``worker_concurrency`` — that
    implicit multiplication is what would exhaust the shared Postgres. What
    changed is that an operator who has fixed the upstream ceiling (pgbouncer /
    a raised max_connections) can now size it explicitly, per host, via env.
    """
    monkeypatch.setattr(settings, "worker_db_pool_size", 10)
    monkeypatch.setattr(settings, "worker_db_max_overflow", 5)

    assert _pool_config(worker_concurrency=8) == {
        "pool_size": 10,
        "max_overflow": 5,
    }
    # Same env, any worker size: the setting decides, never the concurrency.
    assert _pool_config(worker_concurrency=1) == _pool_config(worker_concurrency=8)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"worker_db_pool_size": 0},      # SQLAlchemy: pool_size=0 == UNBOUNDED
        {"worker_db_pool_size": -1},
        {"worker_db_max_overflow": -1},  # SQLAlchemy: max_overflow=-1 == UNBOUNDED
    ],
)
def test_unbounded_pool_values_are_rejected(kwargs: dict[str, int]) -> None:
    """The two values SQLAlchemy reads as "no limit" are exactly the fleet-fatal
    case this budget exists to prevent, so they fail at startup, not in prod."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)
