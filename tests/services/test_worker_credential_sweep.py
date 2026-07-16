"""Unit tests for Worker._sweep_credential_slots (BE-16 task 5, brief item 7).

Its own step, own try/except — deliberately NOT inside _sweep_stuck_jobs's
single `session.begin()` transaction, so a limiter-table hiccup can never
abort a job-reclaim sweep. `credential_limiter.sweep()` itself already
swallows DB errors and returns 0 (task 3); this wrapper's try/except is
defense-in-depth plus the "log only when > 0" policy from the brief.
"""
from __future__ import annotations

import pytest


def _make_worker(**kwargs):
    from app.services.worker import Worker
    return Worker(concurrency=1, **kwargs)


@pytest.mark.asyncio
async def test_sweep_credential_slots_logs_when_positive(monkeypatch):
    from app.services import worker as worker_module

    w = _make_worker()

    async def fake_sweep():
        return 3

    monkeypatch.setattr(worker_module.credential_limiter, "sweep", fake_sweep)

    logged: list[str] = []
    monkeypatch.setattr(worker_module.logger, "info", lambda msg: logged.append(msg))

    await w._sweep_credential_slots()
    assert any("3" in m and "credential" in m.lower() for m in logged)


@pytest.mark.asyncio
async def test_sweep_credential_slots_silent_when_zero(monkeypatch):
    from app.services import worker as worker_module

    w = _make_worker()

    async def fake_sweep():
        return 0

    monkeypatch.setattr(worker_module.credential_limiter, "sweep", fake_sweep)

    logged: list[str] = []
    monkeypatch.setattr(worker_module.logger, "info", lambda msg: logged.append(msg))

    await w._sweep_credential_slots()
    assert logged == []


@pytest.mark.asyncio
async def test_sweep_credential_slots_own_try_except_swallows_error(monkeypatch):
    """Even if credential_limiter.sweep() itself somehow raises, the
    worker's own wrapper must not propagate — this step must never be able
    to abort the surrounding periodic loop or _sweep_stuck_jobs."""
    from app.services import worker as worker_module

    w = _make_worker()

    async def boom():
        raise RuntimeError("credential_slots table gone")

    monkeypatch.setattr(worker_module.credential_limiter, "sweep", boom)

    await w._sweep_credential_slots()  # must not raise
