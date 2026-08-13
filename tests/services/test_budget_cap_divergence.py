"""Fleet-safety: a cost cap must be a FLEET decision, not whatever the
least-updated worker believes.

The incident (38-host operation, uneven cap rollout): 7 hosts still carried
``COST_CAP_BATCH_USD=50`` while 31 had been rolled forward to 2000.
``batches.paused_at`` is a FLEET-WIDE flag, but every worker evaluated it
against its OWN env cap:

  * the stale host paused the batch (cost $120 > $50),
  * a patched host unpaused it on its next tick (cost $120 <= $2000),
  * repeat every ``cost_check_interval_seconds``.

The batch flip-flopped, work stalled intermittently for reasons invisible to
the operator, and nothing in the DB recorded which host had paused it or at
what cap.

These tests drive the REAL ``Worker._budget_monitor`` body against an in-memory
fleet DB that mirrors the repo semantics faithfully — including the unguarded
``unpause_batch`` / ``clear_api_paused`` operator primitives, so a monitor that
still reaches for them oscillates here exactly as it did in production.
"""
from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# In-memory fleet DB: one shared store that every simulated host reads/writes,
# exactly like the real Postgres the 38 hosts shared.
# ---------------------------------------------------------------------------


class _FleetDB:
    def __init__(self, *, batch_costs: dict, fleet_cost: float = 0.0):
        self.batch_costs = dict(batch_costs)
        self.fleet_cost = fleet_cost
        self.batches = {
            bid: {
                "paused_at": None,
                "paused_reason": None,
                "paused_cap_usd": None,
                "paused_by": None,
            }
            for bid in batch_costs
        }
        self.fleet = {
            "api_paused_at": None,
            "api_paused_reason": None,
            "api_paused_cap_usd": None,
            "api_paused_by": None,
        }
        # Every observed gate flip, in order: ("pause"|"unpause", target, by).
        self.transitions: list[tuple[str, object, object]] = []

    # ── batches_repo ────────────────────────────────────────────────────
    async def active_batch_ids(self, session):
        return [b for b, r in self.batches.items() if r["paused_at"] is None]

    async def paused_batch_ids_by_reason(self, session, reason):
        return [
            b for b, r in self.batches.items()
            if r["paused_at"] is not None and r["paused_reason"] == reason
        ]

    async def paused_cap_records(self, session, reason):
        return [
            (b, r["paused_cap_usd"], r["paused_by"])
            for b, r in self.batches.items()
            if r["paused_at"] is not None and r["paused_reason"] == reason
        ]

    async def pause_batch(self, session, batch_id, reason, *, cap_usd=None, paused_by=None):
        row = self.batches[batch_id]
        if row["paused_at"] is None:
            self.transitions.append(("pause", batch_id, paused_by))
        row.update(
            paused_at=_NOW, paused_reason=reason,
            paused_cap_usd=cap_usd, paused_by=paused_by,
        )

    async def unpause_batch(self, session, batch_id):
        """The UNGUARDED operator primitive (POST /jobs/batch/{id}/unpause).

        Today's budget monitor calls this — which is precisely the defect: it
        cannot express "only if I am not relaxing another host's decision".
        """
        row = self.batches[batch_id]
        if row["paused_at"] is not None:
            self.transitions.append(("unpause", batch_id, None))
        row.update(paused_at=None, paused_reason=None,
                   paused_cap_usd=None, paused_by=None)

    async def clear_cap_pause(self, session, batch_id, *, reason,
                              worker_cap_usd, worker_host):
        """Mirror of the guarded repo UPDATE (the WHERE clause is the contract)."""
        row = self.batches[batch_id]
        if row["paused_at"] is None or row["paused_reason"] != reason:
            return False
        if not _entitled(row["paused_cap_usd"], row["paused_by"],
                         worker_cap_usd, worker_host):
            return False
        self.transitions.append(("unpause", batch_id, worker_host))
        row.update(paused_at=None, paused_reason=None,
                   paused_cap_usd=None, paused_by=None)
        return True

    # ── budget_repo ─────────────────────────────────────────────────────
    async def get_state(self, session):
        return SimpleNamespace(**self.fleet)

    async def set_api_paused(self, session, reason, *, cap_usd=None, paused_by=None):
        held = self.fleet
        if held["api_paused_at"] is None:
            self.transitions.append(("pause", "fleet", paused_by))
        stricter_held = (
            held["api_paused_at"] is not None
            and held["api_paused_reason"] == reason
            and held["api_paused_cap_usd"] is not None
            and (cap_usd is None or held["api_paused_cap_usd"] <= cap_usd)
        )
        held["api_paused_at"] = _NOW
        held["api_paused_reason"] = reason
        if not stricter_held:
            held["api_paused_cap_usd"] = cap_usd
            held["api_paused_by"] = paused_by

    async def clear_api_paused(self, session):
        """UNGUARDED fleet clear — the operator escape hatch."""
        if self.fleet["api_paused_at"] is not None:
            self.transitions.append(("unpause", "fleet", None))
        self.fleet.update(api_paused_at=None, api_paused_reason=None,
                          api_paused_cap_usd=None, api_paused_by=None)

    async def clear_api_pause_if_entitled(self, session, *, reason,
                                          worker_cap_usd, worker_host):
        held = self.fleet
        if held["api_paused_at"] is None or held["api_paused_reason"] != reason:
            return False
        if not _entitled(held["api_paused_cap_usd"], held["api_paused_by"],
                         worker_cap_usd, worker_host):
            return False
        self.transitions.append(("unpause", "fleet", worker_host))
        held.update(api_paused_at=None, api_paused_reason=None,
                    api_paused_cap_usd=None, api_paused_by=None)
        return True

    # ── cost_repo ───────────────────────────────────────────────────────
    async def batch_api_cost_usd(self, session, batch_id):
        return self.batch_costs.get(batch_id, 0.0)

    async def fleet_api_cost_usd(self, session, since):
        return self.fleet_cost


