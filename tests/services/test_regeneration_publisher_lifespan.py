"""`main.lifespan` wiring for the regeneration publisher.

Two independent flags gate it, so there are four combinations and only ONE of
them may start a loop. That matters more than it looks: the publisher writes to
Notion, so a process that starts it by accident publishes real pages, and the
default for the whole test suite (and for every un-migrated fleet host) is
"both off".

The rest of the file pins the two things a lifespan change is most likely to
break by accident:

* **startup ORDER.** Operator auth, the SA-key vault, prompts, the startup
  database reconcile, the version floor and the LISTEN bus all run before any
  loop starts. The publisher is appended to that sequence, never inserted into
  it — it claims work, and claiming before the version floor is stamped or
  before the crash sweep has run is exactly the shape those steps exist to
  prevent.
* **clean shutdown.** The loop is signalled and awaited like the embedded
  worker, so a shutting-down process does not leave a half-written Notion page
  behind an orphaned task.

Nothing here touches a database, Notion, or a model: every collaborator in
`lifespan` is stubbed, and the publisher itself is replaced by a recording
double.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _RecordingPublisher:
    """Stands in for `RegenerationPublisher`. Records that it ran and honours
    the stop event exactly as the real loop must."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = False
        self.passes = 0

    async def run_forever(self, stop: asyncio.Event) -> None:
        self.started.set()
        while not stop.is_set():
            self.passes += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.01)
            except (asyncio.TimeoutError, TimeoutError):
                pass
        self.stopped = True


class _HangingPublisher(_RecordingPublisher):
    """Ignores the stop event — the shape a wedged Notion call would produce."""

    async def run_forever(self, stop: asyncio.Event) -> None:
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.stopped = True
            raise


@pytest.fixture
def wired(monkeypatch):
    """`main.lifespan` with everything but the publisher stubbed out."""
    import main as main_mod

    order: list[str] = []

    monkeypatch.setattr(
        main_mod.operator_auth, "require_startup_auth",
        lambda *a, **kw: order.append("auth"))
    monkeypatch.setattr(
        main_mod.sa_key_vault, "harden_vault", lambda: order.append("vault"))
    monkeypatch.setattr(
        main_mod, "load_prompts", lambda: order.append("prompts"))

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(main_mod, "SessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(
        main_mod.sa_keys_repo, "uuid_hash_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(
        main_mod.sa_key_vault, "reconcile_delete_quarantines", lambda *a: None)
    monkeypatch.setattr(
        main_mod.sa_key_vault, "verify_uuid_inventory", lambda *a: None)

    async def _reconcile(_session):
        order.append("db-reconcile")

    async def _reconcile_targets():
        order.append("revision-reconcile")

    async def _raise_floor(_session, **_kw):
        order.append("version-floor")
        return False

    async def _start_listener():
        order.append("listener")

    async def _stop_listener():
        order.append("listener-stopped")

    monkeypatch.setattr(main_mod, "_reconcile_on_startup", _reconcile)
    monkeypatch.setattr(
        main_mod, "_reconcile_revision_targets_on_startup", _reconcile_targets)
    monkeypatch.setattr(main_mod.budget_repo, "raise_version_floor", _raise_floor)
    monkeypatch.setattr(main_mod.events_bus, "start_listener", _start_listener)
    monkeypatch.setattr(main_mod.events_bus, "stop_listener", _stop_listener)
    monkeypatch.setattr(main_mod.settings, "worker_concurrency", 0)

    def _use(publisher) -> None:
        def _build():
            order.append("publisher-built")
            return publisher
        monkeypatch.setattr(
            main_mod.regeneration_publisher, "build_publisher_from_settings", _build)

    def _flags(*, enabled: bool, publisher: bool) -> None:
        monkeypatch.setattr(main_mod.settings, "regeneration_enabled", enabled)
        monkeypatch.setattr(
            main_mod.settings, "regeneration_publisher_enabled", publisher)

    return SimpleNamespace(main=main_mod, order=order, use=_use, flags=_flags)


