"""Pure (no-DB) coverage for ``credential_limiter.acquire``'s poll loop.

The real-Postgres bites-proofs — exact ceiling under a concurrent fleet, no
advisory lock, stale-slot takeover — live in
``tests/integration/test_credential_limiter.py`` and only run with
RUN_DB_INTEGRATION=1. These run everywhere, so the property that actually
wedged production (a single fleet-wide lock) stays guarded in a plain
``pytest tests/`` run too.

``_try_claim`` is the single seam between the loop and Postgres: it returns
``(slot_id_or_None, had_free_slot)``, and faking it exercises every branch of
the retry policy without a database.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import credential_limiter


@pytest.fixture
def no_sleep(monkeypatch):
    """Record inter-poll sleeps instead of serving them."""
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(credential_limiter.asyncio, "sleep", _fake_sleep)
    return slept


def _fake_claims(monkeypatch, outcomes):
    """Queue of ``(slot_id, had_free_slot)`` results for ``_try_claim``."""
    calls: list[tuple] = []
    queue = list(outcomes)

    async def _fake_try_claim(credential, limit, pc_id):
        calls.append((credential, limit, pc_id))
        return queue.pop(0) if queue else (None, False)

    monkeypatch.setattr(credential_limiter, "_try_claim", _fake_try_claim)
    return calls


# ── the regression guard: no fleet-wide lock, ever ────────────────────────


def test_acquire_statement_takes_no_advisory_lock():
    """The 2026-08 defect: `acquire` serialized every slot acquisition
    fleet-wide through `pg_advisory_xact_lock(hashtext(credential))` — 38
    hosts sharing one Gemini key all hash to the same lock. Measured: 75
    connections blocked on it, longest wait 822s, only 54 of a 900-slot
    ceiling in use.

    Mutual exclusion is now one lock per SLOT, implemented as the
    UNIQUE(credential, slot_index) index from migration 0060. Nothing in the
    acquire path may take a lock that every caller shares.
    """
    sql = str(credential_limiter._ACQUIRE_SQL)
    assert "pg_advisory" not in sql.lower(), (
        "acquire took an advisory lock again — that serializes the whole "
        "fleet through one lock; the unique slot index is the mechanism now"
    )
    # ...and the mechanism that replaced it is actually present.
    assert "ON CONFLICT (credential, slot_index)" in sql


def test_acquire_is_one_statement_per_claim():
    """One statement per attempt is what keeps the critical section entirely
    server-side. The old code needed BEGIN + lock + count + insert + COMMIT —
    four client round trips, all of them inside the lock."""
    sql = str(credential_limiter._ACQUIRE_SQL)
    assert sql.count(";") == 0, "the claim must stay a single statement"
    assert "MATERIALIZED" in sql, (
        "the candidate CTE must be MATERIALIZED so random() is drawn once "
        "and had_free_slot reports on the same draw the INSERT used"
    )


# ── retry policy ──────────────────────────────────────────────────────────


async def test_bypass_short_circuits_without_a_claim(monkeypatch):
    calls = _fake_claims(monkeypatch, [])
    assert await credential_limiter.acquire("c", 0, wait_budget_s=1.0) is credential_limiter.BYPASS
    assert await credential_limiter.acquire("c", None, wait_budget_s=1.0) is credential_limiter.BYPASS
    assert calls == []


async def test_first_claim_wins_immediately(monkeypatch, no_sleep):
    calls = _fake_claims(monkeypatch, [("slot-1", True)])
    got = await credential_limiter.acquire("cred", 8, wait_budget_s=30.0, pc_id="host:1")
    assert got == "slot-1"
    assert calls == [("cred", 8, "host:1")]
    assert no_sleep == [], "a winning first claim must not sleep"


async def test_saturated_credential_costs_exactly_one_claim_per_poll(
    monkeypatch, no_sleep
):
    """had_free_slot=False means the credential really is full. Retrying
    immediately would just hammer the DB, so we sleep — one statement per
    poll, exactly as before."""
    outcomes = [(None, False)] * 3 + [("slot-9", True)]
    calls = _fake_claims(monkeypatch, outcomes)

    got = await credential_limiter.acquire("cred", 2, wait_budget_s=30.0)

    assert got == "slot-9"
    assert len(calls) == 4, "one claim per poll while saturated"
    assert len(no_sleep) == 3, "one sleep between each poll"


async def test_lost_race_retries_immediately_without_sleeping(monkeypatch, no_sleep):
    """had_free_slot=True with no slot id means a free index existed and a
    concurrent acquirer took it first — capacity is almost certainly still
    there, so burning a full poll interval on it would be a pure latency
    tax on the common case."""
    calls = _fake_claims(monkeypatch, [(None, True), ("slot-2", True)])

    got = await credential_limiter.acquire("cred", 8, wait_budget_s=30.0)

    assert got == "slot-2"
    assert len(calls) == 2
    assert no_sleep == [], "a lost race must be retried without sleeping"


async def test_immediate_retries_are_bounded_per_poll(monkeypatch):
    """A permanently contended index must not spin: after
    _RACE_RETRIES_PER_POLL immediate retries the loop falls back to the
    normal backoff sleep instead of hammering the DB in a tight loop."""
    calls = _fake_claims(monkeypatch, [(None, True)] * 500)
    slept: list[float] = []

    class _Stop(Exception):
        pass

    async def _fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 3:
            raise _Stop()

    monkeypatch.setattr(credential_limiter.asyncio, "sleep", _fake_sleep)

    with pytest.raises(_Stop):
        await credential_limiter.acquire("cred", 8, wait_budget_s=600.0)

    per_poll = credential_limiter._RACE_RETRIES_PER_POLL + 1
    assert len(calls) == per_poll * len(slept), (
        f"expected {per_poll} claims per poll across {len(slept)} polls, "
        f"got {len(calls)}"
    )


async def test_budget_exhausted_returns_none(monkeypatch):
    """None (budget exhausted) is a different signal from BYPASS — the
    caller turns it into the 429-shaped slot-saturation error."""
    _fake_claims(monkeypatch, [])  # always (None, False)

    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(credential_limiter.asyncio, "sleep", _fast_sleep)

    got = await credential_limiter.acquire("cred", 4, wait_budget_s=-1.0)
    assert got is None
    assert got is not credential_limiter.BYPASS


async def test_backoff_decays_and_caps(monkeypatch):
    """Unchanged by the lock removal: 1s -> 2s -> 4s -> 5s (capped) + jitter
    in [0, 0.5)."""
    _fake_claims(monkeypatch, [])
    slept: list[float] = []

    class _Stop(Exception):
        pass

    async def _fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 5:
            raise _Stop()

    monkeypatch.setattr(credential_limiter.asyncio, "sleep", _fake_sleep)

    with pytest.raises(_Stop):
        await credential_limiter.acquire("cred", 1, wait_budget_s=600.0)

    for got, base in zip(slept, [1.0, 2.0, 4.0, 5.0, 5.0]):
        assert base <= got <= base + credential_limiter._POLL_JITTER_S + 1e-9
