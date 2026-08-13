"""Tests for the Worker._budget_monitor periodic kill-switch (Task 6 / C4).

Architecture: _budget_monitor is a method on Worker. We drive it directly,
mocking the DB boundary (cost_repo, batches_repo, budget_repo, SessionLocal)
so no real database is required.

Each test is designed to FAIL if its specific code branch is removed:
  - over cap → pause call made
  - under cap → unpause call made (only for "batch-cap" reason)
  - cap=0 → NO pause calls for active batches (per-batch check disabled)
  - different reason → NOT touched even when under cap
  - fleet over → set_api_paused called
  - fleet under (own reason) → clear_api_paused called
  - fleet different reason → NOT cleared
"""
from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared: fake BudgetState row
# ---------------------------------------------------------------------------


def _budget_state(
    paused_reason: str | None = None,
    *,
    cap_usd: float | None = None,
    paused_by: str | None = None,
) -> MagicMock:
    s = MagicMock()
    s.api_paused_at = datetime.now(timezone.utc) if paused_reason is not None else None
    s.api_paused_reason = paused_reason
    # Cap-pause provenance (migration 0062). Default None = a pause with no
    # recorded cap/host, i.e. the pre-0062 rows, which keep the historical
    # permissive reconcile. Divergence between hosts is covered by
    # tests/services/test_budget_cap_divergence.py.
    s.api_paused_cap_usd = cap_usd
    s.api_paused_by = paused_by
    return s


# ---------------------------------------------------------------------------
# Helper: run _budget_monitor with fully-controlled mocks
# ---------------------------------------------------------------------------


def _make_settings(*, cost_cap_batch: float, cost_cap_fleet: float) -> MagicMock:
    s = MagicMock()
    s.cost_cap_batch_usd = cost_cap_batch
    s.cost_cap_fleet_daily_usd = cost_cap_fleet
    s.cost_check_interval_seconds = 60
    s.reclaim_stale_seconds = 120
    s.worker_registry_prune_seconds = 7200
    s.queue_max_attempts = 3
    return s


