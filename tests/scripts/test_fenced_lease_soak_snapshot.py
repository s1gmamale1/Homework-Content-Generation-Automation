from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

import pytest

from app.services import flows
from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_contracts import BATCH, BOOK, valid_scope_dict


NOW = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
JOBS = [UUID(f"33333333-3333-3333-3333-{index:012d}") for index in range(1, 5)]


def valid_scope(*, target: int = 4, **updates) -> soak.SoakScope:
    raw = valid_scope_dict()
    job_ids = [
        UUID(f"33333333-3333-3333-3333-{index:012d}")
        for index in range(1, target + 1)
    ]
    raw.update(
        {
            "job_ids": [str(job_id) for job_id in job_ids],
            "participant_hosts": [f"Host-{index:02d}" for index in range(1, 3)],
            "target_running": target,
            "expected_models_by_operation_prefix": {
                "phase.run": "gemini-3.6-flash",
                "lesson.extract": "gemini-3.5-flash-lite",
                "lesson.extract.coverage": "gemini-3.5-flash",
                "lesson.extract.verify": "gemini-3.5-flash-lite",
                "judge:": "gemini-3.5-flash",
                "solve:": "gemini-3.1-pro-preview",
            },
            "approved_incremental_cost_usd": "1.00",
            **updates,
        }
    )
    return soak.SoakScope.model_validate(raw)


def job_row(
    job_id: UUID,
    *,
    status: str = "done",
    token: UUID | None = None,
    owner: str = "Host-01:100@fedcba9",
) -> soak.JobSnapshot:
    return soak.JobSnapshot(
        id=job_id,
        batch_id=BATCH,
        book_id=BOOK,
        batch_book_id=BOOK,
        subject="matematika",
        selected_phases=None,
        output_language="en",
        custom_prompts_present=False,
        provider="gemini",
        model="gemini-3.6-flash",
        transport="api",
        extract_provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        extract_transport="api",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        judge_transport="api",
        solver_provider="gemini",
        solver_model="gemini-3.1-pro-preview",
        solver_transport="api",
        status=status,
        attempts=1 if status != "pending" else 0,
        claim_token=token,
        claimed_by=owner if status != "pending" else None,
        created_at=NOW - timedelta(minutes=3),
        notion_archived_at=None,
        notion_skip_reason="mapping intentionally absent" if status == "done" else None,
    )


def worker_row(index: int, *, heartbeat_age: int = 5) -> soak.RegistryWorkerSnapshot:
    return soak.RegistryWorkerSnapshot(
        pc_id=f"Host-{index:02d}:{99 + index}@fedcba9",
        hostname=f"Host-{index:02d}",
        last_heartbeat=NOW - timedelta(seconds=heartbeat_age),
        status="online",
        git_sha="fedcba9",
        code_version=1001,
        can_gemini_api=True,
    )


def phase_rows(job: soak.JobSnapshot) -> list[soak.PhaseSnapshot]:
    names = ["extract", *flows.flow_for(job.subject or "matematika")]
    return [
        soak.PhaseSnapshot(
            job_id=job.id,
            phase_name=name,
            phase_order=order,
            status="done",
            claim_token=job.claim_token,
            judge_status="ok" if name != "extract" else None,
            solver_status=(
                "ok"
                if name
                in {
                    "memory-check",
                    "practice-error-detection",
                    "practice-rlc",
                    "boss-arena",
                }
                else None
            ),
        )
        for order, name in enumerate(names)
    ]


def usage_row(
    *,
    job_id: UUID = JOBS[0],
    operation: str = "phase.run",
    model_name: str = "gemini-3.6-flash",
    success: bool = True,
    total_tokens: int = 1_100,
    error_message: str | None = None,
    provider: str = "gemini",
    auth_mode: str = "api",
    phase_name: str | None = None,
    phase_job_id: UUID | None = None,
    prompt_tokens: int | None = None,
    output_tokens: int | None = None,
) -> soak.UsageSnapshot:
    if phase_name is None:
        if operation.startswith("lesson.extract"):
            phase_name = "extract"
        elif operation.startswith(("judge:", "solve:")):
            phase_name = operation.split(":", 1)[1]
        else:
            phase_name = "flashcards"
    return soak.UsageSnapshot(
        job_id=job_id,
        phase_output_id=uuid5(job_id, phase_name),
        phase_job_id=phase_job_id or job_id,
        phase_name=phase_name,
        provider=provider,
        operation=operation,
        model_name=model_name,
        auth_mode=auth_mode,
        prompt_tokens=(
            (1_000 if total_tokens else 0)
            if prompt_tokens is None
            else prompt_tokens
        ),
        output_tokens=(
            (100 if total_tokens else 0)
            if output_tokens is None
            else output_tokens
        ),
        cached_tokens=0,
        cache_creation_tokens=0,
        total_tokens=total_tokens,
        success=success,
        error_message=error_message,
    )


