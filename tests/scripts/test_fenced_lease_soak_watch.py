from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.services import flows
from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_contracts import valid_scope_dict
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


class StageWriteStore:
    def __init__(self):
        self.fleet_pause_reason: str | None = None
        self.batch_reasons: dict[UUID, str] = {}
        self.job_updates: list[object] = []

    async def pause_exact_scope(
        self,
        scope: soak.SoakScope,
        *,
        stop_reason: str,
        staging_reason: str,
        trigger_code: str,
    ) -> soak.StopReceipt:
        plan = soak._plan_stop_mutation(
            scope,
            fleet_reason=self.fleet_pause_reason,
            batch_reasons={
                batch_id: self.batch_reasons.get(batch_id)
                for batch_id in scope.batch_ids
            },
            stop_reason=stop_reason,
            staging_reason=staging_reason,
        )
        if plan.set_fleet_pause:
            self.fleet_pause_reason = stop_reason
        for batch_id in plan.batch_ids_to_pause:
            self.batch_reasons[batch_id] = stop_reason
        return soak.StopReceipt(
            run_id=scope.run_id,
            observed_at=NOW,
            trigger_code=trigger_code,
            paused_batch_ids=plan.paused_batch_ids,
            foreign_batch_pause_ids=plan.foreign_batch_pause_ids,
            fleet_pause_set=plan.fleet_pause_set,
            foreign_fleet_pause_preserved=plan.foreign_fleet_pause_preserved,
            batches_paused=len(plan.paused_batch_ids),
            cancelled_jobs=0,
        )


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


def _stage_uuid(prefix: int, index: int) -> UUID:
    return UUID(f"{prefix:08x}-0000-0000-0000-{index:012d}")


def full_stage_scope(*, target: int, batches: int, workers: int) -> soak.SoakScope:
    raw = valid_scope_dict()
    batch_ids = [_stage_uuid(0xB, index) for index in range(1, batches + 1)]
    book_ids = [_stage_uuid(0xC, index) for index in range(1, batches + 1)]
    job_ids = [_stage_uuid(0xD, index) for index in range(1, target + 1)]
    raw.update(
        {
            "run_id": f"stage-{target:02d}",
            "since": (NOW - timedelta(minutes=3)).isoformat(),
            "batch_ids": [str(value) for value in batch_ids],
            "job_ids": [str(value) for value in job_ids],
            "participant_hosts": [
                f"Host-{index:02d}" for index in range(1, workers + 1)
            ],
            "target_running": target,
            "required_book_sha256": {
                str(book_id): f"{index:x}" * 64
                for index, book_id in enumerate(book_ids, start=1)
            },
            "forbidden_notion_mapping_keys": ["english|8"],
            "approved_incremental_cost_usd": "1.00",
            "settle_seconds": 2,
        }
    )
    return soak.SoakScope.model_validate(raw)


def full_stage_attestation(scope: soak.SoakScope) -> soak.FleetAttestation:
    scope_sha = soak.sha256_canonical(scope)
    workers = [
        soak.WorkerAttestation(
            scope_sha256=scope_sha,
            pc_id=f"{hostname}:{100 + index}@fedcba9",
            hostname=hostname,
            observed_at=NOW - timedelta(seconds=5),
            git_sha="fedcba9",
            code_version=1001,
            worker_concurrency=2,
            agent_max_concurrency=4,
            credential_max_concurrent_gemini=32,
            credential_slot_wait_seconds=120,
            gemini_max_concurrency_present=False,
            structured_output_enabled=False,
            process_count_for_host=1,
            credential_fingerprint="gemini:0123456789abcdef",
            pdf_sha256_by_book=dict(scope.required_book_sha256),
            notion_mapping_keys=[],
        )
        for index, hostname in enumerate(scope.participant_hosts)
    ]
    return soak.FleetAttestation(
        scope_sha256=scope_sha,
        observed_at=NOW - timedelta(seconds=5),
        credential_fingerprint="gemini:0123456789abcdef",
        input_artifact_sha256=[soak.sha256_canonical(worker) for worker in workers],
        workers=workers,
    )


