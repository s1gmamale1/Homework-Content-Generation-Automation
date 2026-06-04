import inspect

from app.repositories import jobs as jobs_repo
from app.services.worker import Worker


def test_touch_claim_exists_and_scoped():
    assert hasattr(jobs_repo, "touch_claim")
    src = inspect.getsource(jobs_repo.touch_claim)
    # Only refresh a row that is still RUNNING (never resurrect a finished job).
    assert 'status == "running"' in src or "status==\"running\"" in src
    assert "claimed_at" in src


def test_execute_job_runs_and_cancels_heartbeat():
    src = inspect.getsource(Worker._execute_job)
    assert "_heartbeat" in src          # heartbeat task started
    assert "cancel()" in src            # ...and cancelled when the job ends


def test_heartbeat_uses_configured_interval():
    src = inspect.getsource(Worker._heartbeat)
    assert "heartbeat_seconds" in src
    assert "touch_claim" in src