def complete_usage_rows(
    job_id: UUID, phases: list[soak.PhaseSnapshot]
) -> list[soak.UsageSnapshot]:
    def required_usage(**kwargs) -> soak.UsageSnapshot:
        return usage_row(
            total_tokens=11,
            prompt_tokens=10,
            output_tokens=1,
            **kwargs,
        )

    rows = [
        required_usage(
            job_id=job_id,
            operation="lesson.extract",
            model_name="gemini-3.5-flash-lite",
            phase_name="extract",
        )
    ]
    solver_phases = {
        "memory-check",
        "practice-error-detection",
        "practice-rlc",
        "boss-arena",
    }
    for phase in phases:
        if phase.phase_name == "extract":
            continue
        rows.extend(
            [
                required_usage(job_id=job_id, phase_name=phase.phase_name),
                required_usage(
                    job_id=job_id,
                    operation=f"judge:{phase.phase_name}",
                    model_name="gemini-3.5-flash",
                    phase_name=phase.phase_name,
                ),
            ]
        )
        if phase.phase_name in solver_phases:
            rows.append(
                required_usage(
                    job_id=job_id,
                    operation=f"solve:{phase.phase_name}",
                    model_name="gemini-3.1-pro-preview",
                    phase_name=phase.phase_name,
                )
            )
    return rows


def test_usage_snapshot_preserves_database_phase_binding() -> None:
    job_id = JOBS[0]
    phase_output_id = UUID("55555555-5555-5555-5555-555555555555")
    row = {
        "job_id": job_id,
        "phase_output_id": phase_output_id,
        "phase_job_id": job_id,
        "phase_name": "flashcards",
        "provider": "gemini",
        "operation": "phase.run",
        "model_name": "gemini-3.6-flash",
        "auth_mode": "api",
        "prompt_tokens": 1_000,
        "output_tokens": 100,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 1_100,
        "success": True,
        "error_message": None,
    }

    dumped = soak._usage_from_row(row).model_dump()

    assert dumped.get("phase_output_id") == phase_output_id
    assert dumped.get("phase_job_id") == job_id
    assert dumped.get("phase_name") == "flashcards"


def test_job_snapshot_exposes_only_custom_prompt_presence() -> None:
    assert "custom_prompts_present" in soak.JobSnapshot.model_fields
    assert "custom_prompts" not in soak.JobSnapshot.model_fields
    dumped = job_row(JOBS[0], status="pending").model_dump(mode="json")
    assert dumped["custom_prompts_present"] is False