def full_stage_snapshot(scope: soak.SoakScope, *, state: str) -> soak.RawSnapshot:
    book_ids = [UUID(value) for value in scope.required_book_sha256]
    jobs: list[soak.JobSnapshot] = []
    phases: list[soak.PhaseSnapshot] = []
    events: list[soak.LeaseEventSnapshot] = []
    usages: list[soak.UsageSnapshot] = []
    for index, job_id in enumerate(scope.job_ids):
        batch_index = min(
            index * len(scope.batch_ids) // len(scope.job_ids),
            len(scope.batch_ids) - 1,
        )
        batch_id = scope.batch_ids[batch_index]
        book_id = book_ids[batch_index]
        token = _stage_uuid(0xE, index + 1) if state != "pending" else None
        host_index = index % len(scope.participant_hosts)
        owner = (
            f"{scope.participant_hosts[host_index]}:{100 + host_index}@fedcba9"
            if state != "pending"
            else None
        )
        job = soak.JobSnapshot(
            id=job_id,
            batch_id=batch_id,
            book_id=book_id,
            batch_book_id=book_id,
            subject="matematika",
            selected_phases=None,
            status=state,
            attempts=0 if state == "pending" else 1,
            claim_token=token,
            claimed_by=owner,
            created_at=NOW - timedelta(minutes=2),
            notion_skip_reason=(
                "mapping intentionally absent" if state == "done" else None
            ),
            phase_count=12 if state == "done" else 0,
            usage_count=1 if state == "done" else 0,
            lease_count=2 if state == "done" else (1 if state == "running" else 0),
        )
        jobs.append(job)
        if token is None:
            continue
        events.append(
            soak.LeaseEventSnapshot(
                job_id=job_id,
                claim_token=token,
                event_type="claimed",
                owner=owner,
                created_at=NOW - timedelta(minutes=1),
            )
        )
        if state != "done":
            continue
        events.append(
            soak.LeaseEventSnapshot(
                job_id=job_id,
                claim_token=token,
                event_type="released_done",
                owner=None,
                created_at=NOW - timedelta(seconds=10),
            )
        )
        phase_names = ["extract", *flows.flow_for("matematika")]
        phases.extend(
            soak.PhaseSnapshot(
                job_id=job_id,
                phase_name=phase_name,
                phase_order=phase_order,
                status="done",
                claim_token=token,
                judge_status="ok" if phase_name != "extract" else None,
                solver_status="ok" if phase_name == "practice-rlc" else None,
            )
            for phase_order, phase_name in enumerate(phase_names)
        )
        usages.append(usage_row(job_id=job_id))

    return soak.RawSnapshot(
        observed_at=NOW,
        transaction_read_only="on",
        schema=soak.SchemaSnapshot(
            revision="0052_job_lease_fencing",
            ledger_table=True,
            job_claim_token=True,
            phase_claim_token=True,
        ),
        budget=soak.BudgetSnapshot(
            api_paused_reason=(
                f"lease-soak-staging:{scope.run_id}" if state == "pending" else None
            ),
            min_worker_version=1001,
        ),
        jobs=jobs,
        books={
            str(book_id): soak.BookSnapshot(
                id=book_id,
                content_sha256=scope.required_book_sha256[str(book_id)],
            )
            for book_id in book_ids
        },
        unrelated_active_jobs=[],
        workers=[
            soak.RegistryWorkerSnapshot(
                pc_id=worker.pc_id,
                hostname=worker.hostname,
                last_heartbeat=NOW - timedelta(seconds=5),
                status="online",
                git_sha="fedcba9",
                code_version=1001,
                can_gemini_api=True,
            )
            for worker in full_stage_attestation(scope).workers
        ],
        scrub_tombstones=[],
        db=soak.DatabaseSnapshot(
            total_connections=20 + len(scope.participant_hosts),
            max_connections=100,
            superuser_reserved_connections=3,
            idle_in_transaction_timeout_ms=300_000,
            idle_in_transaction=[],
            server_waits=[],
        ),
        credential_slots=[],
        lease_events=events,
        phases=phases,
        usages=usages,
        fleet_usages_24h=[],
    )


