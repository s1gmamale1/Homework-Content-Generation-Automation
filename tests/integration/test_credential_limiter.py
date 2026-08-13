"""Real-DB bites-proofs for the fleet-wide per-credential api concurrency
limiter (BE-16 tasks 3-4). RUN_DB_INTEGRATION=1 against a scratch DB
(edu_scratch_credlim) — pin 127.0.0.1 (see scratch-db-localhost-dual-server
trap in memory: localhost can resolve to a DIFFERENT server via IPv6).

Each concurrent-acquirer task opens its OWN SessionLocal()/connection —
that is what makes these tests a real stand-in for separate fleet hosts
rather than one process talking to itself.

Task 4 additions (below the task-3 acquire/release/sweep bites-proofs) cover
``resolve_limit`` against REAL ``sa_keys`` rows: a project override winning,
MIN over two rows naming the same (non-unique) ``project_id``, and a NULL
override falling through to the provider default. The pure per-credential
cache / TTL / error-uncached / fingerprint-form coverage lives in
``tests/services/test_credential_limiter_resolve.py`` (no DB needed there).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.models.sa_key import SAKey

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


# ─────────────────────────────────────────────────────────────────────────
# The 2026-08 production defect: every slot acquisition fleet-wide serialized
# through ONE `pg_advisory_xact_lock(hashtext(credential))`, because all 38
# hosts share one Gemini key and therefore hash to the same lock. Measured:
# 75 connections blocked on it, longest wait 822s, 54 of a 900-slot ceiling
# in use. The two tests below are the regression proofs.
# ─────────────────────────────────────────────────────────────────────────


async def test_acquire_is_unaffected_by_the_old_fleet_wide_advisory_lock():
    """Nothing may serialize on a single per-credential advisory lock.

    RED-proof: hold the exact lock the old implementation took and then try
    to acquire. The old code blocked inside `pg_advisory_xact_lock` — which
    is NOT bounded by `wait_budget_s` (the budget is only consulted between
    polls), so it would hang here, pinning a pool connection, until the outer
    `wait_for` fired. That is precisely the production symptom.
    """
    from app.services import credential_limiter

    cred = _cred("nolock")

    async with SessionLocal() as blocker:
        async with blocker.begin():
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:cred))"), {"cred": cred}
            )
            slot = await asyncio.wait_for(
                credential_limiter.acquire(cred, 4, wait_budget_s=5.0), timeout=15.0
            )

    assert slot is not None, "acquire must not depend on a fleet-wide advisory lock"
    await credential_limiter.release(slot)
    assert await _count_rows(cred) == 0


async def test_fleet_of_concurrent_acquirers_is_exact_and_takes_no_advisory_lock():
    """A whole fleet hammering ONE credential at once: the ceiling holds
    exactly, and no advisory lock is taken at any point.

    RED-proof (two independent ways): on the old implementation every one of
    these acquisitions took `pg_advisory_xact_lock`, so (a) the watcher below
    observes advisory locks instead of zero, and (b) they ran strictly one at
    a time — the whole point of the fix is that they no longer do.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.services import credential_limiter

    cred = _cred("fleet")
    limit, hosts = 24, 48

    # Each simulated host gets its own connection. The app engine's pool is
    # sized for ONE worker process (`db._pool_config`), so reusing it would
    # serialize this test at the pool rather than exercising the DB.
    engine = create_async_engine(
        settings.database_url, pool_size=hosts, max_overflow=0
    )
    fleet_sessions = async_sessionmaker(engine, expire_on_commit=False)

    held: set[object] = set()
    peak_held = 0
    advisory_samples: list[int] = []
    row_samples: list[int] = []
    stop = asyncio.Event()

    async def watcher() -> None:
        """Sample the two things that must never happen: an advisory lock
        being taken, and more rows alive than the ceiling allows."""
        async with SessionLocal() as session:
            while not stop.is_set():
                advisory_samples.append(
                    (
                        await session.execute(
                            # Scoped to THIS database on purpose: pg_locks is
                            # cluster-wide, and a scratch DB usually shares a
                            # server with other databases whose own advisory
                            # locks are none of this test's business.
                            text(
                                "SELECT count(*) FROM pg_locks "
                                "WHERE locktype = 'advisory' "
                                "AND database = ("
                                "  SELECT oid FROM pg_database "
                                "  WHERE datname = current_database())"
                            )
                        )
                    ).scalar_one()
                )
                row_samples.append(
                    (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM credential_slots "
                                "WHERE credential = :cred"
                            ),
                            {"cred": cred},
                        )
                    ).scalar_one()
                )
                await asyncio.sleep(0.003)

    async def host(i: int) -> bool:
        nonlocal peak_held
        slot = await credential_limiter.acquire(cred, limit, wait_budget_s=20.0)
        if slot is None:
            return False
        held.add(slot)
        peak_held = max(peak_held, len(held))
        await asyncio.sleep(0.02)  # stand in for the model call
        held.discard(slot)
        await credential_limiter.release(slot)
        return True

    watch_task = asyncio.create_task(watcher())
    try:
        with patch.object(credential_limiter, "SessionLocal", fleet_sessions):
            results = await asyncio.gather(*[host(i) for i in range(hosts)])
    finally:
        stop.set()
        await watch_task
        await engine.dispose()

    assert all(results), "every host must eventually get a slot inside its budget"
    assert peak_held <= limit, f"ceiling exceeded: {peak_held} concurrent holders > {limit}"
    assert max(row_samples) <= limit, (
        f"ceiling exceeded in the table: {max(row_samples)} live rows > {limit}"
    )
    assert advisory_samples, "watcher never sampled"
    assert max(advisory_samples) == 0, (
        "an advisory lock was taken — slot acquisition is serializing "
        f"fleet-wide again (peak {max(advisory_samples)})"
    )
    assert await _count_rows(cred) == 0


