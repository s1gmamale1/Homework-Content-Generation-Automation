"""Tests for app/services/events_bus.py — async pub/sub for SSE."""
import asyncio
import pytest

import app.services.events_bus as bus_module
from app.services.events_bus import close, publish, subscribe, unsubscribe


@pytest.fixture(autouse=True)
def clear_subscribers():
    bus_module._subscribers.clear()
    yield
    bus_module._subscribers.clear()


class TestSubscribe:
    def test_subscribe_returns_queue(self):
        q = subscribe("res:1")
        assert isinstance(q, asyncio.Queue)

    def test_multiple_subscribers_get_separate_queues(self):
        q1 = subscribe("res:1")
        q2 = subscribe("res:1")
        assert q1 is not q2

    def test_subscribe_registers_in_internal_map(self):
        q = subscribe("res:2")
        assert q in bus_module._subscribers["res:2"]


class TestUnsubscribe:
    def test_unsubscribe_removes_queue(self):
        q = subscribe("res:3")
        unsubscribe("res:3", q)
        assert q not in bus_module._subscribers.get("res:3", set())

    def test_unsubscribe_last_subscriber_removes_key(self):
        q = subscribe("res:4")
        unsubscribe("res:4", q)
        assert "res:4" not in bus_module._subscribers

    def test_unsubscribe_nonexistent_queue_noop(self):
        q = asyncio.Queue()
        unsubscribe("res:404", q)  # should not raise

    def test_unsubscribe_one_of_two_keeps_other(self):
        q1 = subscribe("res:5")
        q2 = subscribe("res:5")
        unsubscribe("res:5", q1)
        assert q2 in bus_module._subscribers["res:5"]


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        q = subscribe("job:1")
        await publish("job:1", "phase_completed", {"phase": "flashcards"})
        payload = q.get_nowait()
        assert payload["event"] == "phase_completed"
        assert payload["data"]["phase"] == "flashcards"

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        q1 = subscribe("job:2")
        q2 = subscribe("job:2")
        await publish("job:2", "test_event", {"x": 1})
        assert q1.qsize() == 1
        assert q2.qsize() == 1

    @pytest.mark.asyncio
    async def test_publish_to_unsubscribed_resource_noop(self):
        await publish("nobody-listening", "event", {})  # should not raise

    @pytest.mark.asyncio
    async def test_publish_preserves_data_payload(self):
        q = subscribe("job:3")
        data = {"phase_name": "reading", "tokens_input": 500}
        await publish("job:3", "phase_started", data)
        payload = q.get_nowait()
        assert payload["data"] == data


class TestClose:
    @pytest.mark.asyncio
    async def test_close_sends_none_sentinel(self):
        q = subscribe("job:close")
        await close("job:close")
        sentinel = q.get_nowait()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_close_sends_to_all_subscribers(self):
        q1 = subscribe("job:close2")
        q2 = subscribe("job:close2")
        await close("job:close2")
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_close_nonexistent_resource_noop(self):
        await close("no-subscribers")  # should not raise


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_subscribe_after_unsubscribe_works(self):
        q1 = subscribe("res:cycle")
        unsubscribe("res:cycle", q1)
        q2 = subscribe("res:cycle")
        await publish("res:cycle", "ev", {})
        assert q2.qsize() == 1

    @pytest.mark.asyncio
    async def test_publish_then_close_ordering(self):
        q = subscribe("res:order")
        await publish("res:order", "phase_completed", {})
        await close("res:order")
        first = q.get_nowait()
        second = q.get_nowait()
        assert first["event"] == "phase_completed"
        assert second is None
