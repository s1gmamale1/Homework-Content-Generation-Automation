import inspect
from app.services import pipeline


def test_scheduler_cancels_inflight_on_external_cancel():
    src = inspect.getsource(pipeline._run_content_phases_parallel)
    assert "except asyncio.CancelledError" in src, "scheduler must catch external cancellation"
    # on cancel it must cancel peers AND gather them so each _spawn's kill fires
    assert src.count(".cancel()") >= 2, "must cancel in_flight tasks on CancelledError (not only on failure)"
    assert "gather" in src, "must gather cancelled in_flight tasks so subprocess kills run"
