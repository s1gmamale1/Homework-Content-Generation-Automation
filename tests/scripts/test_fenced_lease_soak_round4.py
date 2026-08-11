from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_snapshot import (
    healthy_completed_snapshot,
    healthy_running_snapshot,
    runtime_attestation,
    valid_scope,
)
from tests.scripts.test_fenced_lease_soak_watch import (
    FakeStore,
    StageWriteStore,
    load_summary,
    pristine_staged_snapshot,
)


class TriggeredSignalHandlers:
    """Loop-safe signal seam: tests choose exactly when termination fires."""

    def __init__(self):
        self.callback: Callable[[], None] | None = None
        self.installed = False
        self.restored = False

    @contextmanager
    def install(self, callback: Callable[[], None]):
        self.callback = callback
        self.installed = True
        try:
            yield
        finally:
            self.restored = True

    def terminate(self) -> None:
        assert self.callback is not None
        self.callback()


class ImmediateSignalHandlers(TriggeredSignalHandlers):
    @contextmanager
    def install(self, callback: Callable[[], None]):
        self.callback = callback
        self.installed = True
        callback()
        try:
            yield
        finally:
            self.restored = True


class SignalOnCollectStore(FakeStore):
    def __init__(self, snapshots, handlers: TriggeredSignalHandlers, *, call: int):
        super().__init__(snapshots)
        self.handlers = handlers
        self.signal_call = call

    async def collect(self, scope: soak.SoakScope) -> soak.RawSnapshot:
        if self.collect_count == self.signal_call:
            self.handlers.terminate()
            await asyncio.sleep(3600)
        return await super().collect(scope)


class CancellationResistantStopper:
    def __init__(self, delegate):
        self.delegate = delegate
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def pause(self, scope, trigger):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        return await self.delegate.pause(scope, trigger)


class FatalExit(BaseException):
    pass


class NeverCancelsStopper:
    def __init__(self):
        self.started = asyncio.Event()
        self.block = asyncio.Event()
        self.finished = asyncio.Event()

    async def pause(self, scope, trigger):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            while not self.block.is_set():
                try:
                    await self.block.wait()
                except asyncio.CancelledError:
                    continue
        self.finished.set()
        return soak.StopReceipt(
            run_id=scope.run_id,
            observed_at=healthy_running_snapshot().observed_at,
            trigger_code=trigger.code,
            paused_batch_ids=[],
            fleet_pause_set=False,
        )


def _write_inputs(tmp_path: Path, scope: soak.SoakScope) -> tuple[Path, Path]:
    scope_path = tmp_path / "scope.json"
    attestation_path = tmp_path / "attestation.json"
    scope_path.write_text(soak.canonical_json(scope), encoding="utf-8")
    attestation_path.write_text(
        soak.canonical_json(runtime_attestation(scope)), encoding="utf-8"
    )
    return scope_path, attestation_path


@pytest.mark.asyncio
async def test_sigterm_after_release_routes_through_armed_stop_and_restores_handlers(
    tmp_path,
):
    scope = valid_scope(target=4)
    scope_path, attestation_path = _write_inputs(tmp_path, scope)
    handlers = TriggeredSignalHandlers()
    read_store = SignalOnCollectStore(
        [pristine_staged_snapshot(scope), healthy_running_snapshot(running=4)],
        handlers,
        call=1,
    )
    write_store = StageWriteStore()

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            str(scope_path),
            "--attestation",
            str(attestation_path),
            "--artifact-dir",
            str(tmp_path),
            "--arm-stop",
            "--confirm-arm",
            f"lease-soak-stop:{scope.run_id}",
        ],
        store_factory=lambda _: read_store,
        write_store_factory=lambda _: write_store,
        database_url="postgresql+asyncpg://ignored/scratch",
        clock=lambda: healthy_running_snapshot().observed_at,
        sleep=lambda _: asyncio.sleep(0),
        stdout=io.StringIO(),
        signal_handler_factory=handlers.install,
    )

    assert code == soak.ExitCode.HARD_STOP_ARMED
    assert handlers.installed and handlers.restored
    assert write_store.fleet_pause_reason == f"lease-soak-stop:{scope.run_id}"
    assert load_summary(tmp_path, scope.run_id)["findings"][0]["code"] == (
        "watch_incomplete"
    )


