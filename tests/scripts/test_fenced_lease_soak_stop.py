from __future__ import annotations

import io
import inspect
import re
from datetime import datetime, timezone
from uuid import UUID

import pytest

from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_snapshot import (
    runtime_attestation,
    valid_scope,
)
from tests.scripts.test_fenced_lease_soak_watch import (
    FakeClock,
    FakeSleep,
    FakeStore,
    hard_runtime_snapshot,
    pristine_staged_snapshot,
)


B1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
B2 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")
NOW = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)


def lease_lost_finding() -> soak.Finding:
    return soak.Finding(
        code="lease_lost",
        hard=True,
        hard_stop=True,
        stage_failure=True,
        message="lease lost",
    )


class FakeWriteStore:
    def __init__(
        self,
        *,
        fleet_reason: str | None = None,
        batch_reasons: dict[UUID, str | None] | None = None,
        events: list[str] | None = None,
    ):
        self.fleet_pause_reason = fleet_reason
        self.batch_reasons = dict(batch_reasons or {})
        self.events = events
        self.job_updates: list[object] = []

    async def pause_exact_scope(
        self,
        scope: soak.SoakScope,
        *,
        stop_reason: str,
        staging_reason: str,
        trigger_code: str,
    ) -> soak.StopReceipt:
        if self.events is not None:
            self.events.append("stop")
        current_batch_reasons = {
            batch_id: self.batch_reasons.get(batch_id)
            for batch_id in scope.batch_ids
        }
        plan = soak._plan_stop_mutation(
            scope,
            fleet_reason=self.fleet_pause_reason,
            batch_reasons=current_batch_reasons,
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


@pytest.mark.asyncio
async def test_armed_stop_pauses_exact_batches_and_fleet_but_never_jobs():
    writer = FakeWriteStore()
    scope = valid_scope(target=40, batch_ids=[B1, B2])

    receipt = await soak.GuardedStopper(writer, clock=lambda: NOW).pause(
        scope, lease_lost_finding()
    )

    assert writer.batch_reasons == {
        B1: f"lease-soak-stop:{scope.run_id}",
        B2: f"lease-soak-stop:{scope.run_id}",
    }
    assert writer.fleet_pause_reason == f"lease-soak-stop:{scope.run_id}"
    assert writer.job_updates == []
    assert receipt.cancelled_jobs == 0
    assert receipt.batches_paused == 2


@pytest.mark.asyncio
async def test_foreign_fleet_and_batch_pauses_are_preserved():
    writer = FakeWriteStore(
        fleet_reason="manual-operator",
        batch_reasons={B1: "manual", B2: None},
    )
    scope = valid_scope(target=40, batch_ids=[B1, B2])

    receipt = await soak.GuardedStopper(writer, clock=lambda: NOW).pause(
        scope, lease_lost_finding()
    )

    assert writer.fleet_pause_reason == "manual-operator"
    assert writer.batch_reasons[B1] == "manual"
    assert writer.batch_reasons[B2] == f"lease-soak-stop:{scope.run_id}"
    assert receipt.foreign_fleet_pause_preserved is True
    assert receipt.foreign_batch_pause_ids == [B1]


@pytest.mark.asyncio
async def test_staging_fleet_pause_is_replaced_by_stop_reason():
    scope = valid_scope(batch_ids=[B1])
    writer = FakeWriteStore(
        fleet_reason=f"lease-soak-staging:{scope.run_id}",
        batch_reasons={B1: None},
    )

    receipt = await soak.GuardedStopper(writer, clock=lambda: NOW).pause(
        scope, lease_lost_finding()
    )

    assert writer.fleet_pause_reason == f"lease-soak-stop:{scope.run_id}"
    assert receipt.fleet_pause_set is True


@pytest.mark.asyncio
async def test_stopper_rejects_non_hard_trigger_before_store_call():
    writer = FakeWriteStore()
    trigger = soak.Finding(code="quality", hard=False, message="quality")

    with pytest.raises(ValueError, match="hard-stop trigger"):
        await soak.GuardedStopper(writer, clock=lambda: NOW).pause(
            valid_scope(batch_ids=[B1]), trigger
        )

    assert writer.batch_reasons == {}


@pytest.mark.asyncio
async def test_task5_records_offending_sample_before_armed_stop_action(tmp_path):
    events: list[str] = []
    scope = valid_scope(target=4)
    store = FakeStore(
        [pristine_staged_snapshot(scope), hard_runtime_snapshot(scope)]
    )
    write_store = FakeWriteStore(events=events)

    class RecordingWriter:
        def append(self, sample):
            del sample
            events.append("sample")

        def finish(self, summary):
            del summary
            events.append("summary")

    clock = FakeClock()
    code = await soak.run_watch(
        scope=scope,
        attestation=runtime_attestation(scope),
        store=store,
        writer=RecordingWriter(),
        stopper=soak.GuardedStopper(write_store, clock=clock),
        clock=clock,
        sleep=FakeSleep(clock),
    )

    assert code == soak.ExitCode.HARD_STOP_ARMED
    assert events == ["sample", "sample", "stop", "summary"]


@pytest.mark.asyncio
async def test_unarmed_watch_can_never_construct_sql_stopper(tmp_path):
    scope = valid_scope(target=4)
    scope_path = tmp_path / "scope.json"
    attestation_path = tmp_path / "attestation.json"
    scope_path.write_text(soak.canonical_json(scope), encoding="utf-8")
    attestation_path.write_text(
        soak.canonical_json(runtime_attestation(scope)), encoding="utf-8"
    )
    read_store = FakeStore(
        [pristine_staged_snapshot(scope), hard_runtime_snapshot(scope)]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("unarmed path constructed a SQL writer")

    code = await soak.async_main(
        [
            "watch",
            "--scope",
            str(scope_path),
            "--attestation",
            str(attestation_path),
            "--artifact-dir",
            str(tmp_path),
        ],
        store_factory=lambda _: read_store,
        write_store_factory=forbidden,
        database_url="unused",
        clock=lambda: NOW,
        sleep=lambda _: FakeSleep(FakeClock())(0),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == soak.ExitCode.HARD_STOP_READ_ONLY


def test_sql_stop_writer_contains_no_job_mutation_or_queue_helper_calls():
    source = inspect.getsource(soak.SqlSoakWriteStore.pause_exact_scope)
    assert re.search(r"\b(?:update|delete\s+from)\s+homework_jobs\b", source, re.I) is None
    module_source = inspect.getsource(soak)
    for forbidden in (
        "cancel_all_in_batch",
        "retry_job",
        "resume_failed_in_batch",
        "clear_api_paused",
        "unpause_batch",
        "unpause_by_reason",
    ):
        assert forbidden not in module_source


def test_sql_stop_writer_bounds_lock_and_statement_waits(monkeypatch):
    captured = {}

    class FakeEngine:
        async def dispose(self):
            return None

    def fake_create_async_engine(database_url, **kwargs):
        captured["database_url"] = database_url
        captured.update(kwargs)
        return FakeEngine()

    monkeypatch.setattr(soak, "create_async_engine", fake_create_async_engine)

    soak.SqlSoakWriteStore("postgresql+asyncpg://scratch/soak")

    settings = captured["connect_args"]["server_settings"]
    assert 0 < int(settings["lock_timeout"]) <= 5_000
    assert 0 < int(settings["statement_timeout"]) <= 30_000
    assert int(settings["statement_timeout"]) < int(
        soak._ARMED_STOP_TIMEOUT_SECONDS * 1_000
    )