async def _run_monitor(
    *,
    active_batch_ids: list,
    paused_reasons_map: dict,
    batch_costs: dict,
    fleet_cost: float,
    budget_state_reason: str | None,
    cost_cap_batch: float,
    cost_cap_fleet: float,
    patch_batches_pause: AsyncMock,
    patch_batches_unpause: AsyncMock,
    patch_budget_set: AsyncMock,
    patch_budget_clear: AsyncMock,
) -> None:
    """Drive Worker._budget_monitor with fully-controlled mocks.

    ``paused_reasons_map`` is a dict of {batch_id: pause_reason} mirroring the
    DB state faithfully.  The fake ``paused_cap_records`` returns only the rows
    whose stored reason equals the ``reason`` argument it is called with —
    exactly what the real query does.  This means Test 5 bites: if production
    ever calls with a non-scoped list (or drops the reason arg) the wrong IDs
    bleed through and assertions fail.

    Every seeded pause here carries NO provenance (paused_cap_usd/paused_by are
    NULL), i.e. a pre-0062 row — those keep the historical reconcile semantics,
    which is what these tests pin.  The fleet-safety rule for provenance-bearing
    pauses lives in tests/services/test_budget_cap_divergence.py.

    ``patch_batches_unpause`` stands in for the monitor's automatic clear
    (``clear_cap_pause``); the unguarded ``unpause_batch`` primitive is patched
    separately and asserted unused — the monitor must never reach for the
    operator escape hatch.
    """
    from app.services.worker import Worker

    with patch("asyncio.Semaphore", return_value=MagicMock()):
        w = Worker(concurrency=1)
    w._stop_event = MagicMock()
    w.hostname = "test-host"
    w.id = "test-host:1@sha"

    # Session context manager
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_begin = MagicMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    async def _active_ids(session):
        return active_batch_ids

    async def _paused_by_reason(session, reason):
        # Faithful DB filter: return only IDs whose stored reason matches.
        return [bid for bid, r in paused_reasons_map.items() if r == reason]

    async def _paused_cap_records(session, reason):
        # Same filter, plus the (NULL, NULL) provenance of a pre-0062 pause.
        return [(bid, None, None) for bid, r in paused_reasons_map.items() if r == reason]

    unused_unpause_batch = AsyncMock()
    unused_clear_api_paused = AsyncMock()

    async def _batch_cost(session, bid):
        return batch_costs.get(bid, 0.0)

    async def _fleet_cost_fn(session, since):
        return fleet_cost

    async def _get_state(session):
        return _budget_state(budget_state_reason)

    patches = {
        "app.services.worker.SessionLocal": MagicMock(return_value=mock_session),
        "app.services.worker.settings": _make_settings(
            cost_cap_batch=cost_cap_batch, cost_cap_fleet=cost_cap_fleet
        ),
        "app.services.worker.batches_repo.active_batch_ids": _active_ids,
        "app.services.worker.batches_repo.paused_batch_ids_by_reason": _paused_by_reason,
        "app.services.worker.batches_repo.paused_cap_records": _paused_cap_records,
        "app.services.worker.batches_repo.pause_batch": patch_batches_pause,
        "app.services.worker.batches_repo.clear_cap_pause": patch_batches_unpause,
        "app.services.worker.batches_repo.unpause_batch": unused_unpause_batch,
        "app.services.worker.cost_repo.batch_api_cost_usd": _batch_cost,
        "app.services.worker.cost_repo.fleet_api_cost_usd": _fleet_cost_fn,
        "app.services.worker.budget_repo.get_state": _get_state,
        "app.services.worker.budget_repo.set_api_paused": patch_budget_set,
        "app.services.worker.budget_repo.clear_api_pause_if_entitled": patch_budget_clear,
        "app.services.worker.budget_repo.clear_api_paused": unused_clear_api_paused,
        "app.services.worker._utcnow": MagicMock(
            return_value=datetime(2026, 6, 19, tzinfo=timezone.utc)
        ),
    }

    with ExitStack() as stack:
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, mock_obj))
        await w._budget_monitor()

    # The unguarded operator primitives must never be the monitor's clear path —
    # they cannot express "only if I am not relaxing another host's decision".
    unused_unpause_batch.assert_not_awaited()
    unused_clear_api_paused.assert_not_awaited()


# ===========================================================================
# Test 1 — batch OVER cap → pause_batch("batch-cap") called
# BITE: removing `if cost > cap: await batches_repo.pause_batch(...)` makes
#       pause never be called — assert_awaited_once fails.
# ===========================================================================


@pytest.mark.asyncio
async def test_batch_over_cap_pauses_batch():
    bid = uuid.uuid4()
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[bid],
        paused_reasons_map={},
        batch_costs={bid: 0.05},  # $0.05 > $0.01 cap
        fleet_cost=0.0,
        budget_state_reason=None,
        cost_cap_batch=0.01,
        cost_cap_fleet=0.0,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    pause.assert_awaited_once()
    call_args = pause.await_args[0]
    assert call_args[1] == bid, f"pause_batch must receive the over-cap batch_id; got {call_args}"
    assert call_args[2] == "batch-cap", f"pause reason must be 'batch-cap'; got {call_args}"
    unpause.assert_not_awaited()


# ===========================================================================
# Test 2 — batch UNDER cap (paused with "batch-cap") → unpause_batch called
# BITE: removing the reconcile branch → unpause never fires.
# ===========================================================================


@pytest.mark.asyncio
async def test_batch_under_cap_unpauses_if_our_reason():
    bid = uuid.uuid4()
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={bid: "batch-cap"},  # paused with "batch-cap", now under cap
        batch_costs={bid: 0.005},               # $0.005 <= $0.01 cap
        fleet_cost=0.0,
        budget_state_reason=None,
        cost_cap_batch=0.01,
        cost_cap_fleet=0.0,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    unpause.assert_awaited_once()
    call_args = unpause.await_args[0]
    assert call_args[1] == bid, f"unpause_batch must receive the under-cap batch_id; got {call_args}"
    pause.assert_not_awaited()