def healthy_completed_snapshot(*, target: int = 4) -> soak.RawSnapshot:
    jobs: list[soak.JobSnapshot] = []
    events: list[soak.LeaseEventSnapshot] = []
    phases: list[soak.PhaseSnapshot] = []
    usages: list[soak.UsageSnapshot] = []
    for index, job_id in enumerate(JOBS[:target]):
        token = UUID(f"44444444-4444-4444-4444-{index + 1:012d}")
        owner = f"Host-{index % 2 + 1:02d}:{index % 2 + 100}@fedcba9"
        job = job_row(job_id, token=token, owner=owner)
        jobs.append(job)
        job_phases = phase_rows(job)
        phases.extend(job_phases)
        events.extend(
            [
                soak.LeaseEventSnapshot(
                    job_id=job_id,
                    claim_token=token,
                    event_type="claimed",
                    owner=owner,
                    created_at=NOW - timedelta(minutes=2),
                ),
                soak.LeaseEventSnapshot(
                    job_id=job_id,
                    claim_token=token,
                    event_type="released_done",
                    owner=None,
                    created_at=NOW - timedelta(seconds=10),
                ),
            ]
        )
        job_usages = complete_usage_rows(job_id, job_phases)
        usages.extend(job_usages)
        job.phase_count = len(job_phases)
        job.usage_count = len(job_usages)
        job.lease_count = 2
    return soak.RawSnapshot(
        observed_at=NOW,
        transaction_read_only="on",
        schema=soak.SchemaSnapshot(
            revision="0052_job_lease_fencing",
            ledger_table=True,
            job_claim_token=True,
            phase_claim_token=True,
        ),
        budget=soak.BudgetSnapshot(api_paused_reason=None, min_worker_version=1001),
        launch_defaults=soak.LaunchDefaultsSnapshot(
            solver_boss_arena_enabled=True,
        ),
        jobs=jobs,
        books={
            str(BOOK): soak.BookSnapshot(
                id=BOOK,
                content_sha256="a" * 64,
                subject="matematika",
                source_language="ru",
            )
        },
        unrelated_active_jobs=[],
        workers=[worker_row(1), worker_row(2)],
        scrub_tombstones=[],
        db=soak.DatabaseSnapshot(
            total_connections=30,
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


def healthy_running_snapshot(*, running: int = 4, db_total: int = 30) -> soak.RawSnapshot:
    raw = healthy_completed_snapshot(target=running)
    raw.db.total_connections = db_total
    for job in raw.jobs:
        job.status = "running"
        job.notion_skip_reason = None
        job.lease_count = 1
    raw.phases = []
    raw.lease_events = [event for event in raw.lease_events if event.event_type == "claimed"]
    return raw


def runtime_attestation(scope: soak.SoakScope) -> soak.FleetAttestation:
    scope_sha = soak.sha256_canonical(scope)
    workers = [
        soak.WorkerAttestation(
            scope_sha256=scope_sha,
            pc_id=f"{hostname}:{99 + index}@fedcba9",
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
            solver_enabled=True,
            process_count_for_host=1,
            credential_fingerprint="gemini:0123456789abcdef",
            pdf_sha256_by_book={str(BOOK): "a" * 64},
            notion_mapping_keys=[],
        )
        for index, hostname in enumerate(scope.participant_hosts, start=1)
    ]
    return soak.FleetAttestation(
        scope_sha256=scope_sha,
        observed_at=NOW - timedelta(seconds=5),
        credential_fingerprint="gemini:0123456789abcdef",
        input_artifact_sha256=sorted(
            soak.sha256_canonical(worker) for worker in workers
        ),
        workers=workers,
    )


def runtime_findings(
    scope: soak.SoakScope,
    raw: soak.RawSnapshot,
    previous_samples: list[soak.RawSnapshot],
    *,
    attestation: soak.FleetAttestation | None = None,
) -> list[soak.Finding]:
    return soak.evaluate_runtime(
        scope,
        attestation or runtime_attestation(scope),
        raw,
        previous_samples,
    )


def codes(findings: list[soak.Finding], *, hard_stop: bool | None = None) -> set[str]:
    if hard_stop is None:
        return {finding.code for finding in findings}
    return {
        finding.code
        for finding in findings
        if finding.hard_stop is hard_stop
    }


def by_code(findings: list[soak.Finding], code: str) -> soak.Finding:
    return next(finding for finding in findings if finding.code == code)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("429 RESOURCE_EXHAUSTED", "provider_429"),
        ("fleet credential slot wait exhausted", "slot_exhaustion"),
        ("403 PERMISSION_DENIED invalid API key", "auth"),
        ("per-attempt timeout after 600s", "attempt_timeout"),
        ("connection reset by peer", "network"),
        ("model returned malformed output", "other"),
    ],
)
def test_error_classes_are_stable(message, expected):
    assert soak.classify_error(message).value == expected


def test_blank_error_has_no_classification():
    assert soak.classify_error("  ") is None


def test_price_scoped_usage_uses_production_pricing_and_preserves_tokens():
    priced = soak.price_scoped_usage([usage_row()])
    assert priced.total_usd == Decimal("0.00225")
    assert priced.rows[0].total_tokens == 1_100
    assert priced.rows[0].model_name == "gemini-3.6-flash"


def test_successful_api_usage_must_be_token_bearing_and_priced():
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(model_name="unknown", total_tokens=0)
    findings = runtime_findings(valid_scope(), raw, [])
    assert "unpriced_or_tokenless_usage" in codes(findings, hard_stop=True)


def _required_usage_index(
    raw: soak.RawSnapshot,
    *,
    job_id: UUID,
    operation: str,
) -> int:
    return next(
        index
        for index, usage in enumerate(raw.usages)
        if usage.job_id == job_id and usage.operation == operation
    )


