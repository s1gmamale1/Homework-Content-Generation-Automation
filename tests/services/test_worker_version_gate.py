"""Unit tests for the worker version claim gate (fleet-worker-version-gate-1).

RED-proofs:
  - If _claim_one never consults is_stale, a stale worker still calls
    claim_next_job — the no-call assertion fails.
  - If the gate compared with <= instead of <, an at-floor worker would be
    blocked — the at-floor test fails.
  - If unknown version (None) passed the gate, the fail-closed test fails.
  - Throttle: second immediate blocked poll must NOT emit a second ERROR.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import code_version
from app.services import operator_auth
from app.services.worker import Worker


def _mock_state(floor):
    state = MagicMock()
    state.api_paused_at = None
    state.min_worker_version = floor
    return state


class _AsyncCM:
    """Minimal async-context-manager double whose __aenter__ returns a fixed
    value — used for both `SessionLocal()` and `session.begin()`."""

    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def _run_claim(worker, *, floor, version):
    """Drive one _claim_one with a mocked session/budget/claim layer.
    Returns the claim_next_job mock."""
    claim_mock = AsyncMock(return_value=None)

    session = MagicMock()
    session.begin = MagicMock(return_value=_AsyncCM())

    with patch(
        "app.services.worker.SessionLocal", MagicMock(return_value=_AsyncCM(session))
    ), patch(
        "app.services.worker.budget_repo.get_state", AsyncMock(return_value=_mock_state(floor))
    ), patch(
        "app.services.worker.jobs_repo.claim_next_job", claim_mock
    ), patch(
        # Claim-side scrub gate (task 3): the shared host lock + tombstone
        # re-read run BEFORE the budget/version gate this file tests, on the
        # same mocked `session`. Stub both no-op (no scrub pending) so this
        # file keeps testing the version gate in isolation.
        "app.services.worker.workers_repo.lock_host_shared", AsyncMock(return_value=None)
    ), patch(
        "app.services.worker.sa_keys_repo.scrub_pending_for_host", AsyncMock(return_value=False)
    ), patch.object(
        code_version, "CODE_VERSION", version
    ):
        asyncio.run(worker._claim_one())
    return claim_mock


def test_stale_worker_never_calls_claim():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=100)
    claim.assert_not_called()


def test_unknown_version_with_floor_is_blocked():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=None)
    claim.assert_not_called()


def test_ahead_override_process_started_during_rotation_is_still_blocked():
    """The temporary floor must dominate a known configured override."""
    _, temporary_floor = operator_auth.rotation_version_floors(
        prior_floor=953,
        target_code_version=1000,
        reported_code_versions=(1000,),
        configured_overrides=(1500,),
    )
    worker = Worker(concurrency=1)

    claim = _run_claim(worker, floor=temporary_floor, version=1500)

    claim.assert_not_called()


def test_at_floor_worker_claims():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=200, version=200)
    claim.assert_called_once()


def test_no_floor_claims():
    w = Worker(concurrency=1)
    claim = _run_claim(w, floor=None, version=None)
    claim.assert_called_once()


def test_stale_log_is_throttled():
    """First blocked poll logs ERROR; an immediate second poll does not."""
    w = Worker(concurrency=1)
    emitted = []
    with patch("app.services.worker.logger") as mock_log:
        mock_log.error = MagicMock(side_effect=lambda *a, **k: emitted.append(a))
        _run_claim(w, floor=200, version=100)
        _run_claim(w, floor=200, version=100)
    assert len(emitted) == 1
    assert "version gate: STALE" in emitted[0][0]


def test_worker_init_has_stale_gate_logged_at():
    w = Worker(concurrency=1)
    assert hasattr(w, "_stale_gate_logged_at")
    assert w._stale_gate_logged_at is None