# ===========================================================================
# Test 3 — caps=0 → active batches NOT paused (per-batch check disabled)
# BITE: removing the `if cap > 0` guard would call pause_batch on active batches.
# ===========================================================================


@pytest.mark.asyncio
async def test_caps_zero_active_batch_not_paused():
    """With cost_cap_batch_usd=0, even a very expensive active batch must not be paused."""
    bid = uuid.uuid4()
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[bid],
        paused_reasons_map={},
        batch_costs={bid: 999.0},  # absurdly expensive — but cap=0 means disabled
        fleet_cost=999.0,
        budget_state_reason=None,
        cost_cap_batch=0.0,  # DISABLED
        cost_cap_fleet=0.0,  # DISABLED
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    # Per-batch pause gate is disabled → must not pause active batches
    pause.assert_not_awaited()
    # Fleet cap disabled and fleet not paused by us → no fleet set
    set_paused.assert_not_awaited()


# ===========================================================================
# Test 4 — fleet cap=0 disabled → set_api_paused NOT called even when expensive
# ===========================================================================


@pytest.mark.asyncio
async def test_fleet_cap_zero_no_fleet_pause():
    """Fleet cap=0 (disabled) — set_api_paused must not be called."""
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={},
        batch_costs={},
        fleet_cost=999.0,  # huge cost — but cap=0 = disabled
        budget_state_reason=None,
        cost_cap_batch=0.0,
        cost_cap_fleet=0.0,  # DISABLED
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    set_paused.assert_not_awaited()


# ===========================================================================
# Test 5 — batch paused with DIFFERENT reason → NOT touched
# BITE: The fake paused_reasons_map seeds BOTH bid_cap (reason "batch-cap") and
#       bid_manual (reason "manual").  The fake DB filter returns only IDs whose
#       stored reason matches the argument.  If production drops the
#       "batch-cap" reason arg (queries all paused batches), the mock returns
#       bid_manual too and the assert_awaited_once assertion fails — the test
#       bites.  With the correct reason-scoped query only bid_cap comes back.
# ===========================================================================


@pytest.mark.asyncio
async def test_different_reason_batch_not_unpaused():
    """A batch paused with reason 'manual' must NOT be unpaused by the monitor.

    The faithful DB mock seeds {bid_cap: "batch-cap", bid_manual: "manual"}.
    paused_batch_ids_by_reason(session, reason) returns only IDs whose stored
    reason equals the argument — exactly what the real SQL WHERE clause does.
    If production ever queries with the wrong reason (or no filter), bid_manual
    bleeds through and the assertions below fail.
    """
    bid_manual = uuid.uuid4()  # paused with 'manual' — must NOT be returned for "batch-cap"
    bid_cap = uuid.uuid4()     # paused with 'batch-cap' — under cap → must be unpaused
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        # Faithful DB state: both batches paused, with DIFFERENT reasons
        paused_reasons_map={bid_cap: "batch-cap", bid_manual: "manual"},
        batch_costs={bid_cap: 0.001, bid_manual: 0.001},
        fleet_cost=0.0,
        budget_state_reason=None,
        cost_cap_batch=0.01,
        cost_cap_fleet=0.0,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    # Only the batch-cap one should be unpaused — exactly once
    unpause.assert_awaited_once()
    call_args = unpause.await_args[0]
    assert call_args[1] == bid_cap, f"Only 'batch-cap' batch must be unpaused; got {call_args}"
    # bid_manual must not appear in any unpause call
    for c in unpause.await_args_list:
        assert c[0][1] != bid_manual, "Monitor must NEVER unpause a batch with a different reason"
    pause.assert_not_awaited()


