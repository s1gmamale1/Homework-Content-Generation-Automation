import inspect

import main
from app.services.worker import Worker


def test_reclaim_uses_lease_ttl_not_job_timeout():
    src = inspect.getsource(Worker._sweep_stuck_jobs)   # the periodic + startup reclaim sweep
    assert "reclaim_stale_seconds" in src
    assert "job_timeout * 2" not in src             # the old window is gone


def test_startup_resets_orphaned_running_jobs():
    # The startup reconcile (books sweep + peer-aware reclaim) lives in
    # main._reconcile_on_startup (fenced job leases, Task 8 — extracted out
    # of main.lifespan so it's directly testable; lifespan just calls it).
    src = inspect.getsource(main._reconcile_on_startup)
    # Startup flips orphaned running jobs back to pending via the PEER-AWARE
    # reclaim (fleet-restart-reclaim-1): it uses stale_after_seconds=0 only when
    # no live peer exists, else the lease window, so a peer's fresh-beat job is
    # never yanked. Assert the actual CALL with its window arg, not just the word —
    # behaviour itself is proven in tests/integration/test_startup_reclaim.py.
    assert "reclaim_orphans_on_startup(" in src
    assert "reclaim_stale_seconds=settings.reclaim_stale_seconds" in src