@pytest.mark.asyncio
async def test_sigterm_before_release_is_incomplete_and_never_mutates(tmp_path):
    scope = valid_scope(target=4)
    scope_path, attestation_path = _write_inputs(tmp_path, scope)
    handlers = TriggeredSignalHandlers()
    read_store = SignalOnCollectStore(
        [pristine_staged_snapshot(scope)], handlers, call=0
    )
    write_store = StageWriteStore()

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            str(scope_path),
            "--attestation",
            str(attestation_path),
            "--artifact-dir",
            str(tmp_path),
            "--arm-stop",
            "--confirm-arm",
            f"lease-soak-stop:{scope.run_id}",
        ],
        store_factory=lambda _: read_store,
        write_store_factory=lambda _: write_store,
        database_url="postgresql+asyncpg://ignored/scratch",
        clock=lambda: healthy_running_snapshot().observed_at,
        sleep=lambda _: asyncio.sleep(0),
        stdout=io.StringIO(),
        signal_handler_factory=handlers.install,
    )

    assert code == soak.ExitCode.INCOMPLETE
    assert handlers.installed and handlers.restored
    assert write_store.stop_calls == 0
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "incomplete"


@pytest.mark.asyncio
async def test_signal_at_handler_install_still_enters_pre_release_fail_closed_path(
    tmp_path,
):
    scope = valid_scope(target=4)
    scope_path, attestation_path = _write_inputs(tmp_path, scope)
    handlers = ImmediateSignalHandlers()
    write_store = StageWriteStore()

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            str(scope_path),
            "--attestation",
            str(attestation_path),
            "--artifact-dir",
            str(tmp_path),
            "--arm-stop",
            "--confirm-arm",
            f"lease-soak-stop:{scope.run_id}",
        ],
        store_factory=lambda _: FakeStore([pristine_staged_snapshot(scope)]),
        write_store_factory=lambda _: write_store,
        database_url="postgresql+asyncpg://ignored/scratch",
        clock=lambda: healthy_running_snapshot().observed_at,
        sleep=lambda _: asyncio.sleep(0),
        stdout=io.StringIO(),
        signal_handler_factory=handlers.install,
    )

    assert code == soak.ExitCode.INCOMPLETE
    assert write_store.stop_calls == 0
    assert handlers.restored
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "incomplete"


@pytest.mark.asyncio
async def test_signal_during_preflight_is_incomplete_with_durable_evidence(tmp_path):
    scope = valid_scope(target=4)
    scope_path, attestation_path = _write_inputs(tmp_path, scope)
    handlers = ImmediateSignalHandlers()

    code = await soak.async_main(
        [
            "preflight",
            "--scope",
            str(scope_path),
            "--attestation",
            str(attestation_path),
            "--artifact-dir",
            str(tmp_path),
        ],
        store_factory=lambda _: FakeStore([pristine_staged_snapshot(scope)]),
        database_url="postgresql+asyncpg://ignored/scratch",
        clock=lambda: healthy_running_snapshot().observed_at,
        signal_handler_factory=handlers.install,
    )

    assert code == soak.ExitCode.INCOMPLETE
    assert handlers.installed and handlers.restored
    summary = load_summary(tmp_path, scope.run_id)
    assert summary["verdict"] == "incomplete"
    assert summary["findings"][0]["code"] == "preflight_incomplete"


@pytest.mark.parametrize("termination_signal", [signal.SIGINT, signal.SIGTERM])
def test_real_posix_termination_cancels_task_and_restores_handler(termination_signal):
    if os.name != "posix":
        pytest.skip("POSIX signal contract")
    code = """
import asyncio, os, signal
from scripts import fenced_lease_soak as soak
async def probe():
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    async def work():
        print('READY', flush=True)
        await asyncio.sleep(3600)
    try:
        await soak._await_with_termination_signals(work())
    except asyncio.CancelledError:
        print('CANCELLED', flush=True)
    print('RESTORED=' + str(all(signal.getsignal(sig) == handler for sig, handler in before.items())), flush=True)
asyncio.run(probe())
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    proc.send_signal(termination_signal)
    stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 0, stderr
    assert stdout.splitlines() == ["CANCELLED", "RESTORED=True"]


def test_default_fatal_stop_cleanup_exits_process_after_durable_unknown_state(tmp_path):
    if os.name != "posix":
        pytest.skip("process-fatal POSIX contract")
    artifact_dir = tmp_path / "fatal"
    code = f"""
