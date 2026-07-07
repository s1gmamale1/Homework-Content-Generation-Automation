# SSE Multi-Pod Bus (Postgres LISTEN/NOTIFY) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SSE live progress work when publishers (pipeline / toc_extractor) run in a different process than the API pod serving the stream, by backing `app/services/events_bus.py` with Postgres LISTEN/NOTIFY.

**Architecture:** `publish()`/`close()` send a NOTIFY on a single `hw_events` channel (via the existing SQLAlchemy engine pool — works identically from worker and API processes). Every process running `main.lifespan` opens one dedicated raw-asyncpg LISTEN connection whose dispatcher routes payloads into the existing local `asyncio.Queue` subscribers. The bus's public API (`subscribe`/`unsubscribe`/`publish`/`close`) is unchanged; publishers are untouched; SSE endpoints gain only a refetch branch for oversized events.

**Tech Stack:** asyncpg>=0.30.0 (already a dependency, `pyproject.toml:11`), SQLAlchemy async engine (`app/db.py`), FastAPI lifespan, pytest + one RUN_DB_INTEGRATION cross-process test.

## Approach & key decisions

Locked with the user 2026-07-07 (fixes `sse-multipod-1`, live-visible daily since the 6-worker fleet became the default operating mode):

1. **Postgres LISTEN/NOTIFY, not Redis** — Postgres is the one infra every worker already shares (the job queue runs on it); Redis is new infra for a solved problem. *(locked in the brief)*
2. **Inline payload + refetch marker when oversized** — NOTIFY caps at ~8000 bytes. Payloads whose encoded wire JSON ≤ 7000 bytes ride inline (the hot path: per-phase progress chips, small dicts, zero extra DB reads). Bigger ones (`toc_ready` with 75+ enriched entries; `phase_completed` carries full `output_md`) collapse to `{"__refetch__": true, …small fields}` and the SSE endpoint rebuilds the data from the DB. Safe because every publisher persists rows BEFORE publishing. The threshold is measured on **encoded bytes**, not the dict. Refetch for `toc_ready`/`toc_review` goes through the shared `_enriched_toc_entries` helper (never a parallel serializer — the in-flight lesson-filter lane is adding `entry_class` to its output and the two lanes must compose for free).
3. **Single `hw_events` channel** — one LISTEN connection per API process, dispatcher filters by `resource_id` via the dict that already exists. Per-resource channels rejected: LISTEN/UNLISTEN churn per SSE client + a reconnect state machine, for filtering costs we can't measure.
4. **NOTIFY-only delivery** — `publish()` never puts directly onto local queues; the pod's own listener routes it back. One code path for embedded and fleet mode, so every local test implicitly tests the production path, and embedded mode can't double-deliver. Cost: ~1ms local round-trip, invisible against multi-second phases.
5. **Reconnect loudly, fail startup visibly** — listener connect failure in `main.lifespan` raises (app doesn't start; the sweep just proved DB reachable). A watchdog probes the LISTEN connection every 5s (`SELECT 1` forces dead-socket detection) and reconnects with exponential backoff, `log.error` on every failure — a silently dead listener reproduces the exact frozen-"Queued"-chip bug being fixed.
6. **`close()` crosses processes too** — the stream-end sentinel travels as a reserved `__close__` event over NOTIFY; the dispatcher translates it to the `None` queue sentinel. A local-only close would leak subscribers on every fleet job.
7. **Unit tests stay DB-free via a conftest loopback** — an autouse fixture routes `_notify` → `_dispatch` in-process, preserving old delivery semantics for the existing suite while still exercising the real encode→dispatch path. The cross-process RED test publishes from a real subprocess against the scratch DB.

Load-bearing facts verified against code: bus is a 27-line in-process dict (`events_bus.py`); subscribers `books.py:353` / `jobs.py:536`; publishers `toc_extractor.py` (4 sites + `close`) and `pipeline.py` (6 sites + `close`), all running in worker processes on the fleet; only 3 test files touch the bus, all via monkeypatch (no dict poking); no test runs `main.lifespan` (`TestClient` never used as a context manager; httpx `ASGITransport` doesn't run lifespan); standalone workers run `main:app` (only `main.py` builds workers), so lifespan wiring covers every pod; `tests/conftest.py` promises unit tests never open a DB connection (sentinel `DATABASE_URL`).

## Global Constraints

- Branch `feat/sse-multipod-bus` in worktree `../HCGA-sse-bus`, commits prefixed `sse:`, PR to GK2's gate (implementer never self-merges).
- Public bus API surface (`subscribe(resource_id)`, `unsubscribe(resource_id, q)`, `publish(resource_id, event, data)`, `close(resource_id)`) stays signature-identical. Publishers (`pipeline.py`, `toc_extractor.py`) are NOT modified except one-line invariant comments.
- No migration — slot 0045 stays free. No FE changes expected (no tsc/build gate unless FE touched).
- Stage only the files each task lists; never `git add -A` (parallel lanes commit to the same branch family).
- Canonical green bar = `uv run python -m pytest tests/ -q` WITHOUT `RUN_DB_INTEGRATION` (~1450 tests). Integration tests: scratch DB, pin `127.0.0.1` not `localhost`, run `tests/integration` as a DIR.
- Worktree trap: `find_dotenv` walks up and can load a stale parent `.env`; integration runs must `export DATABASE_URL` explicitly (env wins — `load_dotenv(override=False)`).
- No paid model calls anywhere in this plan.
- A publish failure must never fail the job/extract that emitted it (progress events are advisory) — but it must log loudly.
- Worklog **0128** (0127 taken by the lesson-filter lane; re-verify INDEX.md at finish). Collision note: that lane touches `books.py` `_enriched_toc_entries` (~:465) and `batch.py`/`launcher.tsx`; our `books.py` surface is the SSE region (~:300-367) — second-to-merge rebases and re-runs.

---

### Task 1: RED cross-process integration test

Proves the bug: a publisher in a SEPARATE PROCESS never reaches an in-process subscriber on the current bus. This test is committed RED (it's gated behind `RUN_DB_INTEGRATION`, so the canonical suite stays green) and turns GREEN in Task 7.

**Files:**
- Create: `tests/integration/test_events_bus_crossproc.py`

**Interfaces:**
- Consumes: current `events_bus.subscribe/unsubscribe`; `start_listener`/`stop_listener` via `getattr` (don't exist yet — the fallback is what lets this test RUN and FAIL on the old bus, demonstrating the gap rather than erroring on a missing attribute).
- Produces: the acceptance test for the whole feature (Task 7 re-runs it GREEN).

- [ ] **Step 1: Write the failing test**

```python
"""Cross-process SSE bus proof (sse-multipod-1).

A publisher running in a SEPARATE PROCESS (like every fleet worker) must
reach a subscriber in THIS process. On the old in-process dict bus the
subprocess publishes into its own dict and this test fails on timeout —
that failure IS the frozen-"Queued"-chip bug. The ``getattr`` fallbacks for
``start_listener``/``stop_listener`` are kept so the test demonstrates the
gap when run against the pre-NOTIFY bus (RED provenance).

Needs RUN_DB_INTEGRATION=1 + a real DATABASE_URL (scratch DB, 127.0.0.1).
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres"
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Publishes one small event (must arrive inline), one oversized toc_ready
# (must arrive as a refetch marker), then close() — from its own interpreter,
# exactly like a fleet worker process.
PUBLISH_SCRIPT = """
import asyncio, sys

from app.services import events_bus

async def main():
    rid = sys.argv[1]
    await events_bus.publish(
        rid, "phase_started", {"phase_name": "flashcards", "phase_order": 2}
    )
    big = {"entries": [
        {"section_title": "L" * 80, "order_index": i} for i in range(120)
    ]}
    await events_bus.publish(rid, "toc_ready", big)
    await events_bus.close(rid)

asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_cross_process_publish_reaches_local_subscriber():
    from app.services import events_bus

    start = getattr(events_bus, "start_listener", None)
    stop = getattr(events_bus, "stop_listener", None)
    if start is not None:
        await start()
    rid = f"book:{uuid4()}"
    q = events_bus.subscribe(rid)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", PUBLISH_SCRIPT, rid,
            cwd=str(REPO_ROOT), env=dict(os.environ),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        assert proc.returncode == 0, f"publisher failed: {err.decode()[-2000:]}"

        first = await asyncio.wait_for(q.get(), timeout=10)
        assert first == {
            "event": "phase_started",
            "data": {"phase_name": "flashcards", "phase_order": 2},
        }

        second = await asyncio.wait_for(q.get(), timeout=10)
        assert second["event"] == "toc_ready"
        assert second["data"].get("__refetch__") is True   # oversized → marker
        assert "entries" not in second["data"]             # big field dropped

        sentinel = await asyncio.wait_for(q.get(), timeout=10)
        assert sentinel is None                            # close() crosses too
    finally:
        events_bus.unsubscribe(rid, q)
        if stop is not None:
            await stop()
```

- [ ] **Step 2: Run it against the CURRENT bus to record RED**

```bash
cd ../HCGA-sse-bus
createdb edu_scratch_sse 2>/dev/null || true      # macmini5 = passwordless superuser on 127.0.0.1
export DATABASE_URL='postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_sse'
uv run alembic upgrade head
RUN_DB_INTEGRATION=1 uv run python -m pytest tests/integration/test_events_bus_crossproc.py -v
```

Expected: FAIL with `asyncio.TimeoutError` on `first = await asyncio.wait_for(q.get(), timeout=10)` — the subprocess published into its own process's dict. Copy the failure line into the commit message.

(Pin `127.0.0.1`, never `localhost` — IPv4/IPv6 resolve to two different PG servers on this host. Never point this at `edu_copy` — that's production. The subprocess inherits the pytest process's env: `tests/conftest.py`'s `os.environ.setdefault` sentinels cover `GEMINI_API_KEY`, and the exported scratch `DATABASE_URL` wins over any `.env`.)

- [ ] **Step 3: Verify the canonical suite ignores it**

Run: `uv run python -m pytest tests/integration/test_events_bus_crossproc.py -q` (no flag)
Expected: `1 skipped`

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_events_bus_crossproc.py
git commit -m "sse: RED cross-process bus test — subprocess publisher never reaches subscriber (times out on the in-process dict bus)"
```

---

### Task 2: Payload codec + dispatcher in events_bus

Pure in-process parts: `_encode` (inline vs refetch marker, byte-measured) and `_dispatch` (route decoded NOTIFY payloads to local queues, translate `__close__` to the `None` sentinel). Nothing is rewired yet — `publish`/`close` still use the old dict path, so the whole suite is untouched by this task.

**Files:**
- Modify: `app/services/events_bus.py`
- Create: `tests/services/test_events_bus.py`

**Interfaces:**
- Produces: `events_bus.CHANNEL = "hw_events"`, `events_bus.INLINE_LIMIT_BYTES = 7000`, `events_bus._CLOSE_EVENT = "__close__"`, `_encode(resource_id, event, data) -> str`, `_dispatch(payload_json: str) -> None`. Tasks 3-6 consume all of these.

- [ ] **Step 1: Write the failing tests**

```python
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
```

Note: `subscribe` returns an unbounded `asyncio.Queue`; `get_nowait`/`put_nowait` need no event loop, so these tests are plain sync functions.

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: FAIL with `AttributeError: module 'app.services.events_bus' has no attribute '_encode'`

- [ ] **Step 3: Implement codec + dispatcher**

Add to `app/services/events_bus.py` (keep existing `subscribe`/`unsubscribe`/`publish`/`close` as-is for now; add imports `json`, `from loguru import logger as log`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite still green**

Run: `uv run python -m pytest tests/ -q`
Expected: same pass count as base (no behavior rewired yet)

- [ ] **Step 6: Commit**

```bash
git add app/services/events_bus.py tests/services/test_events_bus.py
git commit -m "sse: payload codec (inline/refetch marker, byte-measured) + local dispatcher"
```

---

### Task 3: Rewire publish/close through NOTIFY + conftest loopback

The delivery flip: `publish()`/`close()` send NOTIFY via the pooled engine — no direct local-queue put. The conftest loopback keeps the existing unit suite DB-free while exercising the real encode→dispatch path.

**C1 (approval condition):** pg_notify is transactional — it fires on commit of whatever transaction it runs in and is silently DROPPED on rollback. `_notify` must therefore run on its own short-lived committed connection (`engine.begin()` acquires a fresh pooled connection and commits on exit), never enlisted in an ambient caller session/transaction — a publish riding a caller's long transaction is delayed until that commit (frozen chips in a subtler costume) or vanishes on rollback. Publishers already publish post-commit, so this is belt-and-suspenders — pinned by an integration test (Step 4a below) because it's the kind of invariant that silently rots.

**Files:**
- Modify: `app/services/events_bus.py`
- Modify: `tests/conftest.py`
- Modify: `pyproject.toml` (register the `real_events_bus` pytest marker)
- Modify: `tests/services/test_events_bus.py` (append tests)
- Modify: `tests/integration/test_events_bus_crossproc.py` (append the C1 test — runs GREEN in Task 7, after the listener exists)

**Interfaces:**
- Consumes: `_encode`/`_dispatch` from Task 2.
- Produces: `_notify(payload: str) -> None` (the seam the conftest loopback and Task 4's listener complement); `publish`/`close` now NOTIFY-only; `@pytest.mark.real_events_bus` (opts a test out of the loopback). Task 6's endpoints rely on refetch markers arriving in queues exactly as `_dispatch` shapes them.

- [ ] **Step 1: Write the failing tests (append to `tests/services/test_events_bus.py`)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: the three new tests FAIL (`test_publish_is_notify_only` sees the direct local put / missing `_notify`)

- [ ] **Step 3: Implement NOTIFY-backed publish/close**

Replace the old `publish`/`close` in `app/services/events_bus.py`:

```python
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
```

Add `from sqlalchemy import text` to the module imports. Replace the module docstring with the delivery-model one:

```python
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
```

- [ ] **Step 4: Add the loopback fixture to `tests/conftest.py`**

Append at the end (add `import pytest` to the imports):

```python
@pytest.fixture(autouse=True)
def _loopback_events_bus(request, monkeypatch):
    """Unit tests never open a DB connection (module docstring above) — but
    the NOTIFY-backed events bus would. Route ``_notify`` (the ENCODED wire
    payload, post-``_encode``) straight into the local dispatcher: old
    in-process delivery semantics preserved, the real encode → wire-bytes →
    dispatch path still exercised. ``@pytest.mark.real_events_bus`` opts out
    (integration tests that need real pg_notify semantics); the cross-process
    test's publisher is a subprocess outside pytest and is unaffected anyway."""
    if request.node.get_closest_marker("real_events_bus"):
        yield
        return
    from app.services import events_bus

    async def _loopback(payload: str) -> None:
        events_bus._dispatch(payload)

    monkeypatch.setattr(events_bus, "_notify", _loopback)
    yield
```

Register the marker in `pyproject.toml` under the existing `[tool.pytest.ini_options]`:

```toml
markers = [
    "real_events_bus: opt out of the conftest _notify loopback (needs real pg_notify)",
]
```

Also update the conftest module docstring's claim if needed — it stays true (still no real DB), just note the bus loopback.

- [ ] **Step 4a: Append the C1 transactional-isolation test to `tests/integration/test_events_bus_crossproc.py`**

Proves `_notify` commits on its own connection: delivery happens while an unrelated ambient session transaction is still OPEN in the same process, and that transaction's later rollback doesn't retract the event. (Uses `start_listener` via the same `getattr` pattern; first runs GREEN in Task 7 — at Task 3 time the listener doesn't exist yet, and the canonical suite skips it.)

```python
@pytest.mark.real_events_bus
@pytest.mark.asyncio
async def test_publish_fires_despite_unrelated_open_transaction():
    """C1: pg_notify is transactional — fires on commit, dropped on rollback.
    publish() must run it on its own short-lived committed connection, never
    enlisted in an ambient caller transaction (which would delay delivery
    until that commit, or swallow it on rollback)."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services import events_bus

    start = getattr(events_bus, "start_listener", None)
    stop = getattr(events_bus, "stop_listener", None)
    if start is not None:
        await start()
    rid = f"job:{uuid4()}"
    q = events_bus.subscribe(rid)
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))   # ambient tx now OPEN, never committed
            await events_bus.publish(rid, "phase_started", {"phase_order": 1})
            # Delivered BEFORE the ambient tx resolves → publish did not enlist.
            got = await asyncio.wait_for(q.get(), timeout=10)
            assert got == {"event": "phase_started", "data": {"phase_order": 1}}
            await s.rollback()                  # and rollback can't retract it
    finally:
        events_bus.unsubscribe(rid, q)
        if stop is not None:
            await stop()
```

- [ ] **Step 5: Run the bus tests, then the full suite**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: all PASS
Run: `uv run python -m pytest tests/ -q`
Expected: green, same count as base + new tests. If any test fails because it relied on real-`publish` local delivery semantics beyond the loopback (audit says none should — only `test_toc_extractor*.py` monkeypatch `publish`/`close` and `test_stream_cancelled.py` patches `subscribe`), fix the TEST, not the bus, and note it in the commit message.

- [ ] **Step 6: Commit**

```bash
git add app/services/events_bus.py tests/services/test_events_bus.py tests/conftest.py pyproject.toml tests/integration/test_events_bus_crossproc.py
git commit -m "sse: publish/close go NOTIFY-only via pooled engine (own committed tx — C1) + conftest loopback + C1 integration test"
```

---

### Task 4: LISTEN connection lifecycle + reconnect watchdog

**Files:**
- Modify: `app/services/events_bus.py`
- Modify: `tests/services/test_events_bus.py` (append tests)

**Interfaces:**
- Consumes: `_dispatch`, `CHANNEL` from Task 2.
- Produces: `start_listener() -> None` (raises on connect failure), `stop_listener() -> None`, `_connect() -> asyncpg.Connection`, `_watchdog()`, `_dsn_from(url: str) -> str`, module globals `_listener_conn`, `_listener_task`, `_stopping`, `_POLL_SECONDS = 5.0`, `_BACKOFF_MAX = 30.0`. Task 5 wires start/stop into lifespan; Task 1's test uses them via getattr.

- [ ] **Step 1: Write the failing tests (append to `tests/services/test_events_bus.py`)**

```python
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
```

Add `import asyncio` and `import contextlib` to the test file imports.

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: new tests FAIL with `AttributeError` (`_dsn_from`, `start_listener` missing)

- [ ] **Step 3: Implement the listener**

Add to `app/services/events_bus.py` (add `import asyncpg` to imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_events_bus.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: green

- [ ] **Step 6: Commit**

```bash
git add app/services/events_bus.py tests/services/test_events_bus.py
git commit -m "sse: LISTEN lifecycle — start/stop + SELECT-1 watchdog with loud exponential-backoff reconnect"
```

---

### Task 5: Wire the listener into main.lifespan

**Files:**
- Modify: `main.py`
- Modify: `tests/services/test_events_bus.py` (append one test)

**Interfaces:**
- Consumes: `events_bus.start_listener`/`stop_listener` from Task 4.

- [ ] **Step 1: Write the failing test (append to `tests/services/test_events_bus.py`)**

Repo precedent for lifespan assertions is source inspection (`tests/services/test_reclaim_window.py:14`) — no test runs the real lifespan (it needs a live DB):

```python
def test_lifespan_wires_listener_start_and_stop():
    import inspect

    import main

    src = inspect.getsource(main.lifespan)
    assert "events_bus.start_listener()" in src
    assert "events_bus.stop_listener()" in src
    # start must NOT be wrapped in a swallowing try — startup failure is loud.
    before_start = src.split("events_bus.start_listener()")[0]
    assert not before_start.rstrip().endswith("try:")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_events_bus.py::test_lifespan_wires_listener_start_and_stop -v`
Expected: FAIL (`start_listener` not in source)

- [ ] **Step 3: Wire it in `main.py`**

Add `events_bus` to the existing `app.services` imports (`from app.services import ...` — follow the file's import style; currently worker/prompts are imported individually, so add `from app.services import events_bus`).

After the orphan-sweep block (after the `log.info("Orphan sweep complete (books + phase_outputs)")` line, before the embedded-worker block):

```python
    # Cross-process SSE bus (sse-multipod-1): one LISTEN connection per
    # process routes NOTIFY events into local SSE queues. Deliberately no
    # try/except — a process that can't LISTEN would serve frozen streams,
    # which is the exact bug this bus fixes. The sweep above already proved
    # the DB reachable.
    await events_bus.start_listener()
```

In the `finally:` block, after the embedded-worker shutdown (after `log.info("Embedded worker stopped")`, dedented to the `finally` body level so it runs even with no embedded worker):

```python
        await events_bus.stop_listener()
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/services/test_events_bus.py tests/services/test_reclaim_window.py -v`
Expected: PASS (reclaim-window test still passes — it inspects the same source)

- [ ] **Step 5: Full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: green (no test runs lifespan — verified: no `with TestClient` anywhere; ASGITransport doesn't run lifespan)

- [ ] **Step 6: Commit**

```bash
git add main.py tests/services/test_events_bus.py
git commit -m "sse: lifespan starts the LISTEN connection (loud on failure) and stops it on shutdown"
```

---

### Task 6: SSE endpoint refetch branches (jobs + books)

When a queue payload carries `__refetch__: true`, the endpoint rebuilds the data from the DB. The jobs endpoint rebuilds `phase_completed` from `phase_outputs` (same shape as its initial replay, `jobs.py:483-492`) and `error` from `job.error_message`; the books endpoint rebuilds `toc_ready`/`toc_review` through the shared `_enriched_toc_entries` helper (identical shape to its initial replay — and composes with the lesson-filter lane's `entry_class` addition for free) and `error` from `book.error_message`.

Also add the persisted-before-publish invariant comments at the two oversized-publish sites (the one publisher-file change allowed): verify the row/entries are committed BEFORE the publish and say so — if any site publishes before commit, STOP and report (that's a reorder decision for the controller, not a silent fix).

**Files:**
- Modify: `app/api/v1/jobs.py` (stream loop ~536-548 + new helper)
- Modify: `app/api/v1/books.py` (stream loop ~353-365 + new helper)
- Modify: `app/services/pipeline.py` (one comment line above the `phase_completed` publish, currently ~:588)
- Modify: `app/services/toc_extractor.py` (one comment line above the `toc_ready`/`toc_review` publishes, currently ~:130)
- Create: `tests/api/test_stream_refetch.py`

**Interfaces:**
- Consumes: refetch-marker payload shape from Task 2 (`{"__refetch__": True, …hint fields}` in `payload["data"]`).
- Produces: `jobs._refetch_job_event(job_id: UUID, event: str, marker: dict) -> dict`, `books._refetch_book_event(book_id: UUID, event: str, marker: dict) -> dict` (module-private helpers).

- [ ] **Step 1: Write the failing tests**

`tests/api/test_stream_refetch.py` (mirrors `tests/api/test_stream_cancelled.py`'s fake-session pattern):

```python
"""Oversized bus events arrive as ``__refetch__`` markers (NOTIFY caps at
~8KB); the SSE endpoints must rebuild the full data from the DB — through
the same serialization as their initial replay, so clients see one schema
regardless of payload size."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.api.v1.books as books_mod
import app.api.v1.jobs as jobs_mod


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_refetch_job_event_rebuilds_phase_completed_from_db():
    jid = uuid4()
    phase = SimpleNamespace(
        phase_name="reading", phase_order=7, status="done",
        output_md="# full markdown " + "x" * 10_000,
        tokens_input=1000, tokens_output=2000,
    )
    job = SimpleNamespace(id=jid, phase_outputs=[phase])
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=job)):
        data = await jobs_mod._refetch_job_event(
            jid, "phase_completed",
            {"__refetch__": True, "phase_name": "reading", "phase_order": 7,
             "tokens_input": 1000, "tokens_output": 2000},
        )
    assert data == {
        "phase_name": "reading",
        "phase_order": 7,
        "output_md": phase.output_md,
        "tokens_input": 1000,
        "tokens_output": 2000,
    }


@pytest.mark.asyncio
async def test_refetch_job_event_rebuilds_error_from_db():
    jid = uuid4()
    job = SimpleNamespace(id=jid, error_message="boss-arena: " + "e" * 9000)
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get", AsyncMock(return_value=job)):
        data = await jobs_mod._refetch_job_event(
            jid, "error", {"__refetch__": True, "phase_name": "boss-arena"}
        )
    assert data["message"] == job.error_message
    assert data["phase_name"] == "boss-arena"     # inline hint preserved
    assert "__refetch__" not in data


@pytest.mark.asyncio
async def test_refetch_job_event_falls_back_to_hints_when_row_missing():
    jid = uuid4()
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=None)):
        data = await jobs_mod._refetch_job_event(
            jid, "phase_completed", {"__refetch__": True, "phase_order": 7}
        )
    assert data == {"phase_order": 7}


@pytest.mark.asyncio
async def test_refetch_book_event_rebuilds_toc_ready_via_enriched_helper():
    bid = uuid4()
    book = SimpleNamespace(id=bid, toc_validation=None, toc_validation_detail=None)
    entry = MagicMock()
    entry.model_dump.return_value = {"section_title": "L1", "order_index": 0}
    with patch.object(books_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(books_mod.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(books_mod, "_enriched_toc_entries",
                      AsyncMock(return_value=[entry])) as enriched:
        data = await books_mod._refetch_book_event(
            bid, "toc_ready", {"__refetch__": True}
        )
    enriched.assert_awaited_once()   # MUST go through the shared helper
    assert data == {"entries": [{"section_title": "L1", "order_index": 0}]}


@pytest.mark.asyncio
async def test_refetch_book_event_toc_review_keeps_inline_validation():
    bid = uuid4()
    book = SimpleNamespace(id=bid, toc_validation="warn", toc_validation_detail="d")
    entry = MagicMock()
    entry.model_dump.return_value = {"section_title": "L1", "order_index": 0}
    inline_validation = {"verdict": "warn", "issues": ["dup pages"]}
    with patch.object(books_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(books_mod.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(books_mod, "_enriched_toc_entries", AsyncMock(return_value=[entry])):
        data = await books_mod._refetch_book_event(
            bid, "toc_review", {"__refetch__": True, "validation": inline_validation}
        )
    # The live publisher's validation dict is small and survives inline —
    # it must win over the DB-derived replay shape.
    assert data["validation"] == inline_validation
    assert data["entries"] == [{"section_title": "L1", "order_index": 0}]


def test_job_stream_loop_refetches_marker_payloads():
    """End-to-end through the endpoint's live loop: a marker payload in the
    queue must yield the REBUILT data, not the marker."""
    jid = uuid4()
    running_job = SimpleNamespace(
        id=jid, status="running", error_message=None, phase_outputs=[]
    )
    phase = SimpleNamespace(
        phase_name="reading", phase_order=7, status="done",
        output_md="FULL", tokens_input=1, tokens_output=2,
    )
    done_job = SimpleNamespace(id=jid, phase_outputs=[phase])

    marker_payload = {
        "event": "phase_completed",
        "data": {"__refetch__": True, "phase_name": "reading", "phase_order": 7,
                 "tokens_input": 1, "tokens_output": 2},
    }
    terminal_payload = {
        "event": "job_completed",
        "data": {"job_id": str(jid), "download_url": f"/api/v1/jobs/{jid}/download"},
    }
    fake_q = SimpleNamespace(
        get=AsyncMock(side_effect=[marker_payload, terminal_payload])
    )

    async def _run():
        resp = await jobs_mod.stream_job(jid, _FakeRequest())
        return [ev async for ev in resp.body_iterator]

    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases",
                      AsyncMock(side_effect=[running_job, done_job])), \
         patch.object(jobs_mod.events_bus, "subscribe", MagicMock(return_value=fake_q)), \
         patch.object(jobs_mod.events_bus, "unsubscribe", MagicMock()):
        events = asyncio.run(_run())

    completed = [e for e in events if e["event"] == "phase_completed"]
    assert completed, events
    body = json.loads(completed[0]["data"])
    assert body["output_md"] == "FULL"
    assert "__refetch__" not in body


class _FakeRequest:
    async def is_disconnected(self):
        return False
```

(Put `_FakeRequest` above its first use; shown last here for readability only.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/api/test_stream_refetch.py -v`
Expected: FAIL with `AttributeError: ... has no attribute '_refetch_job_event'`

- [ ] **Step 3: Implement the jobs helper + loop branch**

In `app/api/v1/jobs.py`, add above `stream_job`:

```python
async def _refetch_job_event(job_id: UUID, event: str, marker: dict) -> dict:
    """Rebuild an oversized bus event from the DB (NOTIFY caps at ~8KB, so
    e.g. phase_completed's output_md travels as a __refetch__ marker). Safe
    because the pipeline persists rows before publishing. Falls back to the
    inline hint fields if the row isn't found."""
    hint = {k: v for k, v in marker.items() if k != "__refetch__"}
    async with SessionLocal() as session:
        if event == "phase_completed":
            job = await jobs_repo.get_with_phases(session, job_id)
            for p in (job.phase_outputs if job else []):
                if p.phase_order == hint.get("phase_order"):
                    return {
                        "phase_name": p.phase_name,
                        "phase_order": p.phase_order,
                        "output_md": p.output_md or "",
                        "tokens_input": p.tokens_input,
                        "tokens_output": p.tokens_output,
                    }
        elif event == "error":
            job = await jobs_repo.get(session, job_id)
            if job is not None and job.error_message:
                return {**hint, "message": job.error_message}
    return hint
```

In the live-subscription loop (`jobs.py:536-548`), replace the yield:

```python
                payload = await q.get()
                if payload is None:
                    break
                data = payload["data"]
                if isinstance(data, dict) and data.get("__refetch__"):
                    data = await _refetch_job_event(job_id, payload["event"], data)
                yield {"event": payload["event"], "data": json.dumps(data)}
```

(The `if payload["event"] in ("job_completed", "error"): break` line stays.)

- [ ] **Step 4: Implement the books helper + loop branch**

In `app/api/v1/books.py`, add near the stream endpoint:

```python
async def _refetch_book_event(book_id: UUID, event: str, marker: dict) -> dict:
    """Rebuild an oversized bus event from the DB — toc_ready with 75+
    enriched entries blows the ~8KB NOTIFY cap. Goes through the shared
    _enriched_toc_entries helper so the refetched shape is byte-identical
    to the inline/replay one (and composes with future changes to it)."""
    hint = {k: v for k, v in marker.items() if k != "__refetch__"}
    async with SessionLocal() as session:
        book = await books_repo.get(session, book_id)
        if book is None:
            return hint
        if event in ("toc_ready", "toc_review"):
            enriched = await _enriched_toc_entries(session, book)
            data: dict = {"entries": [eo.model_dump(mode="json") for eo in enriched]}
            if event == "toc_review":
                # The live publisher's validation dict is small and usually
                # survives inline — prefer it; fall back to the replay shape.
                data["validation"] = hint.get("validation") or {
                    "verdict": book.toc_validation,
                    "detail": book.toc_validation_detail,
                }
            return data
        if event == "error" and book.error_message:
            return {**hint, "message": book.error_message}
    return hint
```

In the live-subscription loop (`books.py:353-365`), same branch:

```python
                payload = await q.get()
                if payload is None:
                    break
                data = payload["data"]
                if isinstance(data, dict) and data.get("__refetch__"):
                    data = await _refetch_book_event(book_id, payload["event"], data)
                yield {"event": payload["event"], "data": json.dumps(data)}
```

(Verify the stream endpoint's signature exposes `book_id: UUID`; it does — `resource_id = f"book:{book_id}"`. `_refetch_book_event` must be defined AFTER `_enriched_toc_entries` or reference it lazily; module-level def order in Python only matters at call time, so plain reference is fine.)

- [ ] **Step 5: Add the invariant comments at the two publish sites**

First VERIFY, then comment. In `app/services/toc_extractor.py`: confirm `session.commit()` (~:120) precedes the `toc_ready`/`toc_review` publishes (~:130-144); add above the `if final_status == "toc_review":` line:

```python
        # Refetch invariant (events_bus): entries are committed above BEFORE
        # these publishes — an oversized payload's __refetch__ marker makes
        # the SSE endpoint re-read them. Do not reorder publish before commit.
```

In `app/services/pipeline.py`: confirm the phase row (status=done + output_md) is committed before the `phase_completed` publish (~:588); add the analogous comment above that publish. **If either site publishes before commit, STOP and report BLOCKED** — reordering is a controller decision.

- [ ] **Step 6: Run tests**

Run: `uv run python -m pytest tests/api/test_stream_refetch.py tests/api/test_stream_cancelled.py tests/api/test_sse_session_no_yield.py -v`
Expected: all PASS

- [ ] **Step 7: Full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: green

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/jobs.py app/api/v1/books.py app/services/pipeline.py app/services/toc_extractor.py tests/api/test_stream_refetch.py
git commit -m "sse: SSE endpoints rebuild __refetch__ markers from DB (jobs via phase_outputs, books via _enriched_toc_entries) + persisted-before-publish invariant comments"
```

---

### Task 7: GREEN the cross-process test (acceptance)

**Files:**
- Modify: `tests/integration/test_events_bus_crossproc.py` (only if a real defect surfaces — expected: no change)

**Interfaces:**
- Consumes: everything from Tasks 2-5.

- [ ] **Step 1: Run the Task 1 test against the new bus**

```bash
cd ../HCGA-sse-bus
export DATABASE_URL='postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_sse'
RUN_DB_INTEGRATION=1 uv run python -m pytest tests/integration/test_events_bus_crossproc.py -v
```

Expected: PASS — **both** tests in the file: the cross-process test (subprocess publisher → NOTIFY → this process's listener → queue: small event inline, oversized `toc_ready` as `__refetch__` marker, `close()` sentinel crossing processes) and the C1 transactional-isolation test (delivery while an unrelated ambient transaction is open; rollback can't retract). This is the plan's acceptance gate (two local processes against the scratch DB, pipeline-shaped events, oversized payload — no paid calls, no fleet dependency).

Caveats: run from the worktree with `DATABASE_URL` **exported** (worktree stale-`.env` trap — env wins over `.env` because `load_dotenv(override=False)`); assert the failure/pass is from worktree code if anything looks off (`python -c "from app.services import agent; print(agent.__file__)"`).

- [ ] **Step 2: Run the whole integration dir (regression, as a DIR — single files hit MissingGreenlet)**

```bash
RUN_DB_INTEGRATION=1 uv run python -m pytest tests/integration -q
```

Expected: no NEW failures vs the known pre-existing isolation failures (compare against a baseline run on the base commit if unsure).

- [ ] **Step 3: Full canonical suite**

Run: `uv run python -m pytest tests/ -q` (no flag)
Expected: green

- [ ] **Step 4: Commit (only if the test needed a fix; otherwise record the GREEN run in the ledger)**

```bash
git add tests/integration/test_events_bus_crossproc.py
git commit -m "sse: cross-process acceptance GREEN — subprocess publisher reaches API-pod subscriber via LISTEN/NOTIFY"
```

If no file changed, make no commit — note the GREEN evidence for the PR body instead.

---

### Task 8: Docs de-stale + worklog + finish bookkeeping

**Files:**
- Modify: `docs/HOW_IT_WORKS.md` (the section describing SSE / queue+worker — find the live-progress description and update to LISTEN/NOTIFY)
- Modify: `docs/CODE_MAP.md` (the `events_bus.py` entry)
- Modify: `docs/memory/MASTER_MEMORY.md` (worklog 0128), `docs/memory/INDEX.md` (row), `docs/memory/WISHLIST.md` (close `sse-multipod-1`)
- Move: `git mv docs/superpowers/plans/2026-07-07-sse-multipod-bus.md docs/superpowers/plans/shipped/`

- [ ] **Step 1: Update `docs/HOW_IT_WORKS.md`** — wherever it describes live progress / the event bus (search for "events_bus", "SSE", "stream"), replace the in-process description with: bus is Postgres LISTEN/NOTIFY on the single `hw_events` channel; publishers NOTIFY via the engine pool from any process; each `main.lifespan` process holds one LISTEN connection + watchdog; oversized payloads (>7000 encoded bytes) become `__refetch__` markers the SSE endpoints rebuild from the DB; `close()` travels as `__close__`.

- [ ] **Step 2: Update `docs/CODE_MAP.md`** — `events_bus.py` line: "Cross-process SSE bus over Postgres LISTEN/NOTIFY (single `hw_events` channel; NOTIFY-only delivery; oversized payloads → `__refetch__` markers rebuilt by the SSE endpoints; listener wired in `main.lifespan`)."

- [ ] **Step 3: Worklog 0128 in `docs/memory/MASTER_MEMORY.md`** (follow the file's entry format; **re-verify 0128 is still the next number in `docs/memory/INDEX.md`** — 0127 was taken by the lesson-filter lane; if the index moved, renumber). Must include the **DEPLOY NOTE**: the fix only helps once FLEET WORKERS run it too — publishers live in the worker processes; a head-only deploy changes nothing. Rollout = fleet pull + restart on every worker host (stale-worker "Oliver" discipline, worklog 0125, applies). Also note: listener failure now blocks API startup by design; watchdog logs `events_bus: LISTEN connection DOWN` loudly — grep worker/head logs for it when streams freeze.

- [ ] **Step 4: Add the INDEX.md row; close `sse-multipod-1` in `docs/memory/WISHLIST.md`** (strike/remove per the file's convention, pointing at worklog 0128).

- [ ] **Step 5: Move the plan**

```bash
git mv docs/superpowers/plans/2026-07-07-sse-multipod-bus.md docs/superpowers/plans/shipped/
```

- [ ] **Step 6: Commit (verify contents, not just exit code — `git add` with one bad pathspec stages NOTHING)**

```bash
git add docs/HOW_IT_WORKS.md docs/CODE_MAP.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md
git commit -m "sse: worklog 0128 + de-stale HOW_IT_WORKS/CODE_MAP + close sse-multipod-1 + ship plan"
git show --stat HEAD   # confirm every listed file is IN the commit
```

(The `git mv` is already staged by `git mv` itself; `git show --stat` must list the rename too.)

---

### Finish (controller, after all tasks + final review)

1. Full suite green: `uv run python -m pytest tests/ -q`.
2. Rebase check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — if the base moved (lesson-filter lane!), rebase onto `origin/Nggaev-v2`, resolve (expected collision surface: `books.py` — their `_enriched_toc_entries` region vs our SSE region; distinct hunks, should auto-merge), re-run the suite AND the integration test.
3. Push `feat/sse-multipod-bus`, open PR to `Nggaev-v2`, route to GK2's gate — GK2 merges; implementer never self-merges. PR body: RED→GREEN evidence from Tasks 1/7, the three locked decisions, the deploy note.