def _entitled(paused_cap_usd, paused_by, worker_cap_usd, worker_host) -> bool:
    """The guard the repo UPDATE must encode in SQL."""
    if paused_cap_usd is None:
        return True                                   # legacy pause, no provenance
    if paused_by and paused_by.split(":", 1)[0] == worker_host:
        return True                                   # the deciding host, revising itself
    return worker_cap_usd > 0 and paused_cap_usd >= worker_cap_usd


# ---------------------------------------------------------------------------
# One monitor tick for one simulated host
# ---------------------------------------------------------------------------


def _worker(host: str):
    from app.services.worker import Worker

    with patch("asyncio.Semaphore", return_value=MagicMock()):
        w = Worker(concurrency=1)
    w._stop_event = MagicMock()
    w.id = f"{host}:4242@abc1234"
    w.hostname = host
    return w


async def _tick(worker, db: _FleetDB, *, cap_batch: float, cap_fleet: float = 0.0):
    """Run one _budget_monitor pass for a host holding these env caps."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)

    patches = {
        "app.services.worker.SessionLocal": MagicMock(return_value=session),
        "app.services.worker.settings": SimpleNamespace(
            cost_cap_batch_usd=cap_batch,
            cost_cap_fleet_daily_usd=cap_fleet,
            cost_check_interval_seconds=60,
        ),
        "app.services.worker.batches_repo.active_batch_ids": db.active_batch_ids,
        "app.services.worker.batches_repo.paused_batch_ids_by_reason": db.paused_batch_ids_by_reason,
        "app.services.worker.batches_repo.pause_batch": db.pause_batch,
        "app.services.worker.batches_repo.unpause_batch": db.unpause_batch,
        "app.services.worker.cost_repo.batch_api_cost_usd": db.batch_api_cost_usd,
        "app.services.worker.cost_repo.fleet_api_cost_usd": db.fleet_api_cost_usd,
        "app.services.worker.budget_repo.get_state": db.get_state,
        "app.services.worker.budget_repo.set_api_paused": db.set_api_paused,
        "app.services.worker.budget_repo.clear_api_paused": db.clear_api_paused,
        "app.services.worker._utcnow": MagicMock(return_value=_NOW),
    }
    # The guarded primitives only exist once the fix lands; patching them
    # conditionally keeps this helper usable against both code vintages.
    from app.repositories import batches as _batches_repo
    from app.repositories import budget as _budget_repo
    if hasattr(_batches_repo, "paused_cap_records"):
        patches["app.services.worker.batches_repo.paused_cap_records"] = db.paused_cap_records
    if hasattr(_batches_repo, "clear_cap_pause"):
        patches["app.services.worker.batches_repo.clear_cap_pause"] = db.clear_cap_pause
    if hasattr(_budget_repo, "clear_api_pause_if_entitled"):
        patches["app.services.worker.budget_repo.clear_api_pause_if_entitled"] = (
            db.clear_api_pause_if_entitled
        )

    with ExitStack() as stack:
        for target, obj in patches.items():
            stack.enter_context(patch(target, obj))
        await worker._budget_monitor()


# ===========================================================================
# 1 — THE INCIDENT: a stale-cap host and a patched host must not flip-flop
# ===========================================================================


@pytest.mark.asyncio
async def test_low_and_high_cap_workers_do_not_oscillate():
    """7 hosts at $50, 31 hosts at $2000, one batch at $120 of spend.

    Today: host-a pauses, host-b unpauses, forever — one gate flip per tick.
    Required: exactly ONE transition (the pause), and it stays paused.
    """
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 120.0})
    stale = _worker("host-a")     # COST_CAP_BATCH_USD=50 (never rolled forward)
    patched = _worker("host-b")   # COST_CAP_BATCH_USD=2000

    for _ in range(4):
        await _tick(stale, db, cap_batch=50.0)
        await _tick(patched, db, cap_batch=2000.0)

    assert db.transitions == [("pause", bid, "host-a:4242@abc1234")], (
        "the batch gate must flip exactly once (paused by the strict host) — got "
        f"{db.transitions}"
    )
    row = db.batches[bid]
    assert row["paused_at"] is not None, (
        "a batch over the strictest cap in the fleet must stay paused"
    )
    assert row["paused_reason"] == "batch-cap"


# ===========================================================================
# 2 — The pause must record its origin (host + the cap that tripped it)
# ===========================================================================


@pytest.mark.asyncio
async def test_pause_records_deciding_host_and_cap():
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 120.0})

    await _tick(_worker("host-a"), db, cap_batch=50.0)

    row = db.batches[bid]
    assert row["paused_cap_usd"] == 50.0, (
        "the pause must persist the cap it was decided under, or a worker with a "
        "different cap cannot tell whose decision it is reversing"
    )
    assert row["paused_by"] == "host-a:4242@abc1234", (
        "the pause must name the deciding worker so the operator can see which "
        "host stalled the batch"
    )


# ===========================================================================
# 3 — A looser worker may never lift a stricter host's pause
# ===========================================================================


@pytest.mark.asyncio
async def test_looser_worker_leaves_a_stricter_pause_alone():
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 120.0})
    db.batches[bid].update(
        paused_at=_NOW, paused_reason="batch-cap",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )

    await _tick(_worker("host-b"), db, cap_batch=2000.0)

    assert db.transitions == [], "a looser cap must not reverse a stricter decision"
    assert db.batches[bid]["paused_at"] is not None


# ===========================================================================
# 4 — Self-heal: the deciding host lifts its own pause once ITS cap is raised
# ===========================================================================


@pytest.mark.asyncio
async def test_deciding_host_lifts_its_own_pause_after_the_rollout_reaches_it():
    """The operator finishes the rollout on host-a (50 -> 2000). host-a owns the
    pause record, so it clears its own decision — no operator click needed."""
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 120.0})
    db.batches[bid].update(
        paused_at=_NOW, paused_reason="batch-cap",
        paused_cap_usd=50.0, paused_by="host-a:4242@abc1234",
    )

    await _tick(_worker("host-a"), db, cap_batch=2000.0)

    assert db.batches[bid]["paused_at"] is None, (
        "the host that decided the pause must be able to revise its own decision "
        "after its cap is rolled forward"
    )


# ===========================================================================
# 5 — A stricter worker may still lift a looser host's pause when under cap
# ===========================================================================


@pytest.mark.asyncio
async def test_stricter_worker_may_lift_a_looser_pause_when_under_its_own_cap():
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 10.0})
    db.batches[bid].update(
        paused_at=_NOW, paused_reason="batch-cap",
        paused_cap_usd=2000.0, paused_by="host-b:1@sha",
    )

    await _tick(_worker("host-a"), db, cap_batch=50.0)

    assert db.batches[bid]["paused_at"] is None, (
        "a worker at least as strict as the recorded cap, and under its own cap, "
        "is not relaxing anyone's decision — it must still be able to unpause"
    )


# ===========================================================================
# 6 — Safety property intact: a real overspend still pauses
# ===========================================================================


@pytest.mark.asyncio
async def test_real_overspend_still_pauses_and_stays_paused():
    bid = uuid.uuid4()
    db = _FleetDB(batch_costs={bid: 2500.0})   # over EVERY cap in the fleet

    await _tick(_worker("host-b"), db, cap_batch=2000.0)
    await _tick(_worker("host-a"), db, cap_batch=50.0)
    await _tick(_worker("host-b"), db, cap_batch=2000.0)

    assert db.batches[bid]["paused_at"] is not None, "a real overspend must pause"
    assert db.transitions == [("pause", bid, "host-b:4242@abc1234")]


# ===========================================================================
# 7 — Same story for the fleet daily gate (budget_state)
# ===========================================================================


@pytest.mark.asyncio
async def test_fleet_daily_gate_does_not_oscillate_and_records_origin():
    db = _FleetDB(batch_costs={}, fleet_cost=120.0)
    stale = _worker("host-a")
    patched = _worker("host-b")

    for _ in range(4):
        await _tick(stale, db, cap_batch=0.0, cap_fleet=50.0)
        await _tick(patched, db, cap_batch=0.0, cap_fleet=2000.0)

    assert db.transitions == [("pause", "fleet", "host-a:4242@abc1234")], (
        f"the fleet gate must flip exactly once — got {db.transitions}"
    )
    assert db.fleet["api_paused_at"] is not None
    assert db.fleet["api_paused_cap_usd"] == 50.0, (
        "the fleet pause must record the cap it was decided under"
    )
    assert db.fleet["api_paused_by"] == "host-a:4242@abc1234", (
        "the fleet pause must record the deciding host"
    )


# ===========================================================================
# 8 — The entitlement rule as a pure function
# ===========================================================================


def test_may_lift_cap_pause_rule():
    from app.services.worker import _may_lift_cap_pause

    # A looser worker may not lift a stricter host's pause.
    assert not _may_lift_cap_pause(
        worker_cap_usd=2000.0, worker_host="host-b",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # An equally-strict worker may.
    assert _may_lift_cap_pause(
        worker_cap_usd=50.0, worker_host="host-b",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # A stricter worker may.
    assert _may_lift_cap_pause(
        worker_cap_usd=10.0, worker_host="host-b",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # The deciding host may always revise its own decision.
    assert _may_lift_cap_pause(
        worker_cap_usd=2000.0, worker_host="host-a",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # A worker with the cap DISABLED is the loosest of all — it may not lift
    # another host's pause (0 is not "no opinion", it is "no ceiling").
    assert not _may_lift_cap_pause(
        worker_cap_usd=0.0, worker_host="host-b",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # ...but it may clear the pause its OWN host decided.
    assert _may_lift_cap_pause(
        worker_cap_usd=0.0, worker_host="host-a",
        paused_cap_usd=50.0, paused_by="host-a:1@sha",
    )
    # Provenance-less (pre-migration) pauses keep the historical behavior so
    # they drain instead of sticking forever.
    assert _may_lift_cap_pause(
        worker_cap_usd=2000.0, worker_host="host-b",
        paused_cap_usd=None, paused_by=None,
    )
