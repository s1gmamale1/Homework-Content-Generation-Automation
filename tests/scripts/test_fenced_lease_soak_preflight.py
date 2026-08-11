from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_contracts import (
    BATCH,
    BOOK,
    JOB,
    valid_scope_dict,
)


NOW = datetime(2026, 8, 10, 12, 5, tzinfo=timezone.utc)


def valid_scope(**updates) -> soak.SoakScope:
    raw = valid_scope_dict()
    raw.update(
        {
            "participant_hosts": ["Host-02", "Host-03"],
            "target_running": 4,
            "forbidden_notion_mapping_keys": ["english|8"],
            **updates,
        }
    )
    return soak.SoakScope.model_validate(raw)


def valid_worker(index: int = 2) -> soak.WorkerAttestation:
    scope = valid_scope()
    hostname = f"Host-{index:02d}"
    return soak.WorkerAttestation(
        scope_sha256=soak.sha256_canonical(scope),
        pc_id=f"{hostname}:{4240 + index}@fedcba9",
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
        pdf_sha256_by_book={str(BOOK): "a" * 64},
        notion_mapping_keys=[],
    )


def valid_attestation() -> soak.FleetAttestation:
    workers = [valid_worker(2), valid_worker(3)]
    return soak.FleetAttestation(
        scope_sha256=workers[0].scope_sha256,
        observed_at=workers[0].observed_at,
        credential_fingerprint=workers[0].credential_fingerprint,
        input_artifact_sha256=[
            soak.sha256_canonical(worker) for worker in workers
        ],
        workers=workers,
    )


def healthy_raw_snapshot() -> soak.RawSnapshot:
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
            api_paused_reason="lease-soak-staging:stage-04-20260810",
            min_worker_version=1001,
        ),
        jobs=[
            soak.JobSnapshot(
                id=job_id,
                batch_id=BATCH,
                book_id=BOOK,
                batch_book_id=BOOK,
                status="pending",
                attempts=0,
                claim_token=None,
                created_at=NOW - timedelta(minutes=2),
                phase_count=0,
                usage_count=0,
                lease_count=0,
            )
            for job_id in valid_scope().job_ids
        ],
        books={str(BOOK): soak.BookSnapshot(id=BOOK, content_sha256="a" * 64)},
        unrelated_active_jobs=[],
        workers=[
            soak.RegistryWorkerSnapshot(
                pc_id=f"Host-{index:02d}:{4240 + index}@fedcba9",
                hostname=f"Host-{index:02d}",
                last_heartbeat=NOW - timedelta(seconds=5),
                status="online",
                git_sha="fedcba9",
                code_version=1001,
                can_gemini_api=True,
            )
            for index in (2, 3)
        ],
        scrub_tombstones=[],
        db=soak.DatabaseSnapshot(
            total_connections=20,
            max_connections=100,
            superuser_reserved_connections=3,
            idle_in_transaction_timeout_ms=300_000,
            idle_in_transaction=[],
            server_waits=[],
        ),
        credential_slots=[],
        lease_events=[],
        phases=[],
        usages=[],
        fleet_usages_24h=[
            soak.UsageSnapshot(
                job_id=None,
                provider="gemini",
                model_name="gemini-3.6-flash",
                auth_mode="api",
                prompt_tokens=1000,
                output_tokens=100,
                cached_tokens=0,
                cache_creation_tokens=0,
                success=True,
            )
        ],
    )


def hard_codes(findings: list[soak.Finding]) -> set[str]:
    return {finding.code for finding in findings if finding.hard}


def assert_hard(code: str, raw: soak.RawSnapshot, *, scope=None, attestation=None):
    got = soak.evaluate_preflight(
        scope or valid_scope(), attestation or valid_attestation(), raw
    )
    assert code in hard_codes(got)


def test_healthy_preflight_has_no_findings():
    assert soak.evaluate_preflight(
        valid_scope(), valid_attestation(), healthy_raw_snapshot()
    ) == []


class _FailingCollectStore:
    async def collect(self, scope):
        del scope
        raise OSError("postgresql://user:secret@db/private?token=raw-secret")


class _CancelledCollectStore:
    async def collect(self, scope):
        del scope
        raise asyncio.CancelledError("Bearer raw-cancel-secret")


