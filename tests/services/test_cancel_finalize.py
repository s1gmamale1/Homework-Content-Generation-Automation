import inspect
from app.services import worker


def test_cancel_finalize_is_shielded_and_status_gated():
    src = inspect.getsource(worker.Worker._execute_job)
    # discriminator: only finalize when the job is being cancelled
    assert "get_status" in src and ("'cancelling'" in src or '"cancelling"' in src)
    assert "mark_cancelled" in src
    # the finalize write must survive the already-delivered CancelledError
    assert "shield" in src, "wrap the cancelled finalize in asyncio.shield"
    # double-cancel hardening: clear our own cancel state before the finalize
    assert "uncancel" in src, "uncancel() before finalize so a double-cancel can't skip the write"
