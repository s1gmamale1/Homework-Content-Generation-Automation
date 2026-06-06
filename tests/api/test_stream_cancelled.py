"""A cancelled / cancelling job's SSE stream must return TERMINALLY.

When a job is cancelled, ``pipeline.run``'s ``finally: events_bus.close()``
tears the bus down BEFORE the worker commits ``status='cancelled'`` — so no
terminal event is ever published to the live stream. If a client then opens a
fresh stream for an already-``cancelled`` / ``cancelling`` job, the initial-state
replay must yield a terminal ``job_cancelled`` event and return, rather than
subscribing to a now-dead bus and blocking on ``q.get()`` forever (a leaked
subscriber that only self-heals on client disconnect).

This drives the inner ``event_gen`` async generator directly (DB mocked) and
asserts it returns terminally WITHOUT ever subscribing to the event bus.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import app.api.v1.jobs as jobs_mod


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeRequest:
    async def is_disconnected(self):
        return False


def _drive(job_status):
    jid = uuid4()
    job = SimpleNamespace(
        id=jid,
        status=job_status,
        difficulty=None,
        error_message=None,
        phase_outputs=[],
    )
    # If the code (wrongly) falls through to the live-subscription path, this
    # queue returns None immediately so the test fails on assert_not_called
    # rather than hanging on a real blocking get().
    fake_q = SimpleNamespace(get=AsyncMock(return_value=None))
    subscribe = MagicMock(return_value=fake_q)

    async def _run():
        resp = await jobs_mod.stream_job(jid, _FakeRequest())
        events = []
        async for ev in resp.body_iterator:
            events.append(ev)
        return events

    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), patch.object(
        jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=job)
    ), patch.object(jobs_mod.events_bus, "subscribe", subscribe), patch.object(
        jobs_mod.events_bus, "unsubscribe", MagicMock()
    ):
        events = asyncio.run(_run())
    return events, subscribe


def test_stream_cancelled_returns_terminally_without_subscribing():
    events, subscribe = _drive("cancelled")
    assert any(e["event"] == "job_cancelled" for e in events), events
    subscribe.assert_not_called()


def test_stream_cancelling_returns_terminally_without_subscribing():
    events, subscribe = _drive("cancelling")
    assert any(e["event"] == "job_cancelled" for e in events), events
    subscribe.assert_not_called()