@pytest.mark.parametrize(
    "operation",
    [
        "lesson.extract",
        "phase.run",
        "judge:flashcards",
        "solve:boss-arena",
    ],
)
def test_terminal_job_requires_every_mandatory_usage_class(operation):
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    index = _required_usage_index(raw, job_id=job.id, operation=operation)
    raw.usages.pop(index)
    job.usage_count -= 1

    findings = runtime_findings(valid_scope(), raw, [])

    finding = by_code(findings, "usage_coverage_missing")
    assert finding.hard_stop is True
    assert any(
        str(job.id) in row and operation in row
        for row in finding.evidence["rows"]
    )


def test_usage_count_must_equal_the_authoritative_job_count():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    raw.usages.pop(
        _required_usage_index(raw, job_id=job.id, operation="judge:flashcards")
    )

    findings = runtime_findings(valid_scope(), raw, [])

    finding = by_code(findings, "usage_count_mismatch")
    assert finding.hard_stop is True
    assert str(job.id) in finding.evidence["job_ids"]


def test_required_usage_cannot_be_bound_to_another_job():
    raw = healthy_completed_snapshot()
    first, second = raw.jobs[:2]
    usage = raw.usages[
        _required_usage_index(raw, job_id=first.id, operation="phase.run")
    ]
    usage.phase_job_id = second.id

    findings = runtime_findings(valid_scope(), raw, [])

    assert "usage_phase_binding_mismatch" in codes(findings, hard_stop=True)
    assert "usage_coverage_missing" in codes(findings, hard_stop=True)


def test_required_usage_cannot_be_bound_to_the_wrong_phase():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    usage = raw.usages[
        _required_usage_index(raw, job_id=job.id, operation="judge:flashcards")
    ]
    usage.phase_name = "memory-check"

    findings = runtime_findings(valid_scope(), raw, [])

    assert "usage_phase_binding_mismatch" in codes(findings, hard_stop=True)
    assert "usage_coverage_missing" in codes(findings, hard_stop=True)


def test_phase_run_without_phase_identity_is_a_binding_mismatch():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    usage = raw.usages[
        _required_usage_index(raw, job_id=job.id, operation="phase.run")
    ]
    usage.phase_name = None

    findings = runtime_findings(valid_scope(), raw, [])

    assert "usage_phase_binding_mismatch" in codes(findings, hard_stop=True)


def test_wrong_model_row_does_not_satisfy_required_usage_coverage():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    usage = raw.usages[
        _required_usage_index(raw, job_id=job.id, operation="solve:boss-arena")
    ]
    usage.model_name = "gemini-3.6-flash"

    findings = runtime_findings(valid_scope(), raw, [])

    assert "operation_model_mismatch" in codes(findings, hard_stop=True)
    assert "usage_coverage_missing" in codes(findings, hard_stop=True)


def test_multiple_regeneration_rows_may_satisfy_one_required_operation():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    usage = raw.usages[
        _required_usage_index(raw, job_id=job.id, operation="phase.run")
    ]
    raw.usages.append(usage.model_copy(deep=True))
    job.usage_count += 1

    findings = runtime_findings(valid_scope(), raw, [])

    assert "usage_count_mismatch" not in codes(findings, hard_stop=True)
    assert "usage_coverage_missing" not in codes(findings, hard_stop=True)


def test_model_routing_must_match_the_operation_contract():
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(operation="judge:flashcards", model_name="gemini-3.6-flash")
    findings = runtime_findings(valid_scope(), raw, [])
    assert "operation_model_mismatch" in codes(findings, hard_stop=True)


@pytest.mark.parametrize(
    ("operation", "model_name"),
    [
        ("lesson.extract.coverage", "gemini-3.5-flash"),
        ("lesson.extract.verify", "gemini-3.5-flash-lite"),
    ],
)
def test_auxiliary_extract_operations_use_the_configured_extract_model(
    operation, model_name
):
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(
        operation=operation,
        model_name=model_name,
    )
    hard = codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)
    assert "operation_model_mismatch" not in hard


@pytest.mark.parametrize(
    "operation",
    ["lesson.extract.coverage", "lesson.extract.verify"],
)
def test_auxiliary_extract_operations_reject_any_other_model(operation):
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(operation=operation, model_name="gemini-3.6-flash")
    assert "operation_model_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_unknown_future_extract_operation_remains_fail_closed():
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(
        operation="lesson.extract.future",
        model_name="gemini-3.5-flash-lite",
    )
    assert "operation_model_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_failed_usage_without_error_text_is_still_a_hard_stop():
    raw = healthy_running_snapshot()
    raw.usages.append(usage_row(success=False, total_tokens=0, error_message=None))
    assert "provider_or_auth_error" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


