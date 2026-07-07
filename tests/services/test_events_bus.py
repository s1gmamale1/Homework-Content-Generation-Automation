"""Unit tests for the NOTIFY-backed events bus (codec + dispatch + publish)."""
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


def test_encode_measures_encoded_bytes_not_chars():
    # Cyrillic JSON-escapes to ~6 bytes per char — the cap must bite on the
    # ENCODED wire size, not len() of the python string.
    data = {"entries": [{"t": "я" * 400} for _ in range(4)]}
    msg = json.loads(events_bus._encode("book:x", "toc_ready", data))
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