class _FailFirstAppendWriter(soak.ArtifactWriter):
    def __init__(self, artifact_dir, run_id):
        super().__init__(artifact_dir, run_id)
        self.calls = 0

    def append(self, sample):
        self.calls += 1
        if self.calls == 1:
            raise OSError("artifact-secret")
        return super().append(sample)


class _FailFirstFinishWriter(soak.ArtifactWriter):
    def __init__(self, artifact_dir, run_id):
        super().__init__(artifact_dir, run_id)
        self.calls = 0

    def finish(self, summary):
        self.calls += 1
        if self.calls == 1:
            raise OSError("finish-secret")
        return super().finish(summary)


class _HealthyStore:
    async def collect(self, scope):
        del scope
        return healthy_raw_snapshot()


@pytest.mark.asyncio
async def test_run_preflight_persists_db_error_as_digest_only_operational_error(tmp_path):
    scope = valid_scope()
    raw_error = "postgresql://user:secret@db/private?token=raw-secret"

    code = await soak.run_preflight(
        scope=scope,
        attestation=valid_attestation(),
        store=_FailingCollectStore(),
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        clock=lambda: NOW,
    )

    assert code == soak.ExitCode.OPERATIONAL_ERROR
    summary = json.loads((tmp_path / f"{scope.run_id}.summary.json").read_text())
    assert summary["verdict"] == "operational_error"
    finding = summary["findings"][0]
    assert finding["code"] == "preflight_operational_error"
    assert finding["evidence"] == {
        "error_type": "OSError",
        "error_sha256": hashlib.sha256(raw_error.encode()).hexdigest(),
    }
    assert "secret" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_run_preflight_persists_cancellation_as_digest_only_incomplete(tmp_path):
    scope = valid_scope()

    code = await soak.run_preflight(
        scope=scope,
        attestation=valid_attestation(),
        store=_CancelledCollectStore(),
        writer=soak.ArtifactWriter(tmp_path, scope.run_id),
        clock=lambda: NOW,
    )

    assert code == soak.ExitCode.INCOMPLETE
    summary = json.loads((tmp_path / f"{scope.run_id}.summary.json").read_text())
    assert summary["verdict"] == "incomplete"
    assert summary["findings"][0]["code"] == "preflight_incomplete"
    assert "raw-cancel-secret" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_run_preflight_converts_artifact_failure_without_raw_escape(tmp_path):
    scope = valid_scope()
    writer = _FailFirstAppendWriter(tmp_path, scope.run_id)

    code = await soak.run_preflight(
        scope=scope,
        attestation=valid_attestation(),
        store=_HealthyStore(),
        writer=writer,
        clock=lambda: NOW,
    )

    assert code == soak.ExitCode.OPERATIONAL_ERROR
    summary = json.loads((tmp_path / f"{scope.run_id}.summary.json").read_text())
    assert summary["findings"][0]["code"] == "preflight_operational_error"
    assert "artifact-secret" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_run_preflight_retries_failed_final_summary_as_operational_error(tmp_path):
    scope = valid_scope()
    writer = _FailFirstFinishWriter(tmp_path, scope.run_id)

    code = await soak.run_preflight(
        scope=scope,
        attestation=valid_attestation(),
        store=_HealthyStore(),
        writer=writer,
        clock=lambda: NOW,
    )

    assert code == soak.ExitCode.OPERATIONAL_ERROR
    summary = json.loads((tmp_path / f"{scope.run_id}.summary.json").read_text())
    assert summary["findings"][0]["code"] == "preflight_operational_error"
    assert "finish-secret" not in json.dumps(summary)


def test_preflight_uses_caller_supplied_exact_database_revision():
    scope = valid_scope(expected_db_revision="0054_source_integrity")
    attestation = valid_attestation().model_copy(deep=True)
    attestation.scope_sha256 = soak.sha256_canonical(scope)
    attestation.workers[0].scope_sha256 = attestation.scope_sha256
    raw = healthy_raw_snapshot()
    raw.schema_state.revision = "0054_source_integrity"
    assert "schema_revision_mismatch" not in hard_codes(
        soak.evaluate_preflight(scope, attestation, raw)
    )