@pytest.mark.parametrize(
    ("provider", "auth_mode"),
    [("claude", "api"), ("gemini", "cli")],
)
def test_scoped_usage_requires_gemini_api_transport(provider, auth_mode):
    raw = healthy_completed_snapshot()
    raw.usages[0] = usage_row(provider=provider, auth_mode=auth_mode)
    assert "operation_transport_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("lease_lost", "lease_lost"),
        ("reclaimed_stale", "job_reclaimed"),
        ("reclaimed_forced", "job_reclaimed"),
        ("released_retry", "job_retried_or_failed"),
        ("released_failed", "job_retried_or_failed"),
        ("released_cancelled", "job_retried_or_failed"),
    ],
)
def test_bad_lease_events_are_hard_stops(event_type, expected):
    raw = healthy_completed_snapshot()
    raw.lease_events.append(
        soak.LeaseEventSnapshot(
            job_id=raw.jobs[0].id,
            claim_token=raw.jobs[0].claim_token,
            event_type=event_type,
            created_at=NOW,
        )
    )
    assert expected in codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)


def test_claim_and_release_must_each_exist_once_for_retained_token():
    raw = healthy_completed_snapshot()
    raw.lease_events = [
        event
        for event in raw.lease_events
        if not (event.job_id == raw.jobs[0].id and event.event_type == "released_done")
    ]
    assert "claim_event_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_lease_ledger_rejects_an_event_for_a_foreign_token():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    foreign_token = uuid4()
    raw.lease_events.append(
        soak.LeaseEventSnapshot(
            job_id=job.id,
            claim_token=foreign_token,
            event_type="claimed",
            owner=job.claimed_by,
            created_at=NOW,
        )
    )
    job.lease_count += 1

    findings = runtime_findings(valid_scope(), raw, [])

    assert "lease_event_token_mismatch" in codes(findings, hard_stop=True)
    assert str(foreign_token) not in soak.canonical_json(
        by_code(findings, "lease_event_token_mismatch").evidence
    )


def test_lease_ledger_count_must_equal_authoritative_job_count():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    raw.lease_events = [
        event
        for event in raw.lease_events
        if not (event.job_id == job.id and event.event_type == "released_done")
    ]

    findings = runtime_findings(valid_scope(), raw, [])

    finding = by_code(findings, "lease_count_mismatch")
    assert finding.hard_stop is True
    assert str(job.id) in finding.evidence["job_ids"]


def test_lease_ledger_rejects_unknown_same_token_auxiliary_event():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    raw.lease_events.append(
        soak.LeaseEventSnapshot(
            job_id=job.id,
            claim_token=job.claim_token,
            event_type="heartbeat",
            owner=job.claimed_by,
            created_at=NOW,
        )
    )
    job.lease_count += 1

    assert "lease_event_unexpected" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_claim_owner_must_match_job_and_deployed_identity():
    raw = healthy_completed_snapshot()
    raw.jobs[0].claimed_by = "Host-01:100@deadbee"
    assert "claim_event_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_completed_stage_must_reach_target_and_use_enough_claim_owners():
    raw = healthy_completed_snapshot()
    for event in raw.lease_events:
        if event.event_type == "claimed":
            event.owner = "Host-01:100@fedcba9"
    findings = runtime_findings(valid_scope(), raw, [healthy_running_snapshot()])
    assert "claim_owner_underdistributed" in codes(findings, hard_stop=True)


def test_running_or_terminal_claimed_job_must_retain_token():
    raw = healthy_running_snapshot()
    raw.jobs[0].claim_token = None
    assert "running_without_token" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_retry_or_terminal_failure_is_a_hard_stop():
    raw = healthy_running_snapshot()
    raw.jobs[0].attempts = 2
    raw.jobs[0].status = "failed"
    assert "job_retried_or_failed" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_old_claim_cannot_leave_a_phase_with_foreign_token():
    raw = healthy_completed_snapshot()
    raw.phases[0].claim_token = uuid4()
    findings = runtime_findings(valid_scope(), raw, [])
    assert "phase_token_mismatch" in codes(findings, hard_stop=True)


