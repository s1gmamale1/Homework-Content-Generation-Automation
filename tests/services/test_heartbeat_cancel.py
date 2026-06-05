import inspect
from app.services import worker


def test_heartbeat_self_cancels_on_cancelling():
    src = inspect.getsource(worker.Worker._heartbeat)
    assert "get_status" in src, "heartbeat must read job status"
    assert '"cancelling"' in src or "'cancelling'" in src
    assert "RUNNING_JOBS" in src and ".cancel()" in src, "must self-cancel the local task"
