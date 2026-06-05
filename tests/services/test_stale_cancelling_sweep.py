import inspect
from app.services import worker


def test_worker_sweeps_stale_cancelling():
    src = inspect.getsource(worker.Worker)
    assert "reclaim_stale_cancelling" in src, "worker must finalize stale cancelling rows"