# ═══════════════════════ the four flag combinations ══════════════════════


@pytest.mark.parametrize(
    "enabled, publisher_flag",
    [(False, False), (False, True), (True, False)],
)
async def test_the_publisher_does_not_start_unless_both_flags_are_on(
    wired, enabled, publisher_flag
):
    publisher = _RecordingPublisher()
    wired.use(publisher)
    wired.flags(enabled=enabled, publisher=publisher_flag)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.sleep(0.02)
    assert not publisher.started.is_set()
    assert "publisher-built" not in wired.order


async def test_both_flags_on_starts_the_loop_and_stops_it_cleanly(wired):
    publisher = _RecordingPublisher()
    wired.use(publisher)
    wired.flags(enabled=True, publisher=True)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.wait_for(publisher.started.wait(), timeout=5)
    assert publisher.stopped, "the loop must be signalled and awaited on shutdown"
    assert publisher.passes >= 1


async def test_the_default_settings_leave_the_publisher_off(wired):
    """The suite-wide default, and the default for every fleet host that has not
    been explicitly switched on. A publisher started by accident writes real
    Notion pages."""
    from app.config import Settings

    assert Settings.model_fields["regeneration_enabled"].default is False
    assert Settings.model_fields["regeneration_publisher_enabled"].default is False

    publisher = _RecordingPublisher()
    wired.use(publisher)
    async with wired.main.lifespan(MagicMock()):
        await asyncio.sleep(0.02)
    assert not publisher.started.is_set()


# ═══════════════════════════ ordering ════════════════════════════════════


async def test_the_publisher_starts_after_the_security_and_listener_prelude(wired):
    publisher = _RecordingPublisher()
    wired.use(publisher)
    wired.flags(enabled=True, publisher=True)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.wait_for(publisher.started.wait(), timeout=5)

    built = wired.order.index("publisher-built")
    for step in ("auth", "vault", "prompts", "db-reconcile", "revision-reconcile",
                 "version-floor", "listener"):
        assert wired.order.index(step) < built, (
            f"{step!r} must run before the publisher claims anything"
        )
    assert wired.order.index("listener-stopped") > built


async def test_the_prelude_order_itself_is_unchanged(wired):
    publisher = _RecordingPublisher()
    wired.use(publisher)
    wired.flags(enabled=True, publisher=True)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.wait_for(publisher.started.wait(), timeout=5)

    prelude = [s for s in wired.order if s != "publisher-built"]
    assert prelude == [
        "auth", "vault", "prompts", "db-reconcile", "revision-reconcile",
        "version-floor", "listener", "listener-stopped",
    ]


# ═══════════════════════════ shutdown ════════════════════════════════════


async def test_a_wedged_publisher_is_cancelled_rather_than_blocking_shutdown(wired,
                                                                            monkeypatch):
    publisher = _HangingPublisher()
    wired.use(publisher)
    wired.flags(enabled=True, publisher=True)

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(aw, timeout):
        # the production timeout is 30s; a test must not wait it out
        return await real_wait_for(aw, 0.05 if timeout == 30.0 else timeout)

    monkeypatch.setattr(wired.main.asyncio, "wait_for", _fast_wait_for)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.wait_for(publisher.started.wait(), timeout=5)
    assert publisher.stopped, "a loop that ignores the stop event is cancelled"


async def test_a_publisher_that_raises_does_not_break_shutdown(wired):
    class _Exploding(_RecordingPublisher):
        async def run_forever(self, stop):
            self.started.set()
            raise RuntimeError("publisher blew up")

    publisher = _Exploding()
    wired.use(publisher)
    wired.flags(enabled=True, publisher=True)

    async with wired.main.lifespan(MagicMock()):
        await asyncio.wait_for(publisher.started.wait(), timeout=5)
    # reaching here at all is the assertion: the failing task is awaited and its
    # exception is contained, not re-raised out of the ASGI shutdown
    assert wired.order[-1] == "listener-stopped"