async def test_waiting_acquirer_holds_no_db_connection_across_its_poll_sleep():
    """A *waiting* worker must not pin a connection from the same tiny pool
    (2+2 per worker process) that the real jobs need. Every poll opens its
    own session and closes it before sleeping."""
    from app.services import credential_limiter

    cred = _cred("nopin")
    blocker = await credential_limiter.acquire(cred, 1, wait_budget_s=5.0)
    assert blocker is not None

    live = 0
    live_at_sleep: list[int] = []
    real_session_local = credential_limiter.SessionLocal

    class _CountingSession:
        def __init__(self) -> None:
            self._inner = real_session_local()

        async def __aenter__(self):
            nonlocal live
            live += 1
            return await self._inner.__aenter__()

        async def __aexit__(self, *exc):
            nonlocal live
            live -= 1
            return await self._inner.__aexit__(*exc)

    real_sleep = asyncio.sleep

    async def _watching_sleep(seconds):
        live_at_sleep.append(live)
        raise _StopPolling()

    class _StopPolling(Exception):
        pass

    try:
        with patch.object(credential_limiter, "SessionLocal", _CountingSession):
            with patch.object(
                credential_limiter.asyncio, "sleep", _watching_sleep
            ):
                with pytest.raises(_StopPolling):
                    await credential_limiter.acquire(cred, 1, wait_budget_s=30.0)
    finally:
        await credential_limiter.release(blocker)
        assert real_sleep is asyncio.sleep

    assert live_at_sleep == [0], (
        f"a poller held {live_at_sleep} connection(s) across its sleep"
    )


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

    async def _insert_stale() -> object:
        async with SessionLocal() as session:
            async with session.begin():
                return (
                    await session.execute(
                        text(
                            "INSERT INTO credential_slots "
                            "(credential, slot_index, pc_id, acquired_at) "
                            "VALUES (:cred, 0, 'stale-host:1', "
                            "        now() - make_interval(secs => :backdate)) "
                            "RETURNING id"
                        ),
                        {"cred": cred, "backdate": ttl + 300},
                    )
                ).scalar_one()

    # Backdate a slot row well past the TTL — a stale slot from a worker that
    # crashed without releasing.
    stale_id = await _insert_stale()

    # limit=1 but the only existing row is stale — a fresh acquire must
    # still succeed (the stale row must not count against the limit).
    slot = await credential_limiter.acquire(cred, 1, wait_budget_s=2.0)
    assert slot is not None

    # CHANGED with migration 0060: the stale row's slot index is TAKEN OVER
    # in place rather than left behind for sweep() to find, so a crashed
    # holder can never park on a slot index. There is exactly one row, and it
    # carries a NEW id — which is what stops the crashed holder's late
    # release() from deleting the slot its successor now owns.
    assert slot != stale_id
    assert await _count_rows(cred) == 1

    await credential_limiter.release(stale_id)  # the dead holder, waking up late
    assert await _count_rows(cred) == 1, "a late release must not free someone else's slot"

    await credential_limiter.release(slot)
    assert await _count_rows(cred) == 0

    # sweep() still reaps a stale row that nobody re-claimed in the meantime.
    await _insert_stale()
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