# ===========================================================================
# Test 5b — cap DISABLED (cost_cap_batch_usd=0) + batch paused by "batch-cap"
#            → unpause_batch IS called (disable-cap clears own pauses)
# BITE: removing the `if cap <= 0: unpause_batch(...)` branch in the reconcile
#       loop makes unpause never fire → assert_awaited_once fails.
# ===========================================================================


@pytest.mark.asyncio
async def test_cap_disabled_clears_own_batch_cap_pauses():
    """With cost_cap_batch_usd=0 a batch still paused with 'batch-cap' must be
    unpaused — disabling the cap should clear the monitor's own pauses.

    Fleet cap is set >0 so the early-return guard (both-caps-disabled) does not
    fire; we are testing only the per-batch reconcile path.
    """
    bid = uuid.uuid4()
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={bid: "batch-cap"},  # leftover pause from when cap was enabled
        batch_costs={bid: 0.50},                # cost irrelevant — cap disabled
        fleet_cost=0.0,
        budget_state_reason=None,
        cost_cap_batch=0.0,                     # DISABLED → must clear own pauses
        cost_cap_fleet=1.0,                     # >0 so early-return does not fire
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    unpause.assert_awaited_once()
    call_args = unpause.await_args[0]
    assert call_args[1] == bid, f"unpause_batch must receive the cap-paused batch_id; got {call_args}"
    pause.assert_not_awaited()


# ===========================================================================
# Test 6 — fleet OVER cap → set_api_paused("fleet-daily-cap") called
# BITE: removing the `if fleet_cap > 0 and fleet_cost > fleet_cap` branch.
# ===========================================================================


@pytest.mark.asyncio
async def test_fleet_over_cap_sets_api_paused():
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={},
        batch_costs={},
        fleet_cost=2.50,    # $2.50 > $1.00 cap
        budget_state_reason=None,
        cost_cap_batch=0.0,
        cost_cap_fleet=1.00,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    set_paused.assert_awaited_once()
    call_args = set_paused.await_args[0]
    assert call_args[1] == "fleet-daily-cap", (
        f"set_api_paused reason must be 'fleet-daily-cap'; got {call_args}"
    )
    clear_paused.assert_not_awaited()


# ===========================================================================
# Test 7 — fleet UNDER cap, reason matches → clear_api_paused called
# BITE: removing `elif currently_fleet_paused_by_us: clear_api_paused`.
# ===========================================================================


@pytest.mark.asyncio
async def test_fleet_under_cap_clears_own_pause():
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={},
        batch_costs={},
        fleet_cost=0.50,    # $0.50 <= $1.00 cap → should clear
        budget_state_reason="fleet-daily-cap",   # paused by our monitor
        cost_cap_batch=0.0,
        cost_cap_fleet=1.00,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    clear_paused.assert_awaited_once()
    set_paused.assert_not_awaited()


# ===========================================================================
# Test 8 — fleet paused with DIFFERENT reason → NOT cleared by monitor
# BITE: removing the `api_paused_reason == "fleet-daily-cap"` check would
#       allow the monitor to clear a manual fleet pause it doesn't own.
# ===========================================================================


@pytest.mark.asyncio
async def test_fleet_different_reason_not_cleared():
    """Fleet paused with 'manual-operator' — monitor must not clear it."""
    pause = AsyncMock()
    unpause = AsyncMock()
    set_paused = AsyncMock()
    clear_paused = AsyncMock()

    await _run_monitor(
        active_batch_ids=[],
        paused_reasons_map={},
        batch_costs={},
        fleet_cost=0.10,   # well under cap — would trigger clear if reason matched
        budget_state_reason="manual-operator",   # NOT our reason
        cost_cap_batch=0.0,
        cost_cap_fleet=1.00,
        patch_batches_pause=pause,
        patch_batches_unpause=unpause,
        patch_budget_set=set_paused,
        patch_budget_clear=clear_paused,
    )

    clear_paused.assert_not_awaited()
    set_paused.assert_not_awaited()


