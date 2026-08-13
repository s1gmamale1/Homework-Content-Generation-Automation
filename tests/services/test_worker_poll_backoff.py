"""The empty-claim backoff must be jittered, not a fixed period.

fleet-claim-fairness-1: a fleet of workers all sleeping exactly
``WORKER_POLL_INTERVAL`` after an empty claim polls in LOCKSTEP — their misses
cluster on the same instants, so the same workers keep losing the same races.
`empty_poll_backoff` spreads them over the window while keeping the mean poll
rate at the configured interval.
"""
from __future__ import annotations

import statistics

from app.services.worker import _EMPTY_POLL_JITTER, empty_poll_backoff


def test_backoff_stays_inside_the_jitter_band():
    lo, hi = _EMPTY_POLL_JITTER
    for _ in range(2000):
        got = empty_poll_backoff(2.0)
        assert 2.0 * lo <= got <= 2.0 * hi


def test_backoff_is_not_a_fixed_period():
    """The regression this guards: returning `poll_interval` verbatim."""
    draws = {empty_poll_backoff(2.0) for _ in range(200)}
    assert len(draws) > 100, "backoff is (near-)constant — the fleet stays in lockstep"


def test_mean_poll_rate_is_unchanged():
    """Jitter must decorrelate, not slow the fleet down: the mean stays at the
    configured interval (band is centred on 1.0x)."""
    mean = statistics.fmean(empty_poll_backoff(2.0) for _ in range(20000))
    assert abs(mean - 2.0) < 0.05, f"jitter shifted the mean poll rate to {mean:.3f}s"


def test_backoff_never_hot_spins_on_a_zero_interval():
    """A 0/negative configured interval must not turn the empty-queue path into
    a spin loop against Postgres."""
    for interval in (0.0, -1.0):
        assert empty_poll_backoff(interval) >= 0.05