import asyncio
from pathlib import Path
from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_snapshot import healthy_running_snapshot, valid_scope
class Stopper:
    async def pause(self, scope, trigger):
        del scope, trigger
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    continue
async def probe():
    scope = valid_scope(target=4)
    trigger = soak._runtime_hard('lease_lost', 'lease lost')
    await soak._finish_armed_stop(
        scope=scope,
        stopper=Stopper(),
        writer=soak.ArtifactWriter(Path({str(artifact_dir)!r}), scope.run_id),
        raw_samples=[healthy_running_snapshot(running=4)],
        findings=[trigger],
        trigger=trigger,
        clock=lambda: healthy_running_snapshot().observed_at,
        stop_timeout_seconds=0.01,
        stop_cancel_grace_seconds=0.01,
    )
asyncio.run(probe())
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert proc.returncode == int(soak.ExitCode.OPERATIONAL_ERROR), proc.stderr
    summary = load_summary(artifact_dir, valid_scope(target=4).run_id)
    assert summary["verdict"] == "stop_state_unknown_fatal_exit"
    assert summary["findings"][-1]["code"] == "armed_stop_cleanup_stuck"


@pytest.mark.asyncio
async def test_timeout_waits_for_cancellation_resistant_stop_before_returning(tmp_path):
    scope = valid_scope(target=4)
    write_store = StageWriteStore()
    stopper = CancellationResistantStopper(
        soak.GuardedStopper(write_store, clock=lambda: healthy_running_snapshot().observed_at)
    )
    trigger = soak._runtime_hard("lease_lost", "lease lost")
    task = asyncio.create_task(
        soak._finish_armed_stop(
            scope=scope,
            stopper=stopper,
            writer=soak.ArtifactWriter(tmp_path, scope.run_id),
            raw_samples=[healthy_running_snapshot(running=4)],
            findings=[trigger],
            trigger=trigger,
            clock=lambda: healthy_running_snapshot().observed_at,
            stop_timeout_seconds=0.01,
            stop_cancel_grace_seconds=0.2,
        )
    )
    await stopper.cancelled.wait()
    await asyncio.sleep(0.02)
    assert not task.done()
    assert write_store.stop_calls == 0

    stopper.release.set()
    code = await asyncio.wait_for(task, timeout=0.5)

    assert code == soak.ExitCode.HARD_STOP_ARMED
    assert write_store.stop_calls == 1
    assert load_summary(tmp_path, scope.run_id)["stop_receipt"] is not None


@pytest.mark.asyncio
async def test_uncancellable_stop_uses_fatal_no_return_path_with_unknown_state_evidence(
    tmp_path,
):
    scope = valid_scope(target=4)
    stopper = NeverCancelsStopper()
    trigger = soak._runtime_hard("lease_lost", "lease lost")

    def fatal_exit(code: int):
        stopper.block.set()
        raise FatalExit(code)

    with pytest.raises(FatalExit):
        await soak._finish_armed_stop(
            scope=scope,
            stopper=stopper,
            writer=soak.ArtifactWriter(tmp_path, scope.run_id),
            raw_samples=[healthy_running_snapshot(running=4)],
            findings=[trigger],
            trigger=trigger,
            clock=lambda: healthy_running_snapshot().observed_at,
            stop_timeout_seconds=0.01,
            stop_cancel_grace_seconds=0.01,
            fatal_exit=fatal_exit,
        )

    await asyncio.sleep(0)
    assert stopper.finished.is_set()

    summary = load_summary(tmp_path, scope.run_id)
    assert summary["verdict"] == "stop_state_unknown_fatal_exit"
    assert summary["findings"][-1]["code"] == "armed_stop_cleanup_stuck"
    assert set(summary["findings"][-1]["evidence"]) == {
        "error_type",
        "error_sha256",
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda raw: raw.scrub_tombstones.append("Host-01"), "tombstoned"),
        (lambda raw: setattr(raw.budget, "min_worker_version", 1002), "version_floor"),
        (lambda raw: setattr(raw.workers[0], "can_gemini_api", False), "capability"),
        (lambda raw: setattr(raw.workers[0], "status", "offline"), "status"),
        (lambda raw: setattr(raw.workers[0], "code_version", 999), "code_version"),
    ],
)
def test_attested_claimable_set_shrink_is_immediate(mutate, reason):
    scope = valid_scope(target=4)
    raw = healthy_running_snapshot(running=4)
    mutate(raw)

    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), raw, previous_samples=[]
    )

    finding = next(item for item in findings if item.code == "attested_worker_unclaimable")
    assert finding.hard_stop is True
    assert reason in finding.evidence["reasons"]["Host-01"]