@pytest.mark.parametrize("timeout_ms", [0, 300_001, 900_000])
def test_preflight_rejects_disabled_or_over_five_minute_idle_timeout(timeout_ms):
    raw = healthy_raw_snapshot()
    raw.db.idle_in_transaction_timeout_ms = timeout_ms
    assert_hard("db_idle_in_transaction_timeout_unsafe", raw)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda raw: setattr(raw.schema_state, "revision", "0051_launch_defaults_3x"), "schema_revision_mismatch"),
        (lambda raw: raw.jobs.clear(), "scope_job_missing"),
        (lambda raw: setattr(raw.jobs[0], "batch_id", uuid4()), "scope_job_wrong_batch"),
        (lambda raw: setattr(raw.jobs[0], "attempts", 1), "scope_job_not_pristine"),
        (lambda raw: raw.unrelated_active_jobs.append(soak.ActiveJobSnapshot(id=uuid4(), status="running")), "unrelated_active_queue_not_empty"),
        (lambda raw: setattr(raw.budget, "api_paused_reason", "manual"), "staging_pause_missing_or_foreign"),
        (lambda raw: setattr(raw.budget, "min_worker_version", 999), "version_floor_mismatch"),
        (lambda raw: setattr(raw.workers[0], "pc_id", "Host-02:9@fedcba9"), "worker_registry_missing"),
        (lambda raw: setattr(raw.workers[0], "git_sha", "abcdef0"), "worker_sha_mismatch"),
        (lambda raw: setattr(raw.db, "total_connections", 71), "db_connection_baseline_high"),
        (lambda raw: raw.db.idle_in_transaction.append({"pid": 9, "application_name": "hcga-worker:x", "age_s": 301}), "db_idle_in_transaction"),
        (lambda raw: raw.db.server_waits.append({"pid": 8, "wait_event_type": "Lock", "wait_event": "transactionid"}), "db_server_wait"),
        (lambda raw: raw.credential_slots.append(soak.CredentialSlotSnapshot(credential="gemini:0123456789abcdef", pc_id="Host-02:4242@fedcba9", acquired_at=NOW)), "credential_slot_baseline_nonzero"),
    ],
)
def test_preflight_fails_closed_for_raw_snapshot_drift(mutation, code):
    raw = healthy_raw_snapshot()
    mutation(raw)
    assert_hard(code, raw)


def test_preflight_fails_when_a_same_version_unattested_worker_can_claim():
    raw = healthy_raw_snapshot()
    raw.workers.append(
        soak.RegistryWorkerSnapshot(
            pc_id="rogue:9@fedcba9",
            hostname="rogue",
            last_heartbeat=NOW,
            status="online",
            git_sha="fedcba9",
            code_version=1001,
            can_gemini_api=True,
        )
    )
    assert_hard("unattested_claimable_worker", raw)


@pytest.mark.parametrize("status", ["pending", "running", "cancelling"])
def test_preflight_rejects_unrelated_active_queue_rows(status):
    raw = healthy_raw_snapshot()
    raw.unrelated_active_jobs.append(soak.ActiveJobSnapshot(id=uuid4(), status=status))
    assert_hard("unrelated_active_queue_not_empty", raw)


def test_scoped_fresh_pending_jobs_are_expected_under_staging_pause():
    raw = healthy_raw_snapshot()
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert {job.status for job in raw.jobs} == {"pending"}
    assert "unrelated_active_queue_not_empty" not in hard_codes(findings)
    assert "scope_job_not_pristine" not in hard_codes(findings)


def test_preflight_requires_zero_idle_in_transaction_even_below_pool_limit():
    raw = healthy_raw_snapshot()
    raw.db.idle_in_transaction = [
        {"pid": 87104, "application_name": "hcga-worker:Host-40:1", "age_s": 301}
    ]
    assert_hard("db_idle_in_transaction", raw)


def test_preflight_rejects_non_pristine_scoped_job():
    raw = healthy_raw_snapshot()
    raw.jobs[0].attempts = 1
    raw.jobs[0].claim_token = uuid4()
    assert_hard("scope_job_not_pristine", raw)


def test_preflight_rejects_scope_pdf_hash_that_disagrees_with_book_row():
    raw = healthy_raw_snapshot()
    raw.books[str(BOOK)].content_sha256 = "b" * 64
    assert_hard("book_checksum_scope_mismatch", raw)


def test_preflight_rejects_job_whose_batch_points_at_another_book():
    raw = healthy_raw_snapshot()
    raw.jobs[0].batch_book_id = uuid4()
    assert_hard("scope_job_wrong_batch", raw)


def test_preflight_rejects_attested_hostname_that_disagrees_with_registry_pair():
    raw = healthy_raw_snapshot()
    raw.workers[0].hostname = "Host-03"
    assert_hard("worker_registry_missing", raw)


