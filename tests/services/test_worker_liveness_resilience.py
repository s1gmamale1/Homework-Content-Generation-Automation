"""Worker liveness under database pressure (the 2026-08-13 roster-flap fix).

Incident: during a fleet generation run the roster oscillated
38 -> 27 -> 34 -> 16 -> 22 within minutes. Nobody powered off; hosts lost
their heartbeat. Measured: hosts with **0 jobs running** had the STALEST
beats (avg 153s, worst 533s) while hosts running 1-4 jobs were fresh
(31-62s) — the starved workers were parked on a contended lock holding one
of the worker process's mere 4 pooled connections
(``_pool_config`` -> ``pool_size=2, max_overflow=2``), so the registry beat
could not get a connection at all. Past 90s
(``worker_registry_stale_seconds``) they read offline; past 600s
(``worker_registry_prune_seconds``) a PEER DELETED their row and they
re-registered — the flap.

These tests pin the three properties that make that impossible:

  1. the registry beat runs on its OWN connection pool, so job saturation of
     the shared pool can never starve it;
  2. a transient failure is retried inside one cycle and NEVER evicts a
     worker (no stop, no deregister, no self-delete);
  3. the destructive DELETE horizon is derived from
     ``heartbeat interval x tolerated consecutive failures``, is clamped up
     when misconfigured, skips workers with live jobs, and is preceded by a
     non-destructive ``status='offline'`` marking.

Plus the invariant the fix must not break: ``status='draining'`` still stops
the worker (operators rely on it to force config reloads).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import workers as workers_repo
from app.services import worker as worker_mod
from app.services.worker import Worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async-context session stand-in for the registry beat."""

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:  # pragma: no cover - defensive
        pass

    async def close(self) -> None:  # pragma: no cover - defensive
        pass


