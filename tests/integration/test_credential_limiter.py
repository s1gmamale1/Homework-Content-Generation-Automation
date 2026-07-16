"""Real-DB bites-proofs for the fleet-wide per-credential api concurrency
limiter (BE-16 task 3). RUN_DB_INTEGRATION=1 against a scratch DB
(edu_scratch_credlim) — pin 127.0.0.1 (see scratch-db-localhost-dual-server
trap in memory: localhost can resolve to a DIFFERENT server via IPv6).

Each concurrent-acquirer task opens its OWN SessionLocal()/connection
(never share one session across the two acquires) — the advisory xact lock
would otherwise deadlock against itself within a single session/tx.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


def _cred(tag: str) -> str:
    """A fresh, never-reused credential fingerprint per test so tests never
    contend with leftover slot rows from other tests/runs."""
    return f"test-cred-{tag}-{uuid.uuid4().hex[:12]}"


async def _count_rows(cred: str) -> int:
    from app.db import SessionLocal

    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT count(*) FROM credential_slots WHERE credential = :cred"),
            {"cred": cred},
        )
        return result.scalar_one()


async def test_limit_one_two_concurrent_acquires_one_wins_second_waits_then_wins():
    from app.services import credential_limiter

    cred = _cred("concurrent")
    results: dict[str, object] = {}

    async def worker(name: str) -> object:
        slot = await credential_limiter.acquire(cred, 1, wait_budget_s=5.0)
        results[name] = slot
        return slot

    task_a = asyncio.create_task(worker("a"))
    task_b = asyncio.create_task(worker("b"))

    # Give both an initial attempt: one wins immediately, the other blocks on
    # the advisory lock then fails its count check and enters the poll loop.
    await asyncio.sleep(0.3)

    done_names = [n for n in ("a", "b") if n in results and results[n] is not None]
    assert len(done_names) == 1, f"expected exactly one winner so far, got {results}"
    winner = done_names[0]
    loser = "b" if winner == "a" else "a"
    loser_task = task_b if loser == "b" else task_a

    assert not loser_task.done(), "the second acquirer must still be waiting"

    # Release the winner's slot — the loser should pick it up on its next poll.
    await credential_limiter.release(results[winner])

    loser_slot = await asyncio.wait_for(loser_task, timeout=5.0)
    assert loser_slot is not None, "second acquirer must win after release"
    assert results[loser] == loser_slot

    await credential_limiter.release(loser_slot)
    assert await _count_rows(cred) == 0


async def test_second_acquirer_times_out_with_small_wait_budget():
    from app.services import credential_limiter

    cred = _cred("timeout")

    slot1 = await credential_limiter.acquire(cred, 1, wait_budget_s=5.0)
    assert slot1 is not None

    result2 = await credential_limiter.acquire(cred, 1, wait_budget_s=0.5)
    assert result2 is None

    await credential_limiter.release(slot1)
    assert await _count_rows(cred) == 0


async def test_stale_row_does_not_count_and_sweep_deletes_it():
    from app.db import SessionLocal
    from app.services import credential_limiter

    cred = _cred("stale")
    ttl = credential_limiter.STALE_TTL_SECONDS

    # Backdate a slot row well past the TTL — a stale slot from a worker that
    # crashed without releasing.
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO credential_slots (credential, pc_id, acquired_at) "
                    "VALUES (:cred, 'stale-host:1', now() - make_interval(secs => :backdate))"
                ),
                {"cred": cred, "backdate": ttl + 300},
            )

    # limit=1 but the only existing row is stale — a fresh acquire must
    # still succeed (the stale row must not count against the limit).
    slot = await credential_limiter.acquire(cred, 1, wait_budget_s=2.0)
    assert slot is not None

    await credential_limiter.release(slot)

    # release() only removed the fresh row — the original stale row is still
    # sitting there (nothing but sweep/explicit-delete touches stale rows).
    assert await _count_rows(cred) == 1

    deleted = await credential_limiter.sweep()
    assert deleted >= 1
    assert await _count_rows(cred) == 0


async def test_limit_zero_or_none_returns_bypass_sentinel_without_touching_db():
    from app.services import credential_limiter

    cred = _cred("bypass")

    with patch(
        "app.services.credential_limiter.SessionLocal"
    ) as mock_session_local:
        result_zero = await credential_limiter.acquire(cred, 0, wait_budget_s=1.0)
        result_none = await credential_limiter.acquire(cred, None, wait_budget_s=1.0)

    assert result_zero is credential_limiter.BYPASS
    assert result_none is credential_limiter.BYPASS
    mock_session_local.assert_not_called()

    # release() of the bypass sentinel is a documented no-op — must not raise
    # and must not touch the DB either.
    with patch(
        "app.services.credential_limiter.SessionLocal"
    ) as mock_session_local_release:
        await credential_limiter.release(credential_limiter.BYPASS)
    mock_session_local_release.assert_not_called()


async def test_release_of_missing_slot_id_is_a_noop():
    from app.services import credential_limiter

    # A random UUID that was never inserted — release must not raise.
    await credential_limiter.release(uuid.uuid4())


async def test_pc_id_default_format():
    import os as _os
    import socket as _socket

    from app.services import credential_limiter

    assert credential_limiter.PC_ID == f"{_socket.gethostname()}:{_os.getpid()}"