def test_heartbeat_only_shrink_keeps_two_sample_jitter_rule():
    scope = valid_scope(target=4)
    first = healthy_running_snapshot(running=4)
    first.workers[0].last_heartbeat = first.observed_at.replace(year=2025)
    assert "heartbeat_stale" not in {
        finding.code
        for finding in soak.evaluate_runtime(
            scope, runtime_attestation(scope), first, previous_samples=[]
        )
    }
    second = first.model_copy(deep=True)
    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), second, previous_samples=[first]
    )
    assert "heartbeat_stale" in {finding.code for finding in findings}


def test_explicit_capability_shrink_is_immediate_even_with_stale_heartbeat():
    scope = valid_scope(target=4)
    raw = healthy_running_snapshot(running=4)
    raw.workers[0].last_heartbeat = raw.observed_at.replace(year=2025)
    raw.workers[0].can_gemini_api = False

    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), raw, previous_samples=[]
    )

    finding = next(item for item in findings if item.code == "attested_worker_unclaimable")
    assert finding.hard_stop is True
    assert "capability" in finding.evidence["reasons"]["Host-01"]
    assert "heartbeat_stale" not in {item.code for item in findings}


@pytest.mark.parametrize("judge_status", [None, "unavailable", "refused"])
def test_terminal_missing_or_untrusted_judge_proof_fails_stage(judge_status):
    scope = valid_scope(target=4)
    raw = healthy_completed_snapshot(target=4)
    phase = next(row for row in raw.phases if row.phase_name == "flashcards")
    phase.judge_status = judge_status

    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), raw, [healthy_running_snapshot(running=4)]
    )

    finding = next(item for item in findings if item.code == "judge_proof_missing")
    assert finding.stage_failure and not finding.hard_stop


@pytest.mark.parametrize("solver_status", [None, "unavailable", "refused"])
def test_terminal_missing_or_untrusted_required_solver_proof_fails_stage(solver_status):
    scope = valid_scope(target=4)
    raw = healthy_completed_snapshot(target=4)
    phase = next(row for row in raw.phases if row.phase_name == "memory-check")
    phase.solver_status = solver_status

    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), raw, [healthy_running_snapshot(running=4)]
    )

    finding = next(item for item in findings if item.code == "solver_proof_missing")
    assert finding.stage_failure and not finding.hard_stop


def test_mismatch_regen_remains_successful_solver_proof():
    scope = valid_scope(target=4)
    raw = healthy_completed_snapshot(target=4)
    phase = next(row for row in raw.phases if row.phase_name == "memory-check")
    phase.solver_status = "mismatch_regen"

    findings = soak.evaluate_runtime(
        scope, runtime_attestation(scope), raw, [healthy_running_snapshot(running=4)]
    )

    assert "solver_mismatch" not in {item.code for item in findings}
    assert "solver_proof_missing" not in {item.code for item in findings}


@pytest.mark.asyncio
async def test_terminal_unavailable_proof_routes_through_armed_hard_stop(tmp_path):
    scope = valid_scope(target=4)
    terminal = healthy_completed_snapshot(target=4)
    next(row for row in terminal.phases if row.phase_name == "flashcards").judge_status = (
        "unavailable"
    )
    write_store = StageWriteStore()

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=FakeStore(
            [
                pristine_staged_snapshot(scope),
                healthy_running_snapshot(running=4),
                terminal,
            ]
        ),
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=soak.GuardedStopper(
            write_store, clock=lambda: healthy_running_snapshot().observed_at
        ),
        clock=lambda: healthy_running_snapshot().observed_at,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert code == soak.ExitCode.HARD_STOP_ARMED
    summary = load_summary(tmp_path, scope.run_id)
    assert summary["stop_receipt"]["trigger_code"] == "terminal_stage_failure"
    assert "judge_proof_missing" in {
        finding["code"] for finding in summary["findings"]
    }
