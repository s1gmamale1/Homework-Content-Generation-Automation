import asyncio
import json
from collections import defaultdict
from typing import Any

from loguru import logger as log

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

CHANNEL = "hw_events"
# Headroom under Postgres' ~8000-byte NOTIFY payload cap, measured on the
# ENCODED wire JSON (not the python dict).
INLINE_LIMIT_BYTES = 7000
# Per-field cap for what survives into a refetch marker — keeps small hints
# (phase_name, phase_order, …) inline so SSE endpoints can rebuild cheaply.
_FIELD_LIMIT_BYTES = 512
# Reserved event carrying close()'s stream-end sentinel across processes.
_CLOSE_EVENT = "__close__"


def _encode(resource_id: str, event: str, data: dict[str, Any]) -> str:
    """Wire JSON for pg_notify; oversized data collapses to a refetch marker."""
    full = json.dumps({"resource_id": resource_id, "event": event, "data": data})
    if len(full.encode()) <= INLINE_LIMIT_BYTES:
        return full
    marker: dict[str, Any] = {"__refetch__": True}
    for k, v in data.items():
        if k == "__refetch__":
            # Never let a caller-supplied key of this name overwrite the
            # marker sentinel — that would mask that big fields were dropped.
            continue
        if len(json.dumps(v).encode()) <= _FIELD_LIMIT_BYTES:
            marker[k] = v
    out = json.dumps({"resource_id": resource_id, "event": event, "data": marker})
    if len(out.encode()) > INLINE_LIMIT_BYTES:
        # Many small fields can overflow together — degrade to the bare marker.
        out = json.dumps(
            {"resource_id": resource_id, "event": event, "data": {"__refetch__": True}}
        )
    return out


def _dispatch(payload_json: str) -> None:
    """Route one decoded NOTIFY payload into this process's local queues."""
    try:
        msg = json.loads(payload_json)
        resource_id, event, data = msg["resource_id"], msg["event"], msg["data"]
        if not isinstance(data, dict):
            raise TypeError(f"non-dict data: {type(data).__name__}")
    except (ValueError, KeyError, TypeError):
        log.error(
            f"events_bus: dropping undecodable NOTIFY payload: {payload_json[:200]!r}"
        )
        return
    queues = list(_subscribers.get(resource_id, []))
    if event == _CLOSE_EVENT:
        for q in queues:
            q.put_nowait(None)
        return
    for q in queues:
        # Fresh dict per queue — consumers must never share a mutable payload.
        q.put_nowait({"event": event, "data": dict(data)})


def subscribe(resource_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[resource_id].add(q)
    return q


def unsubscribe(resource_id: str, q: asyncio.Queue) -> None:
    _subscribers[resource_id].discard(q)
    if not _subscribers[resource_id]:
        _subscribers.pop(resource_id, None)


async def publish(resource_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data}
    for q in list(_subscribers.get(resource_id, [])):
        await q.put(payload)


async def close(resource_id: str) -> None:
    for q in list(_subscribers.get(resource_id, [])):
        await q.put(None)
