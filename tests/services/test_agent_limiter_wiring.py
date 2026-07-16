"""Wiring tests for BE-16 task 5: agent._spawn_once's api branch acquires a
fleet-wide per-credential slot around the api_transport.generate() call, and
releases it via a shielded finally (mirrors worker.py's documented
uncancel/shield craft).

Everything here is mocked at the module-function level — `credential_id`,
`credential_limiter`, and `api_transport` — so these tests never touch
Postgres or a real subprocess/SDK. `SessionLocal` is stubbed with a trivial
async context manager since `credential_limiter.resolve_limit` (itself
mocked here) is the only thing that would use the session.
"""
from __future__ import annotations

import asyncio
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
async def test_spawn_retries_when_acquire_returns_none_then_succeeds(monkeypatch):
    """The 429-shaped acquire-exhausted error must feed _spawn's existing
    backoff/retry loop exactly like a real provider 429."""
    monkeypatch.setattr(credential_id, "credential_for", lambda *a, **k: "gemini:proj")
    _patch_resolve(monkeypatch)

    acquire_results = [None, "slot-1"]

    async def fake_acquire(credential, limit, *, wait_budget_s, pc_id=None):
        return acquire_results.pop(0)

    released: list[str] = []

    async def fake_release(slot_id):
        released.append(slot_id)

    monkeypatch.setattr(credential_limiter, "acquire", fake_acquire)
    monkeypatch.setattr(credential_limiter, "release", fake_release)

    async def fake_generate(**kwargs):
        return (0, "ok", {"raw": {}}, "")

    monkeypatch.setattr(api_transport_module, "generate", fake_generate)

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)

    rc, text, _usage, _stderr = await agent_module._spawn(
        provider=_StubProvider("gemini"), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )
    assert (rc, text) == (0, "ok")
    assert len(sleeps) == 1
    assert released == ["slot-1"]


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
