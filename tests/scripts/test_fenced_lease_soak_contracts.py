from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from scripts import fenced_lease_soak as soak


BOOK = UUID("11111111-1111-1111-1111-111111111111")
BATCH = UUID("22222222-2222-2222-2222-222222222222")
JOB = UUID("33333333-3333-3333-3333-333333333333")
EXPECTED_MODELS_BY_OPERATION = {
    "phase.run": "gemini-3.6-flash",
    "lesson.extract": "gemini-3.5-flash-lite",
    "lesson.extract.coverage": "gemini-3.5-flash",
    "lesson.extract.verify": "gemini-3.5-flash-lite",
    "judge:": "gemini-3.5-flash",
    "solve:": "gemini-3.1-pro-preview",
}


def valid_scope_dict() -> dict:
    return {
        "run_id": "stage-04-20260810",
        "since": "2026-08-10T12:00:00Z",
        "batch_ids": [str(BATCH)],
        "job_ids": [str(JOB)],
        "participant_hosts": ["Host-02"],
        "target_running": 4,
        "expected_git_sha": "fedcba9",
        "expected_code_version": 1001,
        "expected_db_revision": "0052_job_lease_fencing",
        "worker_concurrency": 2,
        "agent_max_concurrency": 4,
        "credential_max_concurrent_gemini": 32,
        "credential_slot_wait_seconds": 120,
        "legacy_gemini_var_must_be_absent": True,
        "structured_output_enabled": False,
        "required_book_sha256": {str(BOOK): "a" * 64},
        "forbidden_notion_mapping_keys": ["english|8"],
        "expected_models_by_operation_prefix": dict(EXPECTED_MODELS_BY_OPERATION),
        "approved_incremental_cost_usd": "12.50",
        "fleet_cost_limit_usd": "50.00",
        "db_preflight_connection_limit": 70,
        "db_hard_stop_connection_limit": 90,
        "heartbeat_max_age_seconds": 90,
        "attestation_max_age_seconds": 300,
        "settle_seconds": 60,
    }


def valid_worker_dict() -> dict:
    scope = soak.SoakScope.model_validate(valid_scope_dict())
    return {
        "scope_sha256": soak.sha256_canonical(scope),
        "pc_id": "Host-02:4242@fedcba9",
        "hostname": "Host-02",
        "observed_at": "2026-08-10T12:01:00Z",
        "git_sha": "fedcba9",
        "code_version": 1001,
        "worker_concurrency": 2,
        "agent_max_concurrency": 4,
        "credential_max_concurrent_gemini": 32,
        "credential_slot_wait_seconds": 120,
        "gemini_max_concurrency_present": False,
        "structured_output_enabled": False,
        "process_count_for_host": 1,
        "credential_fingerprint": "gemini:0123456789abcdef",
        "pdf_sha256_by_book": {str(BOOK): "a" * 64},
        "notion_mapping_keys": [],
    }


def valid_attestation_dict() -> dict:
    worker = soak.WorkerAttestation.model_validate(valid_worker_dict())
    return {
        "scope_sha256": worker.scope_sha256,
        "observed_at": worker.observed_at.isoformat(),
        "credential_fingerprint": worker.credential_fingerprint,
        "input_artifact_sha256": [soak.sha256_canonical(worker)],
        "workers": [worker.model_dump(mode="json")],
    }


@pytest.mark.parametrize("field", ["batch_ids", "job_ids", "participant_hosts"])
def test_scope_rejects_empty_identity_fields(field):
    raw = valid_scope_dict()
    raw[field] = []
    with pytest.raises(ValidationError):
        soak.SoakScope.model_validate(raw)


@pytest.mark.parametrize("field", ["batch_ids", "job_ids"])
def test_scope_rejects_duplicate_uuid_identity_fields(field):
    raw = valid_scope_dict()
    raw[field] = [raw[field][0], raw[field][0]]
    with pytest.raises(ValidationError, match="duplicate"):
        soak.SoakScope.model_validate(raw)


def test_scope_requires_aware_since_and_normalizes_to_utc():
    raw = valid_scope_dict()
    raw["since"] = "2026-08-10T12:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        soak.SoakScope.model_validate(raw)

    raw["since"] = "2026-08-10T08:00:00-04:00"
    scope = soak.SoakScope.model_validate(raw)
    assert scope.since == datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


def test_scope_pins_exact_participating_hosts():
    raw = valid_scope_dict()
    raw["participant_hosts"] = ["Host-02", "Host-02"]
    with pytest.raises(ValidationError, match="duplicate participant host"):
        soak.SoakScope.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "UPPER"),
        ("expected_git_sha", "not-a-sha"),
        ("expected_code_version", 0),
        ("target_running", 0),
        ("approved_incremental_cost_usd", "0"),
    ],
)
def test_scope_rejects_invalid_bounds(field, value):
    raw = valid_scope_dict()
    raw[field] = value
    with pytest.raises(ValidationError):
        soak.SoakScope.model_validate(raw)


def test_scope_requires_preflight_connection_limit_below_hard_stop():
    raw = valid_scope_dict()
    raw["db_preflight_connection_limit"] = 90
    with pytest.raises(ValidationError, match="preflight"):
        soak.SoakScope.model_validate(raw)


