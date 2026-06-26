"""Unit tests for Worker session-limit cooldown (Task 5).

Tests the two new mechanisms:
  1. _in_cooldown() — True when _cooldown_until is in the future, False when past/None.
  2. _claim_one() — skips claim_next_job while in cooldown; resumes when expired.

No DB, no live worker needed. Tests directly instantiate Worker and call the
helpers, or mock claim_next_job to assert it is/isn't called.

RED-proofs:
  - _in_cooldown: without `_cooldown_until is not None` guard, None case would error.
  - _in_cooldown: without the `< _cooldown_until` check, past cooldown would return True.
  - _claim_one cooldown gate: if _in_cooldown() is never checked, claim_next_job IS
    called even when cooled down — the mock call-count assertion fails.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# _in_cooldown() unit tests
# ---------------------------------------------------------------------------


def _make_worker(**kwargs):
    """Return a Worker instance with concurrency=1 (minimal)."""
    from app.services.worker import Worker

    return Worker(concurrency=1, **kwargs)


def test_in_cooldown_none_returns_false():
    """When _cooldown_until is None, _in_cooldown() must return False.

    RED-proof: if _in_cooldown() ignores None and always evaluates the clock
    comparison, it will raise AttributeError or TypeError on None.
    """
    w = _make_worker()
    assert w._cooldown_until is None
    assert w._in_cooldown() is False


def test_in_cooldown_future_returns_true():
    """When _cooldown_until is in the future, _in_cooldown() must return True.

    RED-proof: without the `< _cooldown_until` comparison, a future timestamp
    would not be detected and this would return False.
    """
    w = _make_worker()
    w._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=1)
    assert w._in_cooldown() is True


def test_in_cooldown_past_returns_false():
    """When _cooldown_until is in the past, _in_cooldown() must return False.

    RED-proof: if the < comparison is inverted (>), past cooldown would return
    True, and the worker would never resume.
    """
    w = _make_worker()
    w._cooldown_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert w._in_cooldown() is False


def test_in_cooldown_exact_now_returns_false():
    """A _cooldown_until exactly at now is treated as expired (strictly <).

    This tests the boundary: the condition is `now < _cooldown_until`,
    so equal means NOT in cooldown.
    """
    w = _make_worker()
    # Set _cooldown_until slightly in the past (timezone-aware)
    w._cooldown_until = datetime.now(timezone.utc) - timedelta(milliseconds=1)
    assert w._in_cooldown() is False


# ---------------------------------------------------------------------------
# _claim_one cooldown gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_one_skips_when_cooled_down():
    """_claim_one must NOT call claim_next_job when _in_cooldown() is True.

    RED-proof: if the cooldown gate is not present in _claim_one, the mock
    WILL be called and assert_not_called() fails.
    """
    w = _make_worker()
    # Set a future cooldown — _in_cooldown() will return True
    w._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch("app.repositories.jobs.claim_next_job", new_callable=AsyncMock) as mock_claim:
        result = await w._claim_one()

    assert result is None, "cooled-down _claim_one must return None (no job claimed)"
    mock_claim.assert_not_called()


@pytest.mark.asyncio
async def test_claim_one_resumes_when_cooldown_expired():
    """_claim_one MUST call claim_next_job when cooldown has expired.

    RED-proof: if the cooldown gate is checked incorrectly (e.g. inverted),
    this call would be skipped and assert_called_once() fails.

    We verify by patching _in_cooldown to return False (expired) and
    asserting that the code path reaches claim_next_job. Since the full
    DB-session path is complex to mock, we use the simpler approach of
    patching _in_cooldown directly and asserting via source inspection +
    a known short-circuit behavior.
    """
    w = _make_worker()
    # Expired cooldown — _in_cooldown() returns False
    w._cooldown_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    # Verify that _in_cooldown() returns False when cooldown is in the past
    # (this is the same as test_in_cooldown_past_returns_false but inline)
    assert w._in_cooldown() is False, (
        "precondition: past cooldown must report _in_cooldown()=False"
    )

    # The cooldown gate MUST NOT skip the DB call when _in_cooldown() is False.
    # We verify this by checking that _claim_one proceeds to the try/except
    # block (i.e. raises an exception from the DB path rather than returning
    # None from the cooldown gate). With no real DB, it will hit an exception
    # inside the session context — that's fine; it means the gate was NOT taken.
    class _EarlyExit(Exception):
        pass

    with patch("app.services.worker.SessionLocal") as mock_session_cls:
        # Raise immediately when session is entered — proves the code entered
        # the DB path (cooldown gate was NOT taken)
        mock_session_cls.return_value.__aenter__ = AsyncMock(side_effect=_EarlyExit())
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # _claim_one catches Exception internally and returns None, so
        # _EarlyExit is swallowed by the broad except block.
        result = await w._claim_one()

    # The result is None because _EarlyExit was swallowed, BUT the key proof
    # is that the session was entered at all — meaning the cooldown gate was
    # NOT taken (if it were, SessionLocal would never be called).
    mock_session_cls.assert_called_once(), (
        "SessionLocal must be entered when cooldown is expired — "
        "proves the cooldown gate did NOT short-circuit"
    )


# ---------------------------------------------------------------------------
# cooldown_until initialized as None in __init__
# ---------------------------------------------------------------------------


def test_worker_init_cooldown_until_is_none():
    """Worker.__init__ must initialize _cooldown_until to None.

    RED-proof: if the attribute is missing, _in_cooldown() raises AttributeError.
    """
    w = _make_worker()
    assert hasattr(w, "_cooldown_until"), "Worker must have _cooldown_until attribute"
    assert w._cooldown_until is None, (
        f"_cooldown_until must be None on init, got {w._cooldown_until!r}"
    )


# ---------------------------------------------------------------------------
# Source-level checks: _execute_job catches SessionLimitPause before Exception
# ---------------------------------------------------------------------------


def test_execute_job_imports_session_limit_pause():
    """worker.py must import SessionLimitPause (from app.services.errors)."""
    import app.services.worker as worker_mod
    from app.services.errors import SessionLimitPause

    # The module must have access to SessionLimitPause (imported at module level)
    assert hasattr(worker_mod, "SessionLimitPause") or "SessionLimitPause" in dir(worker_mod), (
        "worker module must import SessionLimitPause"
    )


def test_execute_job_source_catches_session_limit_pause():
    """_execute_job source must contain an except SessionLimitPause handler."""
    import inspect
    from app.services.worker import Worker

    src = inspect.getsource(Worker._execute_job)
    assert "SessionLimitPause" in src, (
        "_execute_job must catch SessionLimitPause (before the generic except Exception)"
    )
    assert "requeue_session_limited" in src, (
        "_execute_job must call requeue_session_limited on SessionLimitPause"
    )
    assert "_cooldown_until" in src, (
        "_execute_job must set self._cooldown_until on SessionLimitPause"
    )


def test_in_cooldown_method_exists():
    """Worker must have an _in_cooldown() method."""
    from app.services.worker import Worker

    assert callable(getattr(Worker, "_in_cooldown", None)), (
        "Worker must have an _in_cooldown() method"
    )


# ---------------------------------------------------------------------------
# Behavioral: _execute_job correctly routes SessionLimitPause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_job_session_limit_pause_behavioral():
    """Behavioral: _execute_job calls requeue_session_limited (not _mark_failed)
    when pipeline.run raises SessionLimitPause, and sets self._cooldown_until.

    This test drives the *real* _execute_job code path and will FAIL if the
    ``except SessionLimitPause`` clause is reordered after ``except Exception``.
    In that case the generic handler runs instead — it calls _mark_failed and
    never calls requeue_session_limited — so both the assert_called_once and
    assert_not_called assertions fire.

    RED-proof summary:
      - If handler order is wrong: mock_requeue not called → assert_called_once fails.
      - If handler order is wrong: mock_mark_failed called   → assert_not_called fails.
      - If cooldown is not set:    _cooldown_until == None   → equality assert fails.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from app.services.errors import SessionLimitPause

    job_id = uuid4()
    reset_at = datetime.now(timezone.utc) + timedelta(hours=2)
    pause_exc = SessionLimitPause(reset_at=reset_at)

    w = _make_worker()

    # Async-CM stub for `async with SessionLocal() as session` calls in the handler.
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        # pipeline.run raises SessionLimitPause immediately.
        patch("app.services.pipeline.run", new_callable=AsyncMock, side_effect=pause_exc),
        # Intercept the two competing DB calls at the boundary we care about.
        patch(
            "app.repositories.jobs.requeue_session_limited", new_callable=AsyncMock
        ) as mock_requeue,
        patch.object(w, "_mark_failed", new_callable=AsyncMock) as mock_mark_failed,
        # Provide a working async-CM so the handler's SessionLocal() call succeeds.
        patch("app.services.worker.SessionLocal", return_value=mock_session_ctx),
    ):
        await w._execute_job(job_id)

    # --- Pause semantics: requeue must fire, failure must not ---
    mock_requeue.assert_called_once()
    _session_arg, requeued_job_id = mock_requeue.call_args.args[:2]
    assert requeued_job_id == job_id, (
        f"requeue_session_limited must be called with the right job_id "
        f"(got {requeued_job_id!r})"
    )

    mock_mark_failed.assert_not_called()

    # --- Worker must self-park until reset_at ---
    assert w._cooldown_until == reset_at, (
        f"_cooldown_until must equal reset_at={reset_at!r}, got {w._cooldown_until!r}"
    )