async def _insert_sa_key(project_id: str, *, max_concurrent_calls) -> None:
    async with SessionLocal() as session:
        async with session.begin():
            session.add(
                SAKey(
                    original_filename="k.json",
                    project_id=project_id,
                    client_email="sa@x.iam.gserviceaccount.com",
                    sha256=f"sha-credlim-{uuid.uuid4().hex}",
                    byte_size=100,
                    max_concurrent_calls=max_concurrent_calls,
                )
            )


async def test_resolve_limit_project_override_wins():
    from app.services import credential_limiter

    credential_limiter.clear_limit_cache()
    project = f"proj-override-{uuid.uuid4().hex[:12]}"
    await _insert_sa_key(project, max_concurrent_calls=3)

    async with SessionLocal() as session:
        limit = await credential_limiter.resolve_limit(
            session, "gemini", f"gemini:{project}"
        )
    assert limit == 3
    assert limit != credential_limiter.settings.credential_max_concurrent_gemini


async def test_resolve_limit_duplicate_project_rows_min_wins_deterministically():
    """`sa_keys.project_id` has no unique constraint (only sha256) — two SA
    keys can legitimately name the same GCP project. Resolution must be
    deterministic and conservative: MIN over the non-null values."""
    from app.services import credential_limiter

    credential_limiter.clear_limit_cache()
    project = f"proj-dup-{uuid.uuid4().hex[:12]}"
    await _insert_sa_key(project, max_concurrent_calls=5)
    await _insert_sa_key(project, max_concurrent_calls=2)

    async with SessionLocal() as session:
        limit = await credential_limiter.resolve_limit(
            session, "gemini", f"gemini:{project}"
        )
    assert limit == 2  # MIN(5, 2), not the first-inserted row nor MAX


async def test_resolve_limit_null_override_falls_back_to_default():
    from app.services import credential_limiter

    credential_limiter.clear_limit_cache()
    project = f"proj-null-{uuid.uuid4().hex[:12]}"
    await _insert_sa_key(project, max_concurrent_calls=None)

    async with SessionLocal() as session:
        limit = await credential_limiter.resolve_limit(
            session, "gemini", f"gemini:{project}"
        )
    assert limit == credential_limiter.settings.credential_max_concurrent_gemini


async def test_acquire_poll_backoff_is_exponential_with_jitter_and_capped():
    """Task 4 amendment (codex-review #6): the fixed ~1s inter-poll sleep
    now decays 1s -> 2s -> 4s -> 5s (capped), + jitter in [0, 0.5). Verified
    against a real held slot so `acquire` actually enters its poll loop
    against Postgres, capturing the durations passed to `asyncio.sleep`
    without waiting for them for real."""
    from app.services import credential_limiter

    cred = _cred("decay")
    held = await credential_limiter.acquire(cred, 1, wait_budget_s=5.0)
    assert held is not None

    recorded: list[float] = []

    class _StopPolling(Exception):
        pass

    async def _fake_sleep(_seconds):
        recorded.append(_seconds)
        if len(recorded) >= 5:
            raise _StopPolling()

    try:
        with patch(
            "app.services.credential_limiter.asyncio.sleep", side_effect=_fake_sleep
        ):
            with pytest.raises(_StopPolling):
                await credential_limiter.acquire(cred, 1, wait_budget_s=60.0)
    finally:
        await credential_limiter.release(held)

    assert len(recorded) == 5
    bases = [1.0, 2.0, 4.0, 5.0, 5.0]  # decay 1->2->4->5, capped at 5
    for got, base in zip(recorded, bases):
        assert base <= got <= base + 0.5 + 1e-9, (base, got)