def test_duplicate_phase_name_is_a_hard_stop():
    raw = healthy_completed_snapshot()
    raw.phases.append(raw.phases[0].model_copy(deep=True))
    assert "duplicate_phase" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_phase_for_job_outside_scope_is_orphaned():
    raw = healthy_completed_snapshot()
    raw.phases[0].job_id = uuid4()
    assert "orphan_phase" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_terminal_job_requires_exact_done_phase_set():
    raw = healthy_completed_snapshot()
    raw.phases = [
        phase
        for phase in raw.phases
        if not (phase.job_id == raw.jobs[0].id and phase.phase_name == "reflection")
    ]
    assert "phase_set_incomplete" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_partial_selected_phase_job_is_rejected():
    raw = healthy_completed_snapshot(target=4)
    raw.jobs[0].selected_phases = ["flashcards"]
    raw.phases = [
        phase
        for phase in raw.phases
        if phase.job_id != raw.jobs[0].id
        or phase.phase_name in {"extract", "flashcards"}
    ]
    hard = codes(runtime_findings(valid_scope(target=4), raw, []), hard_stop=True)
    assert "job_not_full_homework" in hard
    assert "phase_set_incomplete" in hard


@pytest.mark.parametrize("selected_phases", [[], ["flashcards"]])
def test_runtime_requires_null_or_exact_complete_flow(selected_phases):
    raw = healthy_completed_snapshot()
    raw.jobs[0].selected_phases = selected_phases

    assert "job_not_full_homework" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_runtime_accepts_exact_complete_selected_flow():
    raw = healthy_completed_snapshot()
    raw.jobs[0].selected_phases = flows.flow_for(raw.jobs[0].subject or "")

    assert "job_not_full_homework" not in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("output_language", "ru"), ("custom_prompts_present", True)],
)
def test_runtime_rejects_wrong_language_or_custom_prompts(field, value):
    raw = healthy_completed_snapshot()
    setattr(raw.jobs[0], field, value)

    assert "job_workload_contract_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_runtime_rejects_wrong_book_source_language():
    raw = healthy_completed_snapshot()
    raw.books[str(BOOK)].source_language = "uz"

    assert "job_workload_contract_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_heartbeat_requires_two_consecutive_stale_samples():
    first = healthy_running_snapshot()
    first.workers[0].last_heartbeat = NOW - timedelta(seconds=91)
    one = runtime_findings(valid_scope(), first, [])
    assert "heartbeat_stale" not in codes(one, hard_stop=True)

    second = healthy_running_snapshot()
    second.observed_at = NOW + timedelta(seconds=2)
    second.workers[0].last_heartbeat = NOW - timedelta(seconds=91)
    two = runtime_findings(valid_scope(), second, [first])
    assert "heartbeat_stale" in codes(two, hard_stop=True)


def test_participant_registry_identity_cannot_drift_mid_soak():
    raw = healthy_running_snapshot()
    raw.workers[0].git_sha = "deadbee"
    raw.workers[0].code_version = 999
    assert "worker_runtime_drift" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_runtime_pc_id_must_match_the_preflight_attestation():
    scope = valid_scope()
    raw = healthy_running_snapshot()
    raw.workers[0].pc_id = "Host-01:999@fedcba9"
    assert "worker_runtime_drift" in codes(
        runtime_findings(scope, raw, [], attestation=runtime_attestation(scope)),
        hard_stop=True,
    )


def test_runtime_rejects_newly_claimable_worker_outside_attestation():
    scope = valid_scope()
    raw = healthy_running_snapshot()
    raw.workers.append(
        soak.RegistryWorkerSnapshot(
            pc_id="Host-99:999@fedcba9",
            hostname="Host-99",
            last_heartbeat=NOW - timedelta(seconds=1),
            status="online",
            git_sha="fedcba9",
            code_version=1001,
            can_gemini_api=True,
        )
    )

    findings = runtime_findings(
        scope, raw, [], attestation=runtime_attestation(scope)
    )

    assert "unattested_claimable_worker" in codes(findings, hard_stop=True)


def test_transient_unrelated_terminal_activity_since_start_is_a_hard_stop():
    scope = valid_scope()
    raw = healthy_running_snapshot()
    foreign_id = uuid4()
    encoded = raw.model_dump(mode="json", by_alias=True)
    encoded["unrelated_job_transitions"] = [
        {
            "id": str(foreign_id),
            "status": "failed",
            "attempts": 1,
            "updated_at": NOW.isoformat(),
        }
    ]
    encoded["unrelated_lease_events"] = [
        {
            "job_id": str(foreign_id),
            "claim_token": str(uuid4()),
            "event_type": "released_failed",
            "owner": "Host-99:999@fedcba9",
            "created_at": NOW.isoformat(),
        }
    ]
    raw_with_history = soak.RawSnapshot.model_validate(encoded)

    findings = runtime_findings(scope, raw_with_history, [])

    assert "unrelated_queue_activity_since_start" in codes(
        findings, hard_stop=True
    )


