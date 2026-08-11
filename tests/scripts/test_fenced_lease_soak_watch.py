from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pytest

from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_snapshot import (
    healthy_completed_snapshot,
    healthy_running_snapshot,
    runtime_attestation,
    usage_row,
    valid_scope,
)


NOW = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, snapshots: Sequence[soak.RawSnapshot]):
        self._snapshots = [snapshot.model_copy(deep=True) for snapshot in snapshots]
        self.collect_count = 0

    async def collect(self, scope: soak.SoakScope) -> soak.RawSnapshot:
        del scope
        index = min(self.collect_count, len(self._snapshots) - 1)
        self.collect_count += 1
        return self._snapshots[index].model_copy(deep=True)


class FakeClock:
    def __init__(self):
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeSleep:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def pristine_staged_snapshot(scope: soak.SoakScope) -> soak.RawSnapshot:
    raw = healthy_completed_snapshot(target=scope.target_running)
    raw.budget.api_paused_reason = f"lease-soak-staging:{scope.run_id}"
    raw.lease_events = []
    raw.phases = []
    raw.usages = []
    for job in raw.jobs:
        job.status = "pending"
        job.attempts = 0
        job.claim_token = None
        job.claimed_by = None
        job.error_message = None
        job.last_error = None
        job.notion_archived_at = None
        job.notion_skip_reason = None
        job.phase_count = 0
        job.usage_count = 0
        job.lease_count = 0
    return raw


def hard_runtime_snapshot(scope: soak.SoakScope) -> soak.RawSnapshot:
    raw = healthy_running_snapshot(running=scope.target_running)
    raw.lease_events.append(
        soak.LeaseEventSnapshot(
            job_id=scope.job_ids[0],
            claim_token=raw.jobs[0].claim_token,
            event_type="lease_lost",
            owner=raw.jobs[0].claimed_by,
            created_at=NOW,
        )
    )
    return raw


def load_summary(tmp_path, run_id: str) -> dict:
    return json.loads((tmp_path / f"{run_id}.summary.json").read_text())


@pytest.mark.asyncio
async def test_watch_reaches_target_then_requires_sixty_clean_seconds(tmp_path):
    scope = valid_scope(target=4, settle_seconds=60)
    completed = healthy_completed_snapshot(target=4)
    store = FakeStore(
        [
            pristine_staged_snapshot(scope),
            healthy_running_snapshot(running=4),
            completed,
            completed,
            completed,
        ]
    )
    clock = FakeClock()

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=clock,
        sleep=FakeSleep(clock),
        interval_seconds=30,
    )

    assert code == soak.ExitCode.PASS
    assert store.collect_count == 5
    summary = load_summary(tmp_path, scope.run_id)
    assert summary["verdict"] == "pass"
    assert summary["peaks"]["running_jobs"] == 4
    assert summary["settle_seconds"] == 60


@pytest.mark.asyncio
async def test_new_usage_during_settle_restarts_the_quiet_clock(tmp_path):
    scope = valid_scope(target=4, settle_seconds=60)
    completed = healthy_completed_snapshot(target=4)
    changed = completed.model_copy(deep=True)
    changed.usages.append(usage_row(job_id=scope.job_ids[0]))
    store = FakeStore(
        [
            pristine_staged_snapshot(scope),
            healthy_running_snapshot(running=4),
            completed,
            changed,
            changed,
            changed,
        ]
    )
    clock = FakeClock()

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=clock,
        sleep=FakeSleep(clock),
        interval_seconds=30,
    )

    assert code == soak.ExitCode.PASS
    assert store.collect_count == 6


@pytest.mark.asyncio
async def test_dirty_initial_stage_refuses_to_watch(tmp_path):
    scope = valid_scope(target=4)
    dirty = pristine_staged_snapshot(scope)
    dirty.jobs[0].attempts = 1
    store = FakeStore([dirty])

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=lambda: NOW,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert code == soak.ExitCode.PREFLIGHT_FAILED
    assert store.collect_count == 1
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "preflight_failed"


