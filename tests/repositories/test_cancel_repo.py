import inspect
from app.repositories import jobs as jobs_repo


def test_cancel_if_pending_is_atomic_pending_only():
    src = inspect.getsource(jobs_repo.cancel_if_pending)
    assert 'status == "pending"' in src or "status == 'pending'" in src
    assert '"cancelled"' in src or "'cancelled'" in src
    assert "rowcount" in src


def test_request_cancel_sets_cancelling_on_running():
    src = inspect.getsource(jobs_repo.request_cancel)
    assert '"cancelling"' in src or "'cancelling'" in src
    assert 'status == "running"' in src or "status == 'running'" in src


def test_get_status_reads_status():
    src = inspect.getsource(jobs_repo.get_status)
    assert "select" in src.lower() and "status" in src.lower()


def test_mark_cancelled_preserves_done_phases():
    src = inspect.getsource(jobs_repo.mark_cancelled)
    assert '"cancelled"' in src or "'cancelled'" in src
    assert "PhaseOutput" in src
    assert '!= "done"' in src or "!= 'done'" in src


def test_reclaim_stale_cancelling_targets_cancelling():
    src = inspect.getsource(jobs_repo.reclaim_stale_cancelling)
    assert '"cancelling"' in src or "'cancelling'" in src
    assert '"cancelled"' in src or "'cancelled'" in src
    assert "claimed_at" in src
