"""Wiring tests for BE-16 task 5: agent._spawn_once's api branch acquires a
fleet-wide per-credential slot around the api_transport.generate() call, and
releases it via a shielded finally (mirrors worker.py's documented
uncancel/shield craft).

Everything here is mocked at the module-function level — `credential_id`,
`credential_limiter`, and `api_transport` — so these tests never touch
Postgres or a real subprocess/SDK. `SessionLocal` is stubbed with a trivial
async context manager since `credential_limiter.resolve_limit` (itself
mocked here) is the only thing that would use the session.

Review fix (task-5 CRITICAL finding): the entire limiter chain — credential
lookup, `resolve_limit`, `acquire`, `generate`, and the shielded release
`finally` — must run INSIDE `async with _semaphore():`, with the local
semaphore entered FIRST. Two tests below (`test_semaphore_entered_before_
acquire_and_released_before_exit` and `test_real_semaphore_blocks_limiter_
acquire_until_local_slot_free`) prove that ordering directly.
"""
from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import pytest

from app.config import settings
from app.services import agent as agent_module
from app.services import api_transport as api_transport_module
from app.services import credential_id, credential_limiter


class _StubProvider:
    """Minimal stand-in for a Provider — _spawn_once only reads ``.name``."""

    def __init__(self, name: str = "gemini") -> None:
        self.name = name


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.fixture(autouse=True)
def _fake_session_local(monkeypatch):
    monkeypatch.setattr(agent_module, "SessionLocal", _FakeSession)


@pytest.fixture(autouse=True)
def _reset_bypass_throttle():
    agent_module._last_bypass_log_at = float("-inf")
    agent_module._bypass_count_since_log = 0
    yield
    agent_module._last_bypass_log_at = float("-inf")
    agent_module._bypass_count_since_log = 0


def _patch_resolve(monkeypatch, limit: int = 8):
    async def fake_resolve(session, provider, credential):
        return limit

    monkeypatch.setattr(credential_limiter, "resolve_limit", fake_resolve)