@pytest.mark.asyncio
async def test_read_only_hard_stop_records_and_exits_without_stopper(tmp_path):
    scope = valid_scope(target=4)
    store = FakeStore([pristine_staged_snapshot(scope), hard_runtime_snapshot(scope)])
    clock = FakeClock()

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=clock,
        sleep=FakeSleep(clock),
        interval_seconds=2,
    )

    assert code == soak.ExitCode.HARD_STOP_READ_ONLY
    samples = [
        json.loads(line)
        for line in (tmp_path / f"{scope.run_id}.samples.jsonl").read_text().splitlines()
    ]
    assert any(
        finding["code"] == "lease_lost"
        for finding in samples[-1]["findings"]
    )
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "hard_stop"


@pytest.mark.asyncio
async def test_staging_pause_cannot_reappear_after_release(tmp_path):
    scope = valid_scope(target=4)
    running = healthy_running_snapshot(running=4)
    paused_again = running.model_copy(deep=True)
    paused_again.budget.api_paused_reason = f"lease-soak-staging:{scope.run_id}"
    store = FakeStore(
        [pristine_staged_snapshot(scope), running, paused_again]
    )
    clock = FakeClock()

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=clock,
        sleep=FakeSleep(clock),
        interval_seconds=2,
    )

    assert code == soak.ExitCode.HARD_STOP_READ_ONLY
    assert load_summary(tmp_path, scope.run_id)["findings"][0]["code"] == (
        "staging_pause_reappeared"
    )


@pytest.mark.asyncio
async def test_cancelled_watch_writes_incomplete_without_invoking_stopper(tmp_path):
    scope = valid_scope(target=4)
    store = FakeStore([pristine_staged_snapshot(scope)])

    async def cancelled_sleep(seconds: float) -> None:
        del seconds
        raise asyncio.CancelledError

    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        stopper=None,
        clock=lambda: NOW,
        sleep=cancelled_sleep,
    )

    assert code == soak.ExitCode.INCOMPLETE
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "incomplete"


@pytest.mark.asyncio
async def test_preflight_records_one_sample_and_atomic_summary(tmp_path):
    scope = valid_scope(target=4)
    store = FakeStore([pristine_staged_snapshot(scope)])

    code = await soak.run_preflight(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        clock=lambda: NOW,
    )

    assert code == soak.ExitCode.PASS
    assert len(
        (tmp_path / f"{scope.run_id}.samples.jsonl").read_text().splitlines()
    ) == 1
    assert load_summary(tmp_path, scope.run_id)["verdict"] == "pass"


def test_artifact_is_redacted_append_only_and_summary_is_atomic(
    tmp_path, monkeypatch
):
    replace_calls = []
    fsync_calls = []
    real_replace = soak.os.replace
    real_fsync = soak.os.fsync

    def tracked_replace(source, destination):
        replace_calls.append((source, destination))
        real_replace(source, destination)

    def tracked_fsync(fd):
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(soak.os, "replace", tracked_replace)
    monkeypatch.setattr(soak.os, "fsync", tracked_fsync)
    writer = soak.ArtifactWriter(tmp_path, "stage-04")
    writer.append(
        {
            "run_id": "stage-04",
            "credential_fingerprint": "gemini:0123456789abcdef",
            "nested": {
                "GEMINI_API_KEY": "secret",
                "DATABASE_URL": "postgresql://secret",
            },
        }
    )
    writer.append({"run_id": "stage-04", "sequence": 2})
    writer.finish({"run_id": "stage-04", "verdict": "pass"})

    samples_path = tmp_path / "stage-04.samples.jsonl"
    assert len(samples_path.read_text().splitlines()) == 2
    assert not (tmp_path / "stage-04.summary.json.tmp").exists()
    all_text = samples_path.read_text() + (tmp_path / "stage-04.summary.json").read_text()
    assert "GEMINI_API_KEY" not in all_text
    assert "DATABASE_URL" not in all_text
    assert "gemini:0123456789abcdef" in all_text
    assert replace_calls == [
        (
            tmp_path / "stage-04.summary.json.tmp",
            tmp_path / "stage-04.summary.json",
        )
    ]
    assert len(fsync_calls) == 3