def test_preflight_rejects_stale_attestation_and_heartbeat():
    attestation = valid_attestation().model_copy(deep=True)
    attestation.observed_at = NOW - timedelta(seconds=301)
    attestation.workers[0].observed_at = attestation.observed_at
    assert_hard("worker_attestation_stale", healthy_raw_snapshot(), attestation=attestation)

    raw = healthy_raw_snapshot()
    raw.workers[0].last_heartbeat = NOW - timedelta(seconds=91)
    assert_hard("worker_registry_missing", raw)


def test_preflight_rejects_stale_individual_worker_even_with_fresh_aggregate():
    attestation = valid_attestation().model_copy(deep=True)
    attestation.observed_at = NOW
    attestation.workers[0].observed_at = NOW - timedelta(seconds=301)
    assert_hard(
        "worker_attestation_stale",
        healthy_raw_snapshot(),
        attestation=attestation,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_concurrency", 3),
        ("agent_max_concurrency", 5),
        ("credential_max_concurrent_gemini", 31),
        ("credential_slot_wait_seconds", 119),
        ("gemini_max_concurrency_present", True),
        ("structured_output_enabled", True),
        ("process_count_for_host", 2),
    ],
)
def test_preflight_rejects_attested_config_drift(field, value):
    attestation = valid_attestation().model_copy(deep=True)
    setattr(attestation.workers[0], field, value)
    assert_hard("worker_config_mismatch", healthy_raw_snapshot(), attestation=attestation)


def test_preflight_rejects_credential_pdf_and_notion_drift():
    attestation = valid_attestation().model_copy(deep=True)
    attestation.workers[0].credential_fingerprint = "gemini:ffffffffffffffff"
    assert_hard("credential_fingerprint_mismatch", healthy_raw_snapshot(), attestation=attestation)

    attestation = valid_attestation().model_copy(deep=True)
    attestation.workers[0].pdf_sha256_by_book[str(BOOK)] = None
    assert_hard("pdf_missing_or_mismatch", healthy_raw_snapshot(), attestation=attestation)

    attestation = valid_attestation().model_copy(deep=True)
    attestation.workers[0].notion_mapping_keys = ["english|8"]
    assert_hard("notion_mapping_present", healthy_raw_snapshot(), attestation=attestation)


def test_preflight_rejects_fleet_cost_envelope_overrun():
    scope = valid_scope(approved_incremental_cost_usd="49.9999", fleet_cost_limit_usd="50")
    attestation = valid_attestation().model_copy(deep=True)
    attestation.scope_sha256 = soak.sha256_canonical(scope)
    attestation.workers[0].scope_sha256 = attestation.scope_sha256
    assert_hard(
        "fleet_cost_envelope_exceeded",
        healthy_raw_snapshot(),
        scope=scope,
        attestation=attestation,
    )


def test_preflight_rejects_unpriced_api_usage_in_cost_baseline():
    raw = healthy_raw_snapshot()
    raw.fleet_usages_24h[0].model_name = "unknown-paid-model"
    assert_hard("fleet_cost_envelope_exceeded", raw)


def test_preflight_rejects_snapshot_not_proven_read_only():
    raw = healthy_raw_snapshot()
    raw.transaction_read_only = "off"
    assert_hard("schema_revision_mismatch", raw)


def test_every_finding_has_stable_json_evidence_and_bounded_message():
    raw = healthy_raw_snapshot()
    raw.schema_state.revision = "wrong"
    finding = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)[0]
    assert finding.hard is True
    assert finding.evidence
    assert len(finding.message) <= 500


def test_activity_query_prefix_is_diagnostic_but_never_contains_literals_or_tokens():
    raw = "update x set token='secret-value' where url='https://user:pass@db/x' -- Bearer abc123"
    sanitized = soak.sanitize_query_prefix(raw)
    assert sanitized.startswith("update x set token='?' where url='?'")
    assert "secret-value" not in sanitized
    assert "user:pass" not in sanitized
    assert "abc123" not in sanitized


def test_non_plain_credential_identity_is_one_way_in_evidence():
    sanitized = soak.sanitize_credential_identity("gemini:project-sensitive-name")
    assert sanitized.startswith("credential:sha256:")
    assert "project-sensitive-name" not in sanitized
    assert soak.sanitize_credential_identity("gemini:0123456789abcdef") == (
        "gemini:0123456789abcdef"
    )