def test_two_high_db_samples_are_hard_but_one_is_not():
    scope = valid_scope(db_hard_stop_connection_limit=85)
    first = healthy_running_snapshot(db_total=85)
    one = runtime_findings(scope, first, [])
    assert "db_connection_hard_stop" not in codes(one, hard_stop=True)
    second = healthy_running_snapshot(db_total=86)
    two = runtime_findings(scope, second, [first])
    assert "db_connection_hard_stop" in codes(two, hard_stop=True)


def test_idle_transaction_and_server_wait_are_immediate_hard_stops():
    raw = healthy_running_snapshot()
    raw.db.idle_in_transaction.append({"pid": 7, "age_s": 2})
    raw.db.server_waits.append({"pid": 8, "wait_event_type": "Lock"})
    hard = codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)
    assert {"db_idle_in_transaction", "db_server_wait"} <= hard


def test_active_slots_cannot_exceed_credential_limit():
    raw = healthy_running_snapshot()
    raw.credential_slots = [
        soak.CredentialSlotSnapshot(
            credential="gemini:0123456789abcdef",
            pc_id="Host-01:100",
            acquired_at=NOW,
            slot_count=33,
        )
    ]
    assert "credential_slot_exhausted" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


@pytest.mark.parametrize(
    "message",
    [
        "429 fleet credential slot wait exhausted",
        "429 RESOURCE_EXHAUSTED",
        "403 PERMISSION_DENIED invalid API key",
        "per-attempt timeout after 600s",
        "connection reset by peer",
    ],
)
def test_provider_slot_auth_timeout_and_network_errors_stop_the_soak(message):
    raw = healthy_running_snapshot()
    raw.usages.append(usage_row(success=False, total_tokens=0, error_message=message))
    hard = codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)
    expected = (
        "credential_slot_exhausted"
        if "slot wait exhausted" in message
        else "provider_or_auth_error"
    )
    assert expected in hard


def test_incremental_cost_cap_is_inclusive():
    raw = healthy_completed_snapshot(target=4)
    priced = soak.price_scoped_usage(raw.usages)
    scope = valid_scope(target=4, approved_incremental_cost_usd=str(priced.total_usd))
    assert "incremental_cost_cap" in codes(
        runtime_findings(scope, raw, []), hard_stop=True
    )


def test_notion_archive_is_forbidden_and_skip_outcome_is_required():
    raw = healthy_completed_snapshot()
    raw.jobs[0].notion_archived_at = NOW
    raw.jobs[1].notion_skip_reason = "  "
    hard = codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)
    assert {"unexpected_notion_archive", "notion_outcome_missing"} <= hard


def test_quality_failure_quarantines_but_does_not_emergency_pause():
    raw = healthy_completed_snapshot()
    raw.phases[0].judge_status = "major_shipped"
    findings = runtime_findings(valid_scope(), raw, [healthy_running_snapshot()])
    finding = by_code(findings, "quality_major_shipped")
    assert finding.stage_failure is True
    assert finding.hard_stop is False
    assert finding.hard is False
    assert codes(findings, hard_stop=True) == set()


@pytest.mark.parametrize(
    "solver_status",
    ["mismatch_shipped", "mismatch_regen_failed", "mismatch_blocked"],
)
def test_unresolved_solver_mismatch_quarantines_but_does_not_emergency_pause(
    solver_status,
):
    raw = healthy_completed_snapshot()
    raw.phases[1].solver_status = solver_status
    findings = runtime_findings(valid_scope(), raw, [healthy_running_snapshot()])
    finding = by_code(findings, "solver_mismatch")
    assert finding.stage_failure is True
    assert finding.hard_stop is False
    assert codes(findings, hard_stop=True) == set()


def test_solver_mismatch_resolved_by_regeneration_is_success():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    phase = next(
        item
        for item in raw.phases
        if item.job_id == job.id and item.phase_name == "boss-arena"
    )
    phase.solver_status = "mismatch_regen"
    for operation in ("phase.run", "solve:boss-arena"):
        usage = raw.usages[
            _required_usage_index(raw, job_id=job.id, operation=operation)
        ]
        if operation == "phase.run" and usage.phase_name != "boss-arena":
            usage = next(
                row
                for row in raw.usages
                if row.job_id == job.id
                and row.operation == operation
                and row.phase_name == "boss-arena"
            )
        raw.usages.append(usage.model_copy(deep=True))
        job.usage_count += 1

    findings = runtime_findings(valid_scope(), raw, [healthy_running_snapshot()])

    assert "solver_regen_evidence_missing" not in codes(findings, hard_stop=True)


