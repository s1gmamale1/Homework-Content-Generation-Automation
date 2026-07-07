"""Cross-process SSE event bus backed by Postgres LISTEN/NOTIFY (sse-multipod-1).

Delivery model: ``publish()``/``close()`` send a NOTIFY on the single
``hw_events`` channel — never a direct local-queue put. Every process running
``main.lifespan`` holds one LISTEN connection whose dispatcher routes payloads
into this process's ``asyncio.Queue`` subscribers. Embedded-worker mode rides
the exact same path as the fleet: if NOTIFY breaks, it breaks loudly
everywhere, not just cross-process.

Payloads whose encoded wire JSON exceeds ``INLINE_LIMIT_BYTES`` (NOTIFY caps
at ~8000 bytes) collapse to a ``__refetch__`` marker; the SSE endpoints
rebuild the data from the DB. Safe because publishers persist rows BEFORE
publishing.
"""
import asyncio
import json
from collections import defaultdict
from typing import Any

from loguru import logger as log
from sqlalchemy import text

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


async def _notify(payload: str) -> None:
    """Send one NOTIFY via the pooled engine (works from any process).
    pg_notify fires on commit — engine.begin() commits on exit."""
    from app.db import engine  # late import: no engine at module import time

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_notify(:ch, :payload)"),
            {"ch": CHANNEL, "payload": payload},
        )


async def publish(resource_id: str, event: str, data: dict[str, Any]) -> None:
    try:
        await _notify(_encode(resource_id, event, data))
    except Exception:
        # Progress events are advisory — a publish failure must never fail
        # the job/extract that emitted it. Loud so a dead bus is visible.
        log.exception(
            f"events_bus: NOTIFY failed | resource={resource_id} event={event}"
        )


async def close(resource_id: str) -> None:
    try:
        await _notify(_encode(resource_id, _CLOSE_EVENT, {}))
    except Exception:
        log.exception(f"events_bus: NOTIFY(close) failed | resource={resource_id}")