class _CapturingSession:
    """Session stand-in that records every executed statement (no DB)."""

    def __init__(self, rowcount: int = 1) -> None:
        self.statements: list = []
        self._rowcount = rowcount

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)

        class _R:
            rowcount = self._rowcount

        return _R()

    def compiled(self, index: int = 0) -> str:
        """Fully-inlined SQL, so assertions can read the real predicates."""
        return str(
            self.statements[index].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

    def params(self, index: int = 0) -> dict:
        return dict(self.statements[index].compile(dialect=postgresql.dialect()).params)


def _worker() -> Worker:
    return Worker(concurrency=1, poll_interval=0.1, job_timeout_seconds=5, max_attempts=1)


def _patch_registry_repo(monkeypatch, *, status: str | None = "online"):
    """Point the registry repo calls at mocks; return (get_status, upsert)."""
    get_status = AsyncMock(return_value=status)
    upsert = AsyncMock(return_value=None)
    monkeypatch.setattr(worker_mod.workers_repo, "get_status", get_status)
    monkeypatch.setattr(worker_mod.workers_repo, "upsert_heartbeat", upsert)
    return get_status, upsert


def _patch_heartbeat_factory(monkeypatch, session: _FakeSession) -> None:
    monkeypatch.setattr(worker_mod, "heartbeat_sessionmaker", lambda: (lambda: session))


# ---------------------------------------------------------------------------
# 1. The registry beat must NOT share the job pool
# ---------------------------------------------------------------------------


async def test_registry_beat_survives_a_fully_saturated_shared_pool(monkeypatch):
    """THE incident, reproduced: every connection of the shared 4-connection
    worker pool is taken by job work. The registry beat must still land.

    RED on the old code: ``_registry_heartbeat`` opened ``SessionLocal()`` —
    the shared pool — so this raised, the beat was swallowed with a warning,
    and ``last_heartbeat`` aged out until a peer deleted the row.
    """

    def _pool_exhausted(*_a, **_kw):
        raise TimeoutError(
            "QueuePool limit of size 2 overflow 2 reached, connection timed out"
        )

    monkeypatch.setattr(worker_mod, "SessionLocal", _pool_exhausted)
    session = _FakeSession()
    _patch_heartbeat_factory(monkeypatch, session)
    _, upsert = _patch_registry_repo(monkeypatch)

    w = _worker()
    assert await w._registry_heartbeat() is True
    assert upsert.await_count == 1, "the beat must reach upsert_heartbeat"
    assert session.commits == 1, "the beat must commit on its own connection"


def test_heartbeat_pool_is_dedicated_isolated_and_small():
    """A dedicated engine, not ``app.db.engine``, with a reserved slot and a
    checkout timeout well under one beat interval (so a beat can never sit in
    the pool queue for longer than its own cycle)."""
    import app.db as app_db

    kwargs = worker_mod._heartbeat_engine_kwargs()
    assert kwargs["pool_size"] >= 1
    assert kwargs["pool_size"] + kwargs["max_overflow"] <= 2, (
        "the heartbeat pool must stay tiny — it exists to reserve a slot, "
        "not to add fleet-wide connection pressure"
    )
    assert kwargs["pool_timeout"] <= worker_mod._heartbeat_interval_seconds(), (
        "a beat must never queue for a connection longer than one interval"
    )

    factory = worker_mod.heartbeat_sessionmaker()
    assert factory.kw["bind"] is not app_db.engine, (
        "the registry heartbeat must not be bound to the shared job engine"
    )


async def test_dispose_heartbeat_engine_is_idempotent():
    worker_mod.heartbeat_sessionmaker()
    await worker_mod.dispose_heartbeat_engine()
    await worker_mod.dispose_heartbeat_engine()
    assert worker_mod._HEARTBEAT_ENGINE is None


# ---------------------------------------------------------------------------
# 2. Transient failures are tolerated; a worker is never evicted for one
# ---------------------------------------------------------------------------


async def test_transient_heartbeat_error_is_retried_inside_one_cycle(monkeypatch):
    """One failed attempt must NOT cost a whole heartbeat interval.

    RED on the old code: ``_registry_heartbeat`` made exactly ONE attempt per
    cycle, swallowed the error and returned None — the next chance was a full
    ``heartbeat_seconds`` later, which is how a 30s interval turned into the
    measured 153s average staleness.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    calls = {"n": 0}

    async def _flaky(self) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection reset by peer")

    monkeypatch.setattr(Worker, "_registry_beat_once", _flaky)

    w = _worker()
    assert await w._registry_heartbeat() is True
    assert calls["n"] == 2, "a transient failure must be retried in the same cycle"
    assert w._consecutive_heartbeat_failures == 0


async def test_retries_back_off_between_attempts(monkeypatch):
    delays: list[float] = []

    async def _sleep(d):
        delays.append(d)

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    async def _always_fails(self) -> None:
        raise OSError("db down")

    monkeypatch.setattr(Worker, "_registry_beat_once", _always_fails)

    w = _worker()
    assert await w._registry_heartbeat() is False
    assert len(delays) >= 1, "failed attempts must back off before retrying"
    assert delays == sorted(delays), f"backoff must be monotonic, got {delays}"
    assert sum(delays) < worker_mod._heartbeat_interval_seconds(), (
        "a whole retry cycle must fit inside one heartbeat interval"
    )


async def test_a_failed_beat_never_evicts_the_worker(monkeypatch):
    """A single (or many) failed heartbeats must not stop the worker, must not
    deregister it, and must not touch the registry destructively.

    RED on the old code: ``_consecutive_heartbeat_failures`` did not exist, so
    nothing counted failures and nothing distinguished "one blip" from "this
    host really is gone".
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    deregister = AsyncMock()
    monkeypatch.setattr(worker_mod.workers_repo, "deregister", deregister)

    async def _always_fails(self) -> None:
        raise OSError("db down")

    monkeypatch.setattr(Worker, "_registry_beat_once", _always_fails)

    w = _worker()
    for expected in range(1, worker_mod._HEARTBEAT_MAX_CONSECUTIVE_FAILURES + 3):
        assert await w._registry_heartbeat() is False
        assert w._consecutive_heartbeat_failures == expected
        assert not w._stop_event.is_set(), (
            "a failed heartbeat must never stop the worker — only an explicit "
            "drain may do that"
        )
    assert deregister.await_count == 0, "a failed beat must never self-deregister"


async def test_a_successful_beat_resets_the_failure_counter(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    outcome = {"fail": True}

    async def _toggle(self) -> None:
        if outcome["fail"]:
            raise OSError("db down")

    monkeypatch.setattr(Worker, "_registry_beat_once", _toggle)

    w = _worker()
    assert await w._registry_heartbeat() is False
    assert w._consecutive_heartbeat_failures == 1
    outcome["fail"] = False
    assert await w._registry_heartbeat() is True
    assert w._consecutive_heartbeat_failures == 0


async def test_a_hung_beat_is_bounded_so_the_next_beat_still_fires(monkeypatch):
    """A beat blocked on a contended lock must be abandoned, not allowed to
    stretch the cadence. RED on the old code: the loop awaited the beat inline
    with no timeout, so one 300s-blocked beat meant 330s without a heartbeat.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 30)

    async def _hangs(self) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(Worker, "_registry_beat_once", _hangs)

    w = _worker()
    # A full cycle of hung attempts must still return, well inside one interval.
    result = await asyncio.wait_for(
        w._registry_heartbeat(),
        timeout=worker_mod._heartbeat_interval_seconds(),
    )
    assert result is False
    assert w._consecutive_heartbeat_failures == 1


async def test_heartbeat_loop_keeps_a_fixed_cadence_after_a_slow_beat(monkeypatch):
    """The next beat is scheduled from the previous beat's START, so a slow
    cycle does not push the whole schedule out (old code: interval was added
    AFTER the beat returned)."""
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 0.2)
    starts: list[float] = []
    loop = asyncio.get_running_loop()

    async def _slow(self) -> bool:
        starts.append(loop.time())
        await asyncio.sleep(0.15)
        return True

    monkeypatch.setattr(Worker, "_registry_heartbeat", _slow)

    w = _worker()
    task = asyncio.create_task(w._registry_heartbeat_loop())
    try:
        await asyncio.sleep(0.75)
    finally:
        w.stop()
        await asyncio.wait_for(task, timeout=2)

    assert len(starts) >= 3, f"expected ~4 beats in 0.75s at 0.2s cadence, got {starts}"
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert max(gaps) < 0.3, (
        f"a 0.15s beat must not stretch the 0.2s cadence to 0.35s; gaps={gaps}"
    )


# ---------------------------------------------------------------------------
# 3. Pruning is conservative
# ---------------------------------------------------------------------------


def test_delete_horizon_exceeds_the_tolerated_failure_window(monkeypatch):
    """The arithmetic the incident violated.

    RED on the old code: neither helper existed, and the sweep handed the raw
    ``worker_registry_prune_seconds`` straight to a DELETE.
    """
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 30)
    tolerated = worker_mod._HEARTBEAT_MAX_CONSECUTIVE_FAILURES
    tolerance = 30 * tolerated

    assert worker_mod._heartbeat_tolerance_seconds() == tolerance
    assert worker_mod._offline_after_seconds() >= tolerance * worker_mod._PRUNE_SAFETY_FACTOR
    assert worker_mod._delete_after_seconds() > worker_mod._offline_after_seconds()


def test_worker_slower_than_one_interval_is_never_deleted(monkeypatch):
    """The measured worst case (533s of staleness on a live, working host)
    must be nowhere near the DELETE horizon."""
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 30)
    worst_observed_gap_seconds = 533
    assert worker_mod._delete_after_seconds() > worst_observed_gap_seconds * 2, (
        "the DELETE horizon must be comfortably beyond the worst measured "
        "staleness of a live host, not 12% above it"
    )


def test_a_misconfigured_small_prune_window_is_clamped_up(monkeypatch):
    """An operator lowering WORKER_REGISTRY_PRUNE_SECONDS must not be able to
    re-arm the flap."""
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 30)
    monkeypatch.setattr(worker_mod.settings, "worker_registry_prune_seconds", 45)
    floor = 30 * worker_mod._HEARTBEAT_MAX_CONSECUTIVE_FAILURES * worker_mod._PRUNE_SAFETY_FACTOR
    assert worker_mod._offline_after_seconds() >= floor
    assert worker_mod._delete_after_seconds() >= floor


async def test_sweep_marks_offline_first_and_deletes_only_much_later(monkeypatch):
    """The sweep must call the non-destructive marker at the offline horizon
    and the DELETE only at the (much longer) retention horizon, with the floor
    passed as a guard.

    RED on the old code: ``mark_stale_offline`` did not exist and
    ``prune_stale`` was called with ``worker_registry_prune_seconds`` (600s)
    as a straight DELETE window.
    """
    monkeypatch.setattr(worker_mod.settings, "heartbeat_seconds", 30)
    seen: dict[str, dict] = {}

    class _Tx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def begin(self):
            return _Tx()

    monkeypatch.setattr(worker_mod, "SessionLocal", lambda: _Sess())
    monkeypatch.setattr(
        worker_mod.jobs_repo, "reclaim_stuck_jobs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        worker_mod.jobs_repo, "reclaim_stale_cancelling", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        worker_mod.jobs_repo, "fail_exhausted_pending_jobs", AsyncMock(return_value=0)
    )

    async def _mark(session, **kw):
        seen["mark"] = kw
        return 0

    async def _prune(session, **kw):
        seen["prune"] = kw
        return 0

    monkeypatch.setattr(worker_mod.workers_repo, "mark_stale_offline", _mark)
    monkeypatch.setattr(worker_mod.workers_repo, "prune_stale", _prune)

    await _worker()._sweep_stuck_jobs()

    assert seen["mark"]["older_than_seconds"] == worker_mod._offline_after_seconds()
    assert seen["prune"]["older_than_seconds"] == worker_mod._delete_after_seconds()
    assert seen["prune"]["min_seconds"] == worker_mod._offline_after_seconds()
    assert seen["prune"]["older_than_seconds"] > seen["mark"]["older_than_seconds"], (
        "DELETE must happen strictly later than the offline marking"
    )


async def test_prune_stale_clamps_an_unsafe_window_up_to_the_floor():
    """RED on the old code: ``prune_stale`` had no floor parameter at all."""
    sess = _CapturingSession()
    await workers_repo.prune_stale(sess, older_than_seconds=30, min_seconds=3600)
    params = sess.params()
    windows = [v for v in params.values() if hasattr(v, "total_seconds")]
    assert windows, f"expected an interval bind, got {params}"
    assert max(w.total_seconds() for w in windows) >= 3600, (
        "prune_stale must clamp a too-small window up to the safety floor"
    )


async def test_prune_stale_never_deletes_a_worker_that_owns_a_live_job():
    """A row whose pc_id still owns a running job is demonstrably alive.

    RED on the old code: the DELETE was an unguarded
    ``WHERE last_heartbeat < now() - interval``.
    """
    sess = _CapturingSession()
    await workers_repo.prune_stale(sess, older_than_seconds=3600)
    sql = sess.compiled()
    assert "homework_jobs" in sql, (
        "prune_stale must exclude workers that still own live jobs"
    )
    assert "NOT (EXISTS" in sql or "NOT EXISTS" in sql, sql


async def test_mark_stale_offline_is_non_destructive_and_spares_draining():
    """The offline marker must be an UPDATE (never a DELETE) and must never
    clobber a pending drain signal — operators rely on ``draining`` sticking
    until the worker reads it."""
    sess = _CapturingSession()
    n = await workers_repo.mark_stale_offline(sess, older_than_seconds=600)
    sql = sess.compiled()
    assert sql.strip().upper().startswith("UPDATE"), sql
    assert "DELETE" not in sql.upper(), sql
    assert "draining" in sql, "the marker must exclude rows in the draining state"
    assert "offline" in sql
    assert n == 1


# ---------------------------------------------------------------------------
# 4. Drain semantics are unchanged
# ---------------------------------------------------------------------------


async def test_draining_status_still_stops_the_worker_and_skips_the_upsert(monkeypatch):
    get_status, upsert = _patch_registry_repo(monkeypatch, status="draining")
    w = _worker()

    kept_beating = await w._drain_check_and_beat(_FakeSession())

    assert kept_beating is False
    assert w._stop_event.is_set(), "draining must stop the worker"
    assert upsert.await_count == 0, "draining must not be clobbered by an online beat"


async def test_drain_is_detected_through_the_whole_retry_cycle(monkeypatch):
    """The retry wrapper must not swallow or delay the drain signal, and a
    drain must not be retried (it is a successful cycle, not a failure)."""
    session = _FakeSession()
    _patch_heartbeat_factory(monkeypatch, session)
    get_status, upsert = _patch_registry_repo(monkeypatch, status="draining")

    w = _worker()
    assert await w._registry_heartbeat() is True
    assert w._stop_event.is_set()
    assert upsert.await_count == 0
    assert get_status.await_count == 1, "a drain must not trigger retries"
    assert session.commits == 0, "no commit when the drain branch short-circuits"


async def test_unregistered_and_online_workers_keep_beating(monkeypatch):
    for status in (None, "online", "offline"):
        session = _FakeSession()
        _patch_heartbeat_factory(monkeypatch, session)
        _, upsert = _patch_registry_repo(monkeypatch, status=status)
        w = _worker()
        assert await w._registry_heartbeat() is True
        assert upsert.await_count == 1, f"status={status!r} must still beat"
        assert not w._stop_event.is_set(), f"status={status!r} must not stop the worker"