# ===========================================================================
# Test 8b — Both caps disabled (both=0) → monitor makes NO ledger/repo calls
# BITE: removing the early-return guard means the function proceeds to open a
#       DB session and call active_batch_ids / fleet_api_cost_usd etc.; the
#       assert_not_called checks below would then FAIL.
# ===========================================================================


@pytest.mark.asyncio
async def test_both_caps_disabled_no_db_calls():
    """When both cost_cap_batch_usd=0 and cost_cap_fleet_daily_usd=0, the
    budget monitor must return immediately without touching any ledger or repo.

    BITE: if the early-return guard is removed, the monitor proceeds to open a
    session and call active_batch_ids, batch_api_cost_usd, fleet_api_cost_usd,
    get_state, etc. — any of the assert_not_called checks below will then fail.
    """
    from app.services.worker import Worker
    from unittest.mock import MagicMock, AsyncMock, patch

    with patch("asyncio.Semaphore", return_value=MagicMock()):
        w = Worker(concurrency=1)
    w._stop_event = MagicMock()

    mock_session_local = MagicMock()
    mock_active = AsyncMock()
    mock_records = AsyncMock()
    mock_batch_cost = AsyncMock()
    mock_fleet_cost = AsyncMock()
    mock_get_state = AsyncMock()
    mock_pause = AsyncMock()
    mock_unpause = AsyncMock()
    mock_set_paused = AsyncMock()
    mock_clear_paused = AsyncMock()

    patches = {
        "app.services.worker.SessionLocal": mock_session_local,
        "app.services.worker.settings": _make_settings(
            cost_cap_batch=0.0, cost_cap_fleet=0.0
        ),
        "app.services.worker.batches_repo.active_batch_ids": mock_active,
        "app.services.worker.batches_repo.paused_batch_ids_by_reason": AsyncMock(),
        "app.services.worker.batches_repo.paused_cap_records": mock_records,
        "app.services.worker.batches_repo.pause_batch": mock_pause,
        "app.services.worker.batches_repo.clear_cap_pause": mock_unpause,
        "app.services.worker.batches_repo.unpause_batch": AsyncMock(),
        "app.services.worker.cost_repo.batch_api_cost_usd": mock_batch_cost,
        "app.services.worker.cost_repo.fleet_api_cost_usd": mock_fleet_cost,
        "app.services.worker.budget_repo.get_state": mock_get_state,
        "app.services.worker.budget_repo.set_api_paused": mock_set_paused,
        "app.services.worker.budget_repo.clear_api_pause_if_entitled": mock_clear_paused,
        "app.services.worker.budget_repo.clear_api_paused": AsyncMock(),
    }

    from contextlib import ExitStack
    with ExitStack() as stack:
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, mock_obj))
        await w._budget_monitor()

    # Early return fires — no DB session opened, no repo calls made
    mock_session_local.assert_not_called()
    mock_active.assert_not_awaited()
    mock_records.assert_not_awaited()
    mock_batch_cost.assert_not_awaited()
    mock_fleet_cost.assert_not_awaited()
    mock_get_state.assert_not_awaited()
    mock_pause.assert_not_awaited()
    mock_unpause.assert_not_awaited()
    mock_set_paused.assert_not_awaited()
    mock_clear_paused.assert_not_awaited()


# ===========================================================================
# Test 9 — Source-level: _budget_monitor has the right reason literals
# ===========================================================================


