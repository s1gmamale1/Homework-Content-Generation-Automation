import asyncio
from collections import defaultdict
from typing import Any

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


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
