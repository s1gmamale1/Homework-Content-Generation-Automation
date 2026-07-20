"""Worker parks a SlotSaturation job: requeue with cooldown, attempt refunded,
_mark_failed NEVER called, and NO worker-level cooldown (other credentials'
jobs must keep flowing).

RED-proof: without the except SlotSaturation branch, the generic
except Exception -> _mark_failed handler burns an attempt."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config import settings
from app.services.errors import SlotSaturation


def _make_worker(**kwargs):
    from app.services.worker import Worker

    return Worker(concurrency=1, **kwargs)


@pytest.mark.asyncio
async def test_execute_job_slot_saturation_parks_not_fails():
    """Behavioral: _execute_job calls jobs_repo.requeue_slot_saturated (not
    _mark_failed) when pipeline.run raises SlotSaturation, and does NOT set
    self._cooldown_until (unlike SessionLimitPause — other credentials must
    keep flowing).

    RED-proof:
      - If SlotSaturation falls through to `except Exception`: mock_requeue
        not called -> assert_called_once fails, and mock_mark_failed IS
        called -> assert_not_called fails.
      - If the handler wrongly copies the SessionLimitPause worker-cooldown
        behavior: w._cooldown_until would be set -> assert None fails.
    """
    job_id = uuid4()
    exc = SlotSaturation(
        "429 fleet credential slot wait exhausted (gemini:proj-x)"
    )

    w = _make_worker()

    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.pipeline.run", new_callable=AsyncMock, side_effect=exc),
        patch(
            "app.repositories.jobs.requeue_slot_saturated", new_callable=AsyncMock
        ) as mock_requeue,
        patch(
            "app.repositories.jobs.mark_failed_with_retry", new_callable=AsyncMock
        ) as mock_mark_failed_with_retry,
        patch.object(w, "_mark_failed", new_callable=AsyncMock) as mock_mark_failed,
        patch("app.services.worker.SessionLocal", return_value=mock_session_ctx),
    ):
        await w._execute_job(job_id)

    mock_requeue.assert_called_once()
    _call_args, call_kwargs = mock_requeue.call_args.args, mock_requeue.call_args.kwargs
    assert _call_args[1] == job_id, (
        f"requeue_slot_saturated must be called with the right job_id "
        f"(got {_call_args[1]!r})"
    )
    assert call_kwargs.get("cooldown_seconds") == settings.slot_saturation_requeue_seconds, (
        f"requeue_slot_saturated must be called with "
        f"cooldown_seconds={settings.slot_saturation_requeue_seconds}, "
        f"got {call_kwargs.get('cooldown_seconds')!r}"
    )

    mock_mark_failed.assert_not_called()
    mock_mark_failed_with_retry.assert_not_awaited()

    assert w._cooldown_until is None, (
        f"SlotSaturation must NOT set a worker-level cooldown (other "
        f"credentials' jobs must keep flowing), got {w._cooldown_until!r}"
    )


def test_execute_job_imports_slot_saturation():
    """worker.py must import SlotSaturation (from app.services.errors)."""
    import app.services.worker as worker_mod

    assert hasattr(worker_mod, "SlotSaturation"), (
        "worker module must import SlotSaturation"
    )


def test_execute_job_source_catches_slot_saturation_before_exception():
    """_execute_job source must contain an except SlotSaturation handler that
    calls requeue_slot_saturated, positioned before the generic except
    Exception (else SlotSaturation falls through to _mark_failed)."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._execute_job)
    assert "SlotSaturation" in src, (
        "_execute_job must catch SlotSaturation"
    )
    assert "requeue_slot_saturated" in src, (
        "_execute_job must call requeue_slot_saturated on SlotSaturation"
    )

    slot_idx = src.index("except SlotSaturation")
    # NB: "except Exception:" (no `as exc`) also appears earlier, nested
    # inside the CancelledError branch's own try/except — that's unrelated
    # inner error handling, not the outer except-chain's generic handler.
    # Match the specific outer handler `except Exception as exc:` instead.
    generic_idx = src.index("except Exception as exc:")
    assert slot_idx < generic_idx, (
        "except SlotSaturation must appear BEFORE except Exception, else "
        "the generic handler swallows it and burns a retry attempt"
    )
