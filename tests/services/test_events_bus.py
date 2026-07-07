"""Unit tests for the NOTIFY-backed events bus (codec + dispatch + publish)."""
import asyncio
import contextlib
import json

import pytest

from app.services import events_bus


def test_encode_small_payload_inline():
    raw = events_bus._encode(
        "job:x", "phase_started", {"phase_name": "flashcards", "phase_order": 2}
    )
    assert json.loads(raw) == {
        "resource_id": "job:x",
        "event": "phase_started",
        "data": {"phase_name": "flashcards", "phase_order": 2},
    }
    assert len(raw.encode()) <= events_bus.INLINE_LIMIT_BYTES


def test_encode_oversized_payload_collapses_to_refetch_marker():
    data = {"phase_name": "reading", "phase_order": 7, "output_md": "x" * 20_000}
    raw = events_bus._encode("job:x", "phase_completed", data)
    msg = json.loads(raw)
    assert msg["data"]["__refetch__"] is True
    assert msg["data"]["phase_name"] == "reading"   # small hint fields survive
    assert msg["data"]["phase_order"] == 7
    assert "output_md" not in msg["data"]           # big field dropped
    assert len(raw.encode()) <= events_bus.INLINE_LIMIT_BYTES


def test_encode_oversized_cyrillic_payload_trips_marker():
    # Cyrillic JSON-escapes to ~6 bytes per char, so a payload that looks
    # modest in character count (1600 chars) is actually well over the
    # ENCODED byte cap — this pins that the cap is measured post-escaping,
    # not on len() of the raw python string.
    data = {"entries": [{"t": "я" * 400} for _ in range(4)]}
    msg = json.loads(events_bus._encode("book:x", "toc_ready", data))
    assert msg["data"]["__refetch__"] is True


def test_encode_refetch_key_in_caller_data_does_not_mask_marker():
    # A caller data key literally named "__refetch__" (e.g. False) must not
    # overwrite the marker sentinel once the payload collapses — otherwise
    # the marker would read False and downstream consumers would think the
    # inline data is complete when big fields were actually dropped.
    data = {"__refetch__": False, "output_md": "x" * 20_000}
    raw = events_bus._encode("job:x", "phase_completed", data)
    msg = json.loads(raw)
    assert msg["data"]["__refetch__"] is True


def test_encode_degrades_to_bare_marker_when_even_hints_overflow():
    # 40 fields × ~400 encoded bytes each: every field passes the per-field
    # cap but together they'd overflow the payload cap again.
    data = {f"k{i}": "v" * 400 for i in range(40)}
    raw = events_bus._encode("job:x", "e", data)
    msg = json.loads(raw)
    assert msg["data"]["__refetch__"] is True
    assert len(raw.encode()) <= events_bus.INLINE_LIMIT_BYTES


def test_dispatch_routes_to_subscriber_and_close_sends_sentinel():
    q = events_bus.subscribe("job:r1")
    try:
        events_bus._dispatch(
            events_bus._encode("job:r1", "phase_started", {"phase_order": 1})
        )
        assert q.get_nowait() == {
            "event": "phase_started", "data": {"phase_order": 1}
        }
        events_bus._dispatch(
            events_bus._encode("job:r1", events_bus._CLOSE_EVENT, {})
        )
        assert q.get_nowait() is None
    finally:
        events_bus.unsubscribe("job:r1", q)


def test_dispatch_ignores_unsubscribed_resource():
    q = events_bus.subscribe("job:mine")
    try:
        events_bus._dispatch(events_bus._encode("job:other", "e", {"a": 1}))
        assert q.empty()
    finally:
        events_bus.unsubscribe("job:mine", q)


def test_dispatch_drops_garbage_without_raising():
    events_bus._dispatch("not json at all")
    events_bus._dispatch(json.dumps({"no": "expected keys"}))


def test_dispatch_drops_non_dict_data_without_raising():
    # All three keys present but data is not a dict (e.g. a bare int) — this
    # must be treated as undecodable, not raise, and deliver nothing. This
    # runs inside the asyncpg LISTEN callback in a later task, so a raise
    # here would kill the connection's callback dispatch.
    q = events_bus.subscribe("job:baddata")
    try:
        events_bus._dispatch(
            json.dumps({"resource_id": "job:baddata", "event": "e", "data": 5})
        )
        assert q.empty()
    finally:
        events_bus.unsubscribe("job:baddata", q)


def test_dispatch_gives_each_subscriber_its_own_data_dict():
    q1 = events_bus.subscribe("job:r2")
    q2 = events_bus.subscribe("job:r2")
    try:
        events_bus._dispatch(events_bus._encode("job:r2", "e", {"a": 1}))
        d1, d2 = q1.get_nowait(), q2.get_nowait()
        d1["data"]["a"] = 999
        assert d2["data"]["a"] == 1
    finally:
        events_bus.unsubscribe("job:r2", q1)
        events_bus.unsubscribe("job:r2", q2)


