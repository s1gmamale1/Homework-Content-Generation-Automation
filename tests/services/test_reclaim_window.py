import inspect

import main
from app.services.worker import Worker


def test_reclaim_uses_lease_ttl_not_job_timeout():
    src = inspect.getsource(Worker._sweep_stuck_jobs)   # the periodic + startup reclaim sweep
    assert "reclaim_stale_seconds" in src
    assert "job_timeout * 2" not in src             # the old window is gone


def test_startup_resets_orphaned_running_jobs():
    src = inspect.getsource(main.lifespan)
    # Startup flips orphaned running jobs back to pending (stale_after_seconds=0
    # is correct at boot: no workers are alive, so every running row is orphaned).
    # Assert the actual CALL, not just the word — the lifespan comment already
    # mentions `reclaim_stuck_jobs`, so a bare substring check never guards.
    assert "reclaim_stuck_jobs(session, stale_after_seconds=0)" in src