@pytest.mark.parametrize("missing_operation", ["phase.run", "solve:boss-arena"])
def test_mismatch_regen_requires_two_bound_generation_and_solver_calls(
    missing_operation,
):
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    phase = next(
        item
        for item in raw.phases
        if item.job_id == job.id and item.phase_name == "boss-arena"
    )
    phase.solver_status = "mismatch_regen"
    other_operation = (
        "solve:boss-arena" if missing_operation == "phase.run" else "phase.run"
    )
    usage = next(
        row
        for row in raw.usages
        if row.job_id == job.id
        and row.operation == other_operation
        and row.phase_name == "boss-arena"
    )
    raw.usages.append(usage.model_copy(deep=True))
    job.usage_count += 1

    assert "solver_regen_evidence_missing" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_mismatch_regen_does_not_count_a_wrongly_bound_second_solver_call():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    phase = next(
        item
        for item in raw.phases
        if item.job_id == job.id and item.phase_name == "boss-arena"
    )
    phase.solver_status = "mismatch_regen"
    for operation in ("phase.run", "solve:boss-arena"):
        usage = next(
            row
            for row in raw.usages
            if row.job_id == job.id
            and row.operation == operation
            and row.phase_name == "boss-arena"
        )
        duplicate = usage.model_copy(deep=True)
        if operation == "solve:boss-arena":
            duplicate.phase_job_id = raw.jobs[1].id
        raw.usages.append(duplicate)
        job.usage_count += 1

    hard = codes(runtime_findings(valid_scope(), raw, []), hard_stop=True)
    assert "usage_phase_binding_mismatch" in hard
    assert "solver_regen_evidence_missing" in hard


def test_mismatch_regen_requires_generation_between_the_two_solver_calls():
    raw = healthy_completed_snapshot()
    job = raw.jobs[0]
    phase = next(
        item
        for item in raw.phases
        if item.job_id == job.id and item.phase_name == "boss-arena"
    )
    phase.solver_status = "mismatch_regen"
    generation = next(
        row
        for row in raw.usages
        if row.job_id == job.id
        and row.operation == "phase.run"
        and row.phase_name == "boss-arena"
    )
    solver_index = next(
        index
        for index, row in enumerate(raw.usages)
        if row.job_id == job.id and row.operation == "solve:boss-arena"
    )
    solver = raw.usages[solver_index]
    # A judge-driven regeneration before the first solver must not masquerade
    # as the repair generated in response to a solver mismatch.
    raw.usages.insert(solver_index, generation.model_copy(deep=True))
    raw.usages.append(solver.model_copy(deep=True))
    job.usage_count += 2

    assert "solver_regen_evidence_missing" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "openai"),
        ("model", None),
        ("model", "wrong-content"),
        ("transport", "cli"),
        ("extract_provider", "openai"),
        ("extract_provider", None),
        ("extract_model", None),
        ("extract_model", "wrong-extract"),
        ("extract_transport", "cli"),
        ("judge_provider", "openai"),
        ("judge_provider", None),
        ("judge_model", None),
        ("judge_model", "wrong-judge"),
        ("judge_transport", "cli"),
        ("solver_provider", "openai"),
        ("solver_provider", None),
        ("solver_model", None),
        ("solver_model", "wrong-solver"),
        ("solver_transport", "cli"),
    ],
)
def test_runtime_rejects_job_role_stamp_drift(field, value):
    raw = healthy_completed_snapshot()
    setattr(raw.jobs[0], field, value)

    assert "job_role_stamp_mismatch" in codes(
        runtime_findings(valid_scope(), raw, []), hard_stop=True
    )


def test_validation_corruption_quarantines_the_stage():
    raw = healthy_completed_snapshot()
    raw.phases[1].validation_warnings = ["validation corruption: malformed payload"]
    findings = runtime_findings(valid_scope(), raw, [healthy_running_snapshot()])
    finding = by_code(findings, "validation_corruption")
    assert finding.stage_failure is True
    assert finding.hard_stop is False
    assert codes(findings, hard_stop=True) == set()


def test_clean_completed_stage_has_no_findings_when_target_peak_was_observed():
    completed = healthy_completed_snapshot()
    running = healthy_running_snapshot()
    assert runtime_findings(valid_scope(), completed, [running]) == []