@pytest.mark.asyncio
async def test_publish_is_notify_only(monkeypatch):
    # NOTIFY-only invariant: publish must NOT put directly on local queues —
    # the pod's listener routes it back. A direct put would double-deliver
    # every event in embedded-worker mode.
    sent: list[str] = []

    async def _record(payload: str) -> None:
        sent.append(payload)

    monkeypatch.setattr(events_bus, "_notify", _record)
    q = events_bus.subscribe("job:z")
    try:
        await events_bus.publish("job:z", "e", {"a": 1})
        assert len(sent) == 1
        assert json.loads(sent[0])["data"] == {"a": 1}
        assert q.empty()
    finally:
        events_bus.unsubscribe("job:z", q)


@pytest.mark.asyncio
async def test_publish_and_close_deliver_through_loopback():
    # Under the conftest loopback (_notify → _dispatch) the full
    # publish → encode → dispatch → queue path runs in-process.
    q = events_bus.subscribe("job:lb")
    try:
        await events_bus.publish("job:lb", "phase_started", {"phase_order": 3})
        assert q.get_nowait() == {
            "event": "phase_started", "data": {"phase_order": 3}
        }
        await events_bus.close("job:lb")
        assert q.get_nowait() is None
    finally:
        events_bus.unsubscribe("job:lb", q)


@pytest.mark.asyncio
async def test_publish_swallows_notify_failure(monkeypatch):
    # Progress events are advisory — a dead bus must never fail the job.
    async def _boom(payload: str) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(events_bus, "_notify", _boom)
    await events_bus.publish("job:x", "e", {})   # must not raise
    await events_bus.close("job:x")              # must not raise


class _FakeConn:
    def __init__(self):
        self.closed = False
        self.listeners: list = []

    def is_closed(self):
        return self.closed

    async def add_listener(self, ch, cb):
        self.listeners.append((ch, cb))

    async def fetchval(self, sql):
        if self.closed:
            raise ConnectionError("dead socket")
        return 1

    async def close(self):
        self.closed = True


def test_dsn_from_strips_asyncpg_driver():
    assert (
        events_bus._dsn_from("postgresql+asyncpg://edu:edu@127.0.0.1:5433/edu_homework")
        == "postgresql://edu:edu@127.0.0.1:5433/edu_homework"
    )


@pytest.mark.asyncio
async def test_start_listener_raises_on_connect_failure(monkeypatch):
    # Startup failure must be VISIBLE — a silently dead listener reproduces
    # the frozen-"Queued"-chip bug.
    async def _refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(events_bus.asyncpg, "connect", _refuse)
    with pytest.raises(OSError):
        await events_bus.start_listener()
    assert events_bus._listener_task is None


@pytest.mark.asyncio
async def test_start_listener_listens_on_channel_and_stop_closes(monkeypatch):
    fake = _FakeConn()

    async def _connect_ok(*a, **k):
        return fake

    monkeypatch.setattr(events_bus.asyncpg, "connect", _connect_ok)
    await events_bus.start_listener()
    try:
        assert fake.listeners and fake.listeners[0][0] == events_bus.CHANNEL
        assert events_bus._listener_task is not None
    finally:
        await events_bus.stop_listener()
    assert fake.closed
    assert events_bus._listener_conn is None
    assert events_bus._listener_task is None


@pytest.mark.asyncio
async def test_watchdog_reconnects_after_connection_drop(monkeypatch):
    conns: list[_FakeConn] = []

    async def _connect():
        c = _FakeConn()
        await c.add_listener(events_bus.CHANNEL, events_bus._on_notify)
        conns.append(c)
        return c

    monkeypatch.setattr(events_bus, "_connect", _connect)
    monkeypatch.setattr(events_bus, "_POLL_SECONDS", 0.01)
    events_bus._stopping = False
    events_bus._listener_conn = await _connect()
    task = asyncio.create_task(events_bus._watchdog())
    try:
        conns[0].closed = True          # kill the first connection
        for _ in range(200):            # wait ≤2s for the watchdog to react
            await asyncio.sleep(0.01)
            if len(conns) >= 2:
                break
        assert len(conns) >= 2, "watchdog never reconnected"
        assert not conns[-1].is_closed()
        assert events_bus._listener_conn is conns[-1]
    finally:
        events_bus._stopping = True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        events_bus._listener_conn = None


def test_lifespan_wires_listener_start_and_stop():
    import inspect

    import main

    src = inspect.getsource(main.lifespan)
    assert "events_bus.start_listener()" in src
    assert "events_bus.stop_listener()" in src
    # start must NOT be wrapped in a swallowing try — startup failure is loud.
    before_start = src.split("events_bus.start_listener()")[0]
    assert not before_start.rstrip().endswith("try:")