def write_stage_inputs(tmp_path, scope: soak.SoakScope) -> tuple[str, str]:
    scope_path = tmp_path / "scope.json"
    attestation_path = tmp_path / "attestation.json"
    scope_path.write_text(soak.canonical_json(scope), encoding="utf-8")
    attestation_path.write_text(
        soak.canonical_json(full_stage_attestation(scope)), encoding="utf-8"
    )
    return str(scope_path), str(attestation_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "jobs", "batches", "workers"),
    [
        (4, 4, 1, 2),
        (8, 8, 1, 4),
        (12, 12, 1, 6),
        (20, 20, 1, 10),
        (40, 40, 2, 20),
    ],
)
async def test_full_synthetic_stage_reaches_target_and_settles_cleanly(
    tmp_path, target, jobs, batches, workers
):
    scope = full_stage_scope(target=target, batches=batches, workers=workers)
    assert len(scope.job_ids) == jobs
    assert len(scope.batch_ids) == batches
    assert len(scope.participant_hosts) == workers
    scope_path, attestation_path = write_stage_inputs(tmp_path, scope)
    completed = full_stage_snapshot(scope, state="done")
    store = FakeStore(
        [
            full_stage_snapshot(scope, state="pending"),
            full_stage_snapshot(scope, state="running"),
            completed,
            completed,
            completed,
        ]
    )
    clock = FakeClock()
    stdout = io.StringIO()

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            scope_path,
            "--attestation",
            attestation_path,
            "--artifact-dir",
            str(tmp_path),
            "--interval-seconds",
            "1",
        ],
        store_factory=lambda _: store,
        database_url="unused",
        clock=clock,
        sleep=FakeSleep(clock),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == soak.ExitCode.PASS
    assert stdout.getvalue() == "READY_TO_RELEASE\n"
    summary = load_summary(tmp_path, scope.run_id)
    assert summary["verdict"] == "pass"
    assert summary["peaks"]["running_jobs"] == target
    assert summary["lease_events"] == {
        "claimed": jobs,
        "released_done": jobs,
    }


@pytest.mark.asyncio
async def test_armed_cli_records_foreign_token_then_pauses_only_exact_scope(tmp_path):
    scope = full_stage_scope(target=4, batches=2, workers=2)
    scope_path, attestation_path = write_stage_inputs(tmp_path, scope)
    violating = full_stage_snapshot(scope, state="done")
    violating.phases[0].claim_token = UUID(
        "ffffffff-ffff-ffff-ffff-ffffffffffff"
    )
    store = FakeStore(
        [
            full_stage_snapshot(scope, state="pending"),
            full_stage_snapshot(scope, state="running"),
            violating,
        ]
    )
    write_store = StageWriteStore()
    statuses_before = [job.status for job in violating.jobs]
    clock = FakeClock()

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            scope_path,
            "--attestation",
            attestation_path,
            "--artifact-dir",
            str(tmp_path),
            "--interval-seconds",
            "1",
            "--arm-stop",
            "--confirm-arm",
            f"lease-soak-stop:{scope.run_id}",
        ],
        store_factory=lambda _: store,
        write_store_factory=lambda _: write_store,
        database_url="unused",
        clock=clock,
        sleep=FakeSleep(clock),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == soak.ExitCode.HARD_STOP_ARMED
    assert write_store.fleet_pause_reason == f"lease-soak-stop:{scope.run_id}"
    assert write_store.batch_reasons == {
        batch_id: f"lease-soak-stop:{scope.run_id}"
        for batch_id in scope.batch_ids
    }
    assert write_store.job_updates == []
    assert [job.status for job in violating.jobs] == statuses_before
    samples = [
        json.loads(line)
        for line in (tmp_path / f"{scope.run_id}.samples.jsonl")
        .read_text()
        .splitlines()
    ]
    assert samples[-1]["findings"][0]["code"] == "phase_token_mismatch"
    summary = load_summary(tmp_path, scope.run_id)
    assert summary["verdict"] == "hard_stop"
    assert summary["exit_code"] == 4
    assert summary["stop_receipt"]["batches_paused"] == 2
    assert summary["stop_receipt"]["cancelled_jobs"] == 0


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