def test_budget_monitor_source_references_batch_cap():
    """_budget_monitor source must reference 'batch-cap' and 'fleet-daily-cap'."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._budget_monitor)
    assert '"batch-cap"' in src or "'batch-cap'" in src, (
        "_budget_monitor must use reason literal 'batch-cap'"
    )
    assert '"fleet-daily-cap"' in src or "'fleet-daily-cap'" in src, (
        "_budget_monitor must use reason literal 'fleet-daily-cap'"
    )


def test_budget_monitor_reconcile_is_reason_scoped_and_provenance_aware():
    """The reconcile worklist must be the reason-scoped PROVENANCE query, and
    the automatic clear must go through the guarded repo call — never the
    unguarded operator primitives."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._budget_monitor)
    assert "paused_cap_records" in src, (
        "_budget_monitor must call paused_cap_records to limit reconcile to its own "
        "pauses AND read the cap/host each pause was decided under"
    )
    assert "clear_cap_pause" in src, (
        "_budget_monitor must clear batch pauses via the guarded clear_cap_pause"
    )
    assert "unpause_batch" not in src, (
        "_budget_monitor must NOT use the unguarded operator unpause_batch — that "
        "is what let a looser worker reverse a stricter host's decision"
    )
    assert "clear_api_paused" not in src.replace("clear_api_pause_if_entitled", ""), (
        "_budget_monitor must NOT use the unguarded operator clear_api_paused"
    )
    assert "_may_lift_cap_pause" in src, (
        "_budget_monitor must consult the fleet-safety entitlement rule before lifting"
    )


def test_budget_monitor_source_uses_utcnow():
    """_budget_monitor must use _utcnow for the 24h fleet window."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._budget_monitor)
    assert "_utcnow" in src, (
        "_budget_monitor must call _utcnow() for the fleet 24h since-cutoff"
    )


def test_budget_monitor_source_checks_own_fleet_reason():
    """_budget_monitor must guard clear_api_paused with a reason equality check."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._budget_monitor)
    assert "api_paused_reason" in src, (
        "_budget_monitor must check api_paused_reason before clearing fleet pause "
        "(must not clear a pause it doesn't own)"
    )


# ===========================================================================
# Test 10 — Config: new fields exist with correct defaults
# ===========================================================================


def test_config_cost_cap_batch_usd_default():
    from app.config import Settings
    assert Settings.model_fields["cost_cap_batch_usd"].default == 0.0, (
        "cost_cap_batch_usd must default to 0.0 (disabled)"
    )


def test_config_cost_cap_fleet_daily_usd_default():
    from app.config import Settings
    assert Settings.model_fields["cost_cap_fleet_daily_usd"].default == 0.0, (
        "cost_cap_fleet_daily_usd must default to 0.0 (disabled)"
    )


def test_config_cost_check_interval_seconds_default():
    from app.config import Settings
    assert Settings.model_fields["cost_check_interval_seconds"].default == 60, (
        "cost_check_interval_seconds must default to 60"
    )


# ===========================================================================
# Test 11 — Worker: _last_budget_check_at initialized; run loop wired
# ===========================================================================


def test_worker_has_last_budget_check_at():
    from app.services.worker import Worker
    with patch("asyncio.Semaphore", return_value=MagicMock()):
        w = Worker(concurrency=1)
    assert hasattr(w, "_last_budget_check_at"), (
        "Worker must have _last_budget_check_at attribute"
    )
    assert w._last_budget_check_at == 0.0, (
        "_last_budget_check_at must initialize to 0.0"
    )


def test_worker_run_loop_references_budget_monitor():
    """Worker.run source must gate _budget_monitor with cost_check_interval_seconds."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker.run)
    assert "_budget_monitor" in src, "Worker.run must call _budget_monitor"
    assert "_last_budget_check_at" in src, "Worker.run must gate on _last_budget_check_at"
    assert "cost_check_interval_seconds" in src, (
        "Worker.run must use settings.cost_check_interval_seconds for the gate"
    )


# ===========================================================================
# Test 12 — batches repo: paused_batch_ids_by_reason exists
# ===========================================================================


def test_paused_batch_ids_by_reason_exists():
    from app.repositories import batches as batches_repo
    assert callable(getattr(batches_repo, "paused_batch_ids_by_reason", None)), (
        "batches repo must expose paused_batch_ids_by_reason"
    )


def test_paused_batch_ids_by_reason_source():
    import inspect
    from app.repositories import batches as batches_repo

    src = inspect.getsource(batches_repo.paused_batch_ids_by_reason)
    assert "paused_at" in src, "must filter on paused_at IS NOT NULL"
    assert "paused_reason" in src, "must filter on paused_reason == reason"
