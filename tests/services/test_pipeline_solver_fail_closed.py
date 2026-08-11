"""Fail-closed policy after the solver proves a high-confidence mismatch.

Every test starts after a mismatch has been established. Provider transients
must escape for the existing bounded queue retry; hard repair failures must
persist the inspected artifact as ``mismatch_blocked``; lease/cancel/park
signals must retain their control-flow meaning.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings as _settings
from app.services import pipeline
from app.services.errors import (
    CancelWonSignal,
    LeaseLostSignal,
    PersistentSolverMismatch,
    SessionLimitPause,
    SlotSaturation,
    TransientPhaseError,
)
from app.services.lease import CancelRequested, JobLease, LeaseLost
from app.services.solver import SolveOutcome
from tests.services.test_pipeline_solver import (
    _agree,
    _make_kwargs,
    _mismatch,
    patch_io,
)


async def test_known_mismatch_plus_transient_regen_failure_escapes_for_queue_retry(
    monkeypatch, patch_io
):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_mismatch()]

    async def transient_on_regen(*, requested_provider, model, run_fn, transport, **kw):
        patch_io.failover_calls.append((requested_provider, model, transport))
        if patch_io.failover_outputs:
            return patch_io.failover_outputs.pop(0)
        raise ConnectionError("connection reset")

    monkeypatch.setattr(pipeline, "_run_with_failover", transient_on_regen)

    with pytest.raises(ConnectionError, match="connection reset"):
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert not [c for c in patch_io.set_status_calls if c[0] in {"done", "failed"}]


async def test_known_mismatch_plus_api_auth_repair_failure_is_blocked(
    monkeypatch, patch_io
):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_mismatch()]

    async def auth_on_regen(*, requested_provider, model, run_fn, transport, **kw):
        if patch_io.failover_outputs:
            return patch_io.failover_outputs.pop(0)
        raise RuntimeError("401 unauthenticated")

    monkeypatch.setattr(pipeline, "_run_with_failover", auth_on_regen)
    kw = _make_kwargs("memory-check")
    kw["transport"] = "api"

    with pytest.raises(PersistentSolverMismatch) as caught:
        await pipeline._execute_phase(**kw)

    assert "401 unauthenticated" in str(caught.value)
    failed = [c for c in patch_io.set_status_calls if c[0] == "failed"][-1][1]
    assert failed["solver_status"] == "mismatch_blocked"
    assert not [c for c in patch_io.set_status_calls if c[0] == "done"]


async def test_known_mismatch_plus_hard_recheck_unavailable_becomes_blocked(patch_io):
    failure = RuntimeError("invalid solver verdict")
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# regenerated output", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [
        _mismatch(),
        SolveOutcome(
            available=False,
            agrees=True,
            warnings=["solver-unavailable: RuntimeError"],
            feedback="",
            has_mismatch=False,
            refused=False,
            failure=failure,
        ),
    ]

    with pytest.raises(PersistentSolverMismatch) as caught:
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert caught.value.repair_error is failure
    failed = [c for c in patch_io.set_status_calls if c[0] == "failed"][-1][1]
    assert failed["output_md"] == "# regenerated output"
    assert failed["solver_status"] == "mismatch_blocked"
    assert not [c for c in patch_io.set_status_calls if c[0] == "done"]


async def test_known_mismatch_plus_transient_recheck_unavailable_escapes(patch_io):
    failure = ConnectionError("connection reset during solver recheck")
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# regenerated output", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [
        _mismatch(),
        SolveOutcome(
            available=False,
            agrees=True,
            warnings=["solver-unavailable: ConnectionError"],
            feedback="",
            has_mismatch=False,
            refused=False,
            failure=failure,
        ),
    ]

    with pytest.raises(ConnectionError) as caught:
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert caught.value is failure
    assert not [c for c in patch_io.set_status_calls if c[0] in {"done", "failed"}]


async def test_later_repair_failure_retains_latest_proven_mismatch(
    monkeypatch, patch_io
):
    initial = _mismatch()
    latest = SolveOutcome(
        available=True,
        agrees=False,
        warnings=["[high] Q7: regenerated key is still wrong"],
        feedback="fix Q7",
        has_mismatch=True,
    )
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# first repair", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [initial, latest]
    monkeypatch.setattr(_settings, "max_solve_regens", 2)

    async def fail_second_repair(*, requested_provider, model, run_fn, transport, **kw):
        if patch_io.failover_outputs:
            return patch_io.failover_outputs.pop(0)
        raise RuntimeError("invalid repair request")

    monkeypatch.setattr(pipeline, "_run_with_failover", fail_second_repair)

    with pytest.raises(PersistentSolverMismatch) as caught:
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert caught.value.warnings == tuple(latest.warnings)


@pytest.mark.parametrize(
    ("refused", "expected_status"),
    [(False, "unavailable"), (True, "refused")],
)
async def test_initial_solver_unavailable_remains_advisory(
    patch_io, refused, expected_status
):
    failure = RuntimeError("model down")
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [
        SolveOutcome(
            available=False,
            agrees=True,
            warnings=["solver-refused: content policy" if refused else "solver-unavailable: RuntimeError"],
            feedback="",
            has_mismatch=False,
            refused=refused,
            failure=failure,
        )
    ]

    await pipeline._execute_phase(**_make_kwargs("memory-check"))

    done = [c for c in patch_io.set_status_calls if c[0] == "done"][-1][1]
    assert done["solver_status"] == expected_status
    assert not [c for c in patch_io.set_status_calls if c[0] == "failed"]


@pytest.mark.parametrize(
    "signal",
    [
        LeaseLostSignal(),
        CancelWonSignal(),
        SessionLimitPause(None),
        SlotSaturation("fleet credential slot wait exhausted"),
        TransientPhaseError("memory-check: provider unavailable"),
    ],
    ids=["lease", "cancel", "session", "slot", "typed-transient"],
)
async def test_solver_regen_control_and_transient_signals_pass_through(
    monkeypatch, patch_io, signal
):
    patch_io.failover_outputs = [("# initial output", 100, 50, "claude")]
    patch_io.solve_outputs = [_mismatch()]

    async def fail_on_regen(*, requested_provider, model, run_fn, transport, **kw):
        if patch_io.failover_outputs:
            return patch_io.failover_outputs.pop(0)
        raise signal

    monkeypatch.setattr(pipeline, "_run_with_failover", fail_on_regen)

    with pytest.raises(type(signal)) as caught:
        await pipeline._execute_phase(**_make_kwargs("memory-check"))

    assert caught.value is signal
    assert not [c for c in patch_io.set_status_calls if c[0] in {"done", "failed"}]


@pytest.mark.parametrize(
    ("repo_result", "expected_signal"),
    [(LeaseLost, LeaseLostSignal), (CancelRequested, CancelWonSignal)],
)
async def test_blocked_phase_write_obeys_lease_and_cancel_signal(
    monkeypatch, patch_io, repo_result, expected_signal
):
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# still wrong", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _mismatch()]
    attempted_failed_writes = []

    async def fenced_set_status(session, po_id, status, **kw):
        patch_io.set_status_calls.append((status, kw))
        if status == "failed":
            attempted_failed_writes.append(kw)
            return repo_result
        return None

    monkeypatch.setattr(pipeline.phase_repo, "set_status", fenced_set_status)
    lease = JobLease(
        job_id=uuid.uuid4(), claim_token=uuid.uuid4(), owner_id="test-worker"
    )
    kw = _make_kwargs("memory-check")
    kw["job_id"] = lease.job_id
    kw["lease"] = lease

    with pytest.raises(expected_signal):
        await pipeline._execute_phase(**kw)

    assert len(attempted_failed_writes) == 1
    assert attempted_failed_writes[0]["claim_token"] == lease.claim_token
    assert not [c for c in patch_io.set_status_calls if c[0] == "done"]


async def test_regenerated_agreement_still_completes_normally(patch_io):
    patch_io.failover_outputs = [
        ("# initial output", 100, 50, "claude"),
        ("# corrected output", 110, 55, "claude"),
    ]
    patch_io.solve_outputs = [_mismatch(), _agree()]

    await pipeline._execute_phase(**_make_kwargs("memory-check"))

    done = [c for c in patch_io.set_status_calls if c[0] == "done"][-1][1]
    assert done["solver_status"] == "mismatch_regen"
    assert done["output_md"] == "# corrected output"
