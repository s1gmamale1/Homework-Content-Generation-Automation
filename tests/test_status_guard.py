import pytest

from app.repositories.jobs import status_write_allowed


@pytest.mark.parametrize("current,target,allowed", [
    ("pending", "running", True),       # claim/start
    ("running", "running", True),       # per-phase re-write
    ("running", "done", True),          # success
    ("running", "failed", True),        # crash
    ("running", "cancelling", True),    # request_cancel path
    ("cancelling", "cancelled", True),  # finalize
    ("cancelling", "running", False),   # THE RACE — must be blocked
    ("cancelling", "pending", False),   # no resurrection
    ("cancelling", "done", False),      # cancel wins over a late success
    ("cancelling", "failed", False),    # cancel wins over a late failure
    ("done", "running", False),         # terminal frozen
    ("failed", "running", False),
    ("cancelled", "running", False),
    ("cancelled", "pending", False),
])
def test_status_write_allowed(current, target, allowed):
    assert status_write_allowed(current, target) is allowed