@pytest.mark.parametrize("missing_key", sorted(EXPECTED_MODELS_BY_OPERATION))
def test_scope_requires_every_model_operation_key(missing_key):
    raw = valid_scope_dict()
    raw["expected_models_by_operation_prefix"].pop(missing_key)

    with pytest.raises(ValidationError, match="missing required keys"):
        soak.SoakScope.model_validate(raw)


def test_scope_rejects_unknown_model_operation_key():
    raw = valid_scope_dict()
    raw["expected_models_by_operation_prefix"]["phase.unknown"] = "gemini-3.6-flash"

    with pytest.raises(ValidationError, match="unexpected keys"):
        soak.SoakScope.model_validate(raw)


@pytest.mark.parametrize("model", ["", "   ", " gemini-3.6-flash"])
def test_scope_requires_stripped_nonblank_expected_models(model):
    raw = valid_scope_dict()
    raw["expected_models_by_operation_prefix"]["phase.run"] = model

    with pytest.raises(ValidationError, match="stripped non-empty model"):
        soak.SoakScope.model_validate(raw)


def test_final_deployed_identity_is_caller_supplied_not_baked_in():
    raw = valid_scope_dict()
    raw["expected_git_sha"] = "abcdef0123456789"
    raw["expected_code_version"] = 1001
    raw["expected_db_revision"] = "0054_source_integrity"
    scope = soak.SoakScope.model_validate(raw)
    assert scope.expected_git_sha == "abcdef0123456789"
    assert scope.expected_code_version == 1001
    assert scope.expected_db_revision == "0054_source_integrity"


@pytest.mark.parametrize("value", ["", " 0054_source_integrity", "0054;drop", "UPPER"])
def test_scope_rejects_blank_or_unsafe_database_revision(value):
    raw = valid_scope_dict()
    raw["expected_db_revision"] = value
    with pytest.raises(ValidationError):
        soak.SoakScope.model_validate(raw)


def test_attestation_rejects_raw_secret_fields():
    raw = valid_attestation_dict()
    raw["workers"][0]["gemini_api_key"] = "secret"
    with pytest.raises(ValidationError):
        soak.FleetAttestation.model_validate(raw)


def test_redacted_model_dump_removes_nested_secrets_but_keeps_safe_identifiers():
    model = soak.Finding(
        code="example",
        hard=True,
        message="safe",
        evidence={
            "database_url": "postgresql://secret",
            "token": "secret",
            "credential_fingerprint": "gemini:0123456789abcdef",
            "claim_token": "safe-lease-id",
        },
    )
    dumped = soak.redacted_model_dump(model)
    assert "database_url" not in dumped["evidence"]
    assert "token" not in dumped["evidence"]
    assert dumped["evidence"]["credential_fingerprint"].startswith("gemini:")
    assert dumped["evidence"]["claim_token"] == "safe-lease-id"


def test_scope_loader_supports_stdin_and_rejects_unknown_fields(tmp_path):
    import io
    import json

    encoded = json.dumps(valid_scope_dict())
    scope = soak.load_scope("-", stdin=io.StringIO(encoded))
    assert scope.run_id == "stage-04-20260810"

    raw = valid_scope_dict()
    raw["api_key"] = "forbidden"
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        soak.load_scope(path)


def test_unarmed_watch_is_read_only_by_construction():
    args = soak.parse_args(
        [
            "watch",
            "--scope",
            "scope.json",
            "--attestation",
            "fleet.json",
            "--artifact-dir",
            "out",
        ]
    )
    assert args.arm_stop is False
    assert args.confirm_arm is None


def test_parser_rejects_confirmation_without_arm():
    with pytest.raises(SystemExit):
        soak.parse_args(
            [
                "watch",
                "--scope",
                "scope.json",
                "--attestation",
                "fleet.json",
                "--artifact-dir",
                "out",
                "--confirm-arm",
                "lease-soak-stop:stage-04-20260810",
            ]
        )


@pytest.mark.parametrize("confirm", [None, "wrong", "lease-soak-stop:other-run"])
def test_arm_stop_requires_exact_second_gesture(confirm):
    argv = [
        "watch",
        "--scope",
        "scope.json",
        "--attestation",
        "fleet.json",
        "--artifact-dir",
        "out",
        "--arm-stop",
    ]
    if confirm is not None:
        argv += ["--confirm-arm", confirm]
    with pytest.raises(SystemExit):
        soak.validate_arm_confirmation(
            soak.parse_args(argv), run_id="stage-04-20260810"
        )


def test_cli_surface_has_only_the_four_planned_commands():
    assert set(soak.build_parser()._subparsers._group_actions[0].choices) == {
        "attest-local",
        "attest-aggregate",
        "preflight",
        "watch",
    }


def test_persisted_placeholder_models_for_later_tasks_forbid_extra_fields():
    snapshot = soak.SoakSnapshot(
        run_id="stage-04-20260810",
        observed_at="2026-08-10T12:01:00Z",
        findings=[],
    )
    receipt = soak.StopReceipt(
        run_id="stage-04-20260810",
        observed_at="2026-08-10T12:01:00Z",
        trigger_code="example",
        paused_batch_ids=[str(BATCH)],
        fleet_pause_set=True,
    )
    assert snapshot.run_id == receipt.run_id
    with pytest.raises(ValidationError):
        soak.StopReceipt.model_validate({**receipt.model_dump(), "token": "secret"})
