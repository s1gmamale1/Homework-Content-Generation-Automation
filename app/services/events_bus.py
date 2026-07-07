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

import asyncpg
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


_listener_conn: "asyncpg.Connection | None" = None
_listener_task: "asyncio.Task | None" = None
_stopping = False
_POLL_SECONDS = 5.0
_BACKOFF_MAX = 30.0


def _dsn_from(url: str) -> str:
    """SQLAlchemy URL → raw asyncpg DSN (strip the +asyncpg driver marker)."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _dsn() -> str:
    from app.config import settings

    return _dsn_from(settings.database_url)


def _on_notify(conn, pid, channel, payload) -> None:
    _dispatch(payload)


async def _connect() -> "asyncpg.Connection":
    conn = await asyncpg.connect(_dsn())
    await conn.add_listener(CHANNEL, _on_notify)
    return conn


async def start_listener() -> None:
    """Open this process's LISTEN connection and start the watchdog.

    Raises on connect failure — startup must be loud: a silently dead
    listener is exactly the frozen-"Queued"-chip bug this bus fixes.
    """
    global _listener_conn, _listener_task, _stopping
    _stopping = False
    _listener_conn = await _connect()
    _listener_task = asyncio.create_task(_watchdog(), name="events-bus-listener")
    log.info(f"events_bus: LISTEN {CHANNEL} up")


async def _watchdog() -> None:
    """Probe the LISTEN connection; reconnect with backoff, logging LOUDLY.

    ``SELECT 1`` (not just ``is_closed``) forces detection of a socket that
    died silently while idle — the listener can sit idle for hours.
    """
    global _listener_conn
    while not _stopping:
        await asyncio.sleep(_POLL_SECONDS)
        if _stopping:
            return
        try:
            conn = _listener_conn
            if conn is None or conn.is_closed():
                raise ConnectionError("listener connection closed")
            await conn.fetchval("SELECT 1")
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _stopping:
                return
            log.error(
                f"events_bus: LISTEN connection DOWN ({exc!r}) — live SSE is "
                f"frozen on this pod until reconnect"
            )
        backoff = 1.0
        while not _stopping:
            try:
                _listener_conn = await _connect()
                log.success(f"events_bus: LISTEN {CHANNEL} reconnected")
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(
                    f"events_bus: reconnect failed ({exc!r}); retrying in "
                    f"{backoff:.0f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)


async def stop_listener() -> None:
    global _listener_conn, _listener_task, _stopping
    _stopping = True
    if _listener_task is not None:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None
    if _listener_conn is not None:
        try:
            if not _listener_conn.is_closed():
                await _listener_conn.close()
        finally:
            _listener_conn = None