# ─────────────────────────────────────────────────────────────────────
# Review fix (task-5 CRITICAL finding): the local `_semaphore()` must be
# entered BEFORE the fleet-credential-limiter chain (credential_for /
# resolve_limit / acquire), and the shielded release must complete BEFORE
# the semaphore is exited — otherwise a won fleet slot can be held while
# this call queues for a local slot, and the local concurrency cap no
# longer bounds how many callers poll `acquire` at once.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semaphore_entered_before_acquire_and_released_before_exit(monkeypatch):
    """Probe semaphore + fake limiter recording timestamps: prove
    sem-enter -> acquire -> generate -> release -> sem-exit, in that order."""
    events: list[tuple[str, float]] = []

    class _ProbeSemaphore:
        async def __aenter__(self):
            events.append(("sem_enter", perf_counter()))
            return self

        async def __aexit__(self, *exc_info):
            events.append(("sem_exit", perf_counter()))
            return False

    monkeypatch.setattr(agent_module, "_semaphore", lambda: _ProbeSemaphore())
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        events.append(("acquire", perf_counter()))
        return "slot-1"

    async def fake_release(slot_id):
        events.append(("release", perf_counter()))

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        events.append(("generate", perf_counter()))
        return (0, "ok", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    rc, text, _usage, _stderr = await agent_module._spawn_once(
        provider=_StubProvider("claude"), model="claude-x", prompt="hi",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")

    labels = [label for label, _ts in events]
    assert labels == ["sem_enter", "acquire", "generate", "release", "sem_exit"]

    by_label = dict(events)
    # The load-bearing ordering property (timestamps, not just label order):
    # semaphore-enter strictly precedes the limiter acquire, and the
    # (shielded) release strictly precedes semaphore-exit.
    assert by_label["sem_enter"] <= by_label["acquire"]
    assert by_label["acquire"] <= by_label["generate"]
    assert by_label["generate"] <= by_label["release"]
    assert by_label["release"] <= by_label["sem_exit"]


@pytest.mark.asyncio
async def test_real_semaphore_blocks_limiter_acquire_until_local_slot_free(monkeypatch):
    """With a real (size-1) local semaphore held by someone else, the
    fleet-credential-limiter chain must never even be polled — proving the
    local slot gates entry to `acquire`, not the other way around. This is
    the concrete regression the review flagged: before the fix, `acquire`
    ran BEFORE `_semaphore()`, so a caller could win a scarce fleet-wide
    credential slot and then sit holding it while queueing locally."""
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(agent_module, "_semaphore", lambda: sem)
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    acquire_called = asyncio.Event()

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        acquire_called.set()
        return "slot-1"

    async def fake_release(slot_id):
        pass

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        return (0, "ok", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    await sem.acquire()  # occupy the only local slot ourselves
    task = asyncio.create_task(
        agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    )
    await asyncio.sleep(0.05)
    assert acquire_called.is_set() is False, (
        "limiter acquire must not run before the local semaphore is free"
    )

    sem.release()
    rc, text, _usage, _stderr = await task
    assert (rc, text) == (0, "ok")
    assert acquire_called.is_set() is True


# ─────────────────────────────────────────────────────────────────────
# acquire before generate, release after (success path)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acquire_before_generate_release_after(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)
    order: list[Any] = []

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        order.append("acquire")
        return "slot-1"

    async def fake_release(slot_id):
        order.append(("release", slot_id))

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        order.append("generate")
        return (0, "ok", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    rc, text, _usage, _stderr = await agent_module._spawn_once(
        provider=_StubProvider("claude"), model="claude-x", prompt="hi",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")
    assert order == ["acquire", "generate", ("release", "slot-1")]


# ─────────────────────────────────────────────────────────────────────
# release on exception AND on cancellation
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_runs_on_provider_exception(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    released: list[Any] = []

    async def fake_release(slot_id):
        released.append(slot_id)

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        raise RuntimeError("boom-provider")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    with pytest.raises(RuntimeError, match="boom-provider"):
        await agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    assert released == ["slot-1"]


@pytest.mark.asyncio
async def test_release_runs_on_cancellation(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    released: list[Any] = []

    async def fake_release(slot_id):
        released.append(slot_id)

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    started = asyncio.Event()

    async def fake_generate(**kwargs):
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    task = asyncio.create_task(
        agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == ["slot-1"]


@pytest.mark.asyncio
async def test_double_cancellation_release_still_completes(monkeypatch):
    """Second cancel arriving while we're awaiting the shielded release must
    NOT stop the release — only detach the caller from waiting for it. The
    ORIGINAL (first) CancelledError still propagates (codex #5)."""
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)

    generate_started = asyncio.Event()
    release_started = asyncio.Event()
    release_may_finish = asyncio.Event()
    released: list[Any] = []

    async def fake_generate(**kwargs):
        generate_started.set()
        await asyncio.sleep(10)

    async def fake_release(slot_id):
        release_started.set()
        await release_may_finish.wait()
        released.append(slot_id)

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    task = asyncio.create_task(
        agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    )
    await generate_started.wait()
    task.cancel()                    # 1st cancel -> aborts generate, enters finally
    await release_started.wait()     # now inside `await asyncio.shield(release_task)`
    task.cancel()                    # 2nd cancel -> hits the shield await itself
    release_may_finish.set()         # let the orphaned release task finish

    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the orphaned release task (still running on the live loop) a
    # chance to actually finish appending.
    for _ in range(50):
        if released:
            break
        await asyncio.sleep(0.01)
    assert released == ["slot-1"]


# ─────────────────────────────────────────────────────────────────────
# Three explicit masking tests (codex #5): a release DB error must never
# mask the model result / an in-flight provider exception / cancellation.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_error_does_not_mask_successful_result(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    async def fake_release(slot_id):
        raise RuntimeError("release db down")

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        return (0, "ok", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    rc, text, _usage, _stderr = await agent_module._spawn_once(
        provider=_StubProvider("claude"), model="m", prompt="x",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")


@pytest.mark.asyncio
async def test_release_error_does_not_mask_provider_exception(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    async def fake_release(slot_id):
        raise RuntimeError("release db down")

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        raise ValueError("provider boom")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    with pytest.raises(ValueError, match="provider boom"):
        await agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )


@pytest.mark.asyncio
async def test_release_error_does_not_swallow_cancellation(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "claude:abc")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return "slot-1"

    async def fake_release(slot_id):
        raise RuntimeError("release db down")

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    started = asyncio.Event()

    async def fake_generate(**kwargs):
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    task = asyncio.create_task(
        agent_module._spawn_once(
            provider=_StubProvider("claude"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ─────────────────────────────────────────────────────────────────────
# two _spawn retry attempts -> two distinct acquire/release pairs, no slot
# held across the backoff sleep
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_retry_attempts_have_distinct_acquire_release_pairs(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")
    _patch_resolve(monkeypatch)

    acquire_calls: list[str] = []
    release_calls: list[str] = []
    ids = iter(["slot-1", "slot-2"])

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        sid = next(ids)
        acquire_calls.append(sid)
        return sid

    async def fake_release(slot_id):
        release_calls.append(slot_id)

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    outputs = [
        (1, "", {"raw": {}}, "429 RESOURCE_EXHAUSTED"),
        (0, "ok", {"raw": {}}, ""),
    ]

    async def fake_generate(**kwargs):
        return outputs.pop(0)

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        # Mid-backoff: the load-bearing invariant — no slot held across it.
        assert len(acquire_calls) == len(release_calls), (
            "a slot must not be held across the backoff sleep"
        )

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)

    rc, text, _usage, _stderr = await agent_module._spawn(
        provider=_StubProvider("gemini"), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")
    assert acquire_calls == ["slot-1", "slot-2"]
    assert release_calls == ["slot-1", "slot-2"]
    assert len(sleeps) == 1


# ─────────────────────────────────────────────────────────────────────
# near-timeout interplay: outer wait_for close to the slot budget (tiny
# values) -> the outer timeout path stays clean, no leaked slot.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_near_timeout_interplay_leaves_no_leaked_slot(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")
    _patch_resolve(monkeypatch, limit=1)
    monkeypatch.setattr(settings, "credential_slot_wait_seconds", 0.4)

    acquired: list[str] = []
    released: list[str] = []

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        # Simulates a saturated fleet: never returns within the outer
        # wait_for's tiny budget, regardless of wait_budget_s.
        await asyncio.sleep(10)
        acquired.append("slot")  # pragma: no cover — should never reach here
        return "slot"

    async def fake_release(slot_id):
        released.append(slot_id)

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):  # pragma: no cover — must never run
        return (0, "should not run", {}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            agent_module._spawn_once(
                provider=_StubProvider("gemini"), model="gemini-2.5-flash",
                prompt="x", attachments=[], transport="api",
            ),
            timeout=0.5,
        )

    assert acquired == []
    assert released == []


# ─────────────────────────────────────────────────────────────────────
# acquire -> None (budget exhausted) -> rate-limited-shaped error consumed
# by _spawn's existing retry loop
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acquire_none_returns_rate_limited_shaped_error(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")
    _patch_resolve(monkeypatch)

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return None

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)

    generate_called = False

    async def fake_generate(**kwargs):
        nonlocal generate_called
        generate_called = True
        return (0, "should not run", {}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    rc, _text, _usage, stderr = await agent_module._spawn_once(
        provider=_StubProvider("gemini"), model="m", prompt="x",
        attachments=[], transport="api",
    )
    assert rc == 1
    assert "429" in stderr
    assert "fleet credential slot wait exhausted" in stderr
    assert generate_called is False
    assert agent_module._is_rate_limited(stderr) is True


@pytest.mark.asyncio
async def test_spawn_returns_slot_exhaustion_without_retry_when_acquire_returns_none(
    monkeypatch,
):
    """New contract (356a639, queue-correctness-1): the acquire-exhausted
    429-shaped tuple is fleet slot saturation, NOT an ordinary provider 429 —
    `_spawn` must return it after exactly ONE `_spawn_once`/`acquire`
    attempt, with no backoff and no second acquire. Retrying in-process
    would re-burn a full `credential_slot_wait_seconds` (120s) fleet-slot
    wait per attempt; instead the pipeline converts the tuple to a
    SlotSaturation signal and the worker parks the job at the queue level
    for a later claim (see tests/services/test_spawn_slot_saturation.py,
    which covers this same contract from the `_spawn` angle)."""
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")
    _patch_resolve(monkeypatch)

    acquire_calls: list[str] = []

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        acquire_calls.append("call")
        return None

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)

    generate_called = False

    async def fake_generate(**kwargs):
        nonlocal generate_called
        generate_called = True
        return (0, "should not run", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    async def no_sleep(_delay):  # pragma: no cover — must not be reached
        raise AssertionError("slot exhaustion must not back off and retry")

    monkeypatch.setattr(agent_module.asyncio, "sleep", no_sleep)

    rc, _text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider("gemini"), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )
    assert rc == 1
    assert "fleet credential slot wait exhausted" in stderr
    assert acquire_calls == ["call"]
    assert generate_called is False


# ─────────────────────────────────────────────────────────────────────
# None credential -> no acquire, no BYPASS log
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_none_credential_skips_acquire_entirely(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: None)

    acquire_called = False

    async def fake_acquire(*a, **k):
        nonlocal acquire_called
        acquire_called = True
        return "slot"

    resolve_called = False

    async def fake_resolve(*a, **k):
        nonlocal resolve_called
        resolve_called = True
        return 8

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "resolve_limit", fake_resolve)

    async def fake_generate(**kwargs):
        return (0, "ok", {}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    errors: list[str] = []
    monkeypatch.setattr(agent_module.logger, "error", lambda msg: errors.append(msg))

    rc, text, _usage, _stderr = await agent_module._spawn_once(
        provider=_StubProvider("claude"), model="m", prompt="x",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")
    assert acquire_called is False
    assert resolve_called is False
    assert errors == []


# ─────────────────────────────────────────────────────────────────────
# BYPASS on DB error still calls generate (+ throttled ERROR log)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_error_bypasses_and_still_calls_generate(monkeypatch):
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")

    async def boom_resolve(session, provider, credential):
        raise RuntimeError("db down")

    monkeypatch.setattr(credential_limiter, "resolve_limit", boom_resolve)

    generate_called = False

    async def fake_generate(**kwargs):
        nonlocal generate_called
        generate_called = True
        return (0, "ok", {}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    errors: list[str] = []
    monkeypatch.setattr(agent_module.logger, "error", lambda msg: errors.append(msg))

    rc, text, _usage, _stderr = await agent_module._spawn_once(
        provider=_StubProvider("gemini"), model="m", prompt="x",
        attachments=[], transport="api",
    )
    assert generate_called is True
    assert (rc, text) == (0, "ok")
    assert any("BYPASSED" in m for m in errors)


def test_bypass_log_throttled_to_once_per_60s(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(agent_module.logger, "error", lambda msg: logged.append(msg))
    fake_clock = {"t": 0.0}
    monkeypatch.setattr(agent_module, "perf_counter", lambda: fake_clock["t"])

    agent_module._log_credential_bypass(RuntimeError("e1"))
    assert len(logged) == 1

    fake_clock["t"] = 10.0
    agent_module._log_credential_bypass(RuntimeError("e2"))
    assert len(logged) == 1  # still inside the 60s throttle window

    fake_clock["t"] = 61.0
    agent_module._log_credential_bypass(RuntimeError("e3"))
    assert len(logged) == 2
    assert "bypass" in logged[1].lower()


# ─────────────────────────────────────────────────────────────────────
# drift guard (final-review fix): an API_PROVIDERS entry with no matching
# credential_max_concurrent_<name> settings field must fail LOUD, not
# silently let resolve_limit's getattr(..., 0) default BYPASS the limiter.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drift_guard_bites_for_provider_missing_settings_field(monkeypatch):
    """Simulates the exact drift the guard exists for: a provider added to
    API_PROVIDERS + credential_for, but with no
    `credential_max_concurrent_<name>` settings field. Before the fix, the
    guard's assert predicate (`provider.name in agent_models.API_PROVIDERS`)
    was tautological — already implied by the `if` gating this whole
    branch — so it never fired, and the call would proceed into
    `resolve_limit`, whose `getattr(..., 0)` default silently returns 0
    (BYPASS), silently disabling the limiter for this provider forever.
    After the fix, this must raise AssertionError naming the missing
    settings field BEFORE `resolve_limit` is ever called."""
    monkeypatch.setattr(
        agent_module.agent_models, "API_PROVIDERS",
        frozenset({"claude", "gemini", "clodex", "fakeprov"}),
    )
    monkeypatch.setattr(
        credential_id, "credential_for",
        lambda provider, env: "fakeprov:abc" if provider == "fakeprov" else None,
    )

    resolve_called = False

    async def fake_resolve(session, provider, credential):
        nonlocal resolve_called
        resolve_called = True
        return 8

    monkeypatch.setattr(credential_limiter, "resolve_limit", fake_resolve)

    acquire_called = False

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        nonlocal acquire_called
        acquire_called = True
        return "slot-1"

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)

    async def fake_release(slot_id):  # pragma: no cover — must never run
        pass

    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):  # pragma: no cover — must never run
        return (0, "should not run", {}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    with pytest.raises(AssertionError, match="credential_max_concurrent_fakeprov"):
        await agent_module._spawn_once(
            provider=_StubProvider("fakeprov"), model="m", prompt="x",
            attachments=[], transport="api",
        )
    assert resolve_called is False
    assert acquire_called is False


# ─────────────────────────────────────────────────────────────────────
# cli transport never touches the limiter
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_transport_never_touches_limiter(monkeypatch):
    credential_for_called = False

    def fake_credential_for(*a, **k):
        nonlocal credential_for_called
        credential_for_called = True
        return "gemini:proj"

    acquire_called = False

    async def fake_acquire(*a, **k):
        nonlocal acquire_called
        acquire_called = True
        return "slot"

    monkeypatch.setattr(credential_id, "credential_for", fake_credential_for)
    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)

    # Short-circuit right after the api-branch check (which cli must not
    # take) so this never actually spawns a subprocess.
    def fake_resolve_binary(provider):
        raise RuntimeError("test-short-circuit: no real subprocess spawn")

    monkeypatch.setattr(agent_module, "_resolve_binary", fake_resolve_binary)

    with pytest.raises(RuntimeError, match="test-short-circuit"):
        await agent_module._spawn_once(
            provider=_StubProvider("gemini"), model="m", prompt="x",
            attachments=[], transport="cli",
        )

    assert credential_for_called is False
    assert acquire_called is False
