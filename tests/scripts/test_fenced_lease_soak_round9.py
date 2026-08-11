from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_preflight import (
    healthy_raw_snapshot as healthy_preflight_snapshot,
    valid_attestation as preflight_attestation,
    valid_scope as preflight_scope,
)
from tests.scripts.test_fenced_lease_soak_snapshot import (
    NOW,
    healthy_running_snapshot,
    runtime_attestation,
    runtime_findings,
    valid_scope,
)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda raw: setattr(raw, "transaction_read_only", "off"), "schema_revision_mismatch"),
        (lambda raw: setattr(raw.schema_state, "revision", "wrong_revision"), "schema_revision_mismatch"),
        (lambda raw: setattr(raw.schema_state, "ledger_table", False), "schema_revision_mismatch"),
        (lambda raw: setattr(raw.schema_state, "job_claim_token", False), "schema_revision_mismatch"),
        (lambda raw: setattr(raw.schema_state, "phase_claim_token", False), "schema_revision_mismatch"),
        (lambda raw: setattr(next(iter(raw.books.values())), "content_sha256", "f" * 64), "book_checksum_scope_mismatch"),
    ],
)
def test_runtime_rechecks_immutable_database_and_source_contract(mutate, code):
    scope = valid_scope()
    raw = healthy_running_snapshot()
    mutate(raw)

    findings = runtime_findings(scope, raw, [])

    assert code in {row.code for row in findings if row.hard_stop}


@pytest.mark.parametrize("drift", ["book", "job"])
def test_preflight_and_runtime_reject_per_book_subject_drift(drift):
    scope = valid_scope()
    raw = healthy_running_snapshot()
    if drift == "book":
        raw.books[str(raw.jobs[0].book_id)].subject = "fizika"
    else:
        raw.jobs[0].subject = "fizika"

    preflight_codes = {
        row.code for row in soak.evaluate_preflight(scope, runtime_attestation(scope), raw)
    }
    runtime_codes = {row.code for row in runtime_findings(scope, raw, []) if row.hard_stop}

    assert "scope_job_workload_contract_mismatch" in preflight_codes
    assert "job_workload_contract_mismatch" in runtime_codes


@pytest.mark.parametrize("drift", ["credential", "holder"])
def test_runtime_binds_every_credential_slot_to_attested_process(drift):
    scope = valid_scope()
    raw = healthy_running_snapshot()
    attestation = runtime_attestation(scope)
    worker = attestation.workers[0]
    raw.credential_slots = [
        soak.CredentialSlotSnapshot(
            credential=(
                "gemini:ffffffffffffffff"
                if drift == "credential"
                else worker.credential_fingerprint
            ),
            pc_id=("Host-99:9@badcafe" if drift == "holder" else worker.pc_id),
            acquired_at=NOW,
        )
    ]

    findings = soak.evaluate_runtime(scope, attestation, raw, [])

    assert "credential_slot_identity_mismatch" in {
        row.code for row in findings if row.hard_stop
    }


def test_preflight_binds_credential_slot_to_attested_process() -> None:
    scope = preflight_scope()
    attestation = preflight_attestation()
    raw = healthy_preflight_snapshot()
    raw.credential_slots = [
        soak.CredentialSlotSnapshot(
            credential="gemini:ffffffffffffffff",
            pc_id=attestation.workers[0].pc_id,
            acquired_at=NOW,
        )
    ]

    findings = soak.evaluate_preflight(scope, attestation, raw)

    assert "credential_slot_identity_mismatch" in {
        row.code for row in findings if row.hard
    }


def unscoped_usage(*, event_id: int = 1, model: str = "gemini-3.6-flash"):
    return soak.UnscopedApiUsageSnapshot(
        id=UUID(int=event_id),
        created_at=NOW + timedelta(seconds=event_id),
        job_id=None,
        provider="gemini",
        operation="toc.extract",
        model_name=model,
        auth_mode="api",
        success=True,
    )


def test_unscoped_api_usage_is_hard_contamination_even_for_expected_model():
    raw = healthy_running_snapshot()
    raw.unscoped_api_usages = [unscoped_usage()]

    findings = runtime_findings(valid_scope(), raw, [])

    assert "unscoped_api_usage" in {row.code for row in findings if row.hard_stop}


def test_unscoped_usage_history_cannot_shrink_between_samples():
    previous = healthy_running_snapshot()
    previous.unscoped_api_usages = [unscoped_usage(event_id=1)]
    current = healthy_running_snapshot()

    findings = runtime_findings(valid_scope(), current, [previous])

    assert "unscoped_api_usage_history_shrank" in {
        row.code for row in findings if row.hard_stop
    }


def test_unscoped_api_usage_contract_serializes_only_sanitized_identity():
    dumped = unscoped_usage().model_dump(mode="json")
    assert set(dumped) == {
        "id",
        "created_at",
        "job_id",
        "provider",
        "operation",
        "model_name",
        "auth_mode",
        "success",
    }
    assert "credential" not in dumped
    assert "error_message" not in dumped


def test_credential_slot_contract_rejects_raw_credential_material() -> None:
    with pytest.raises(ValidationError, match="sanitized fingerprint"):
        soak.CredentialSlotSnapshot(
            credential="AIzaRawSecretMaterialThatMustNeverReachEvidence",
            pc_id="Host-01:100@fedcba9",
            acquired_at=NOW,
        )


def test_round9_evidence_redacts_urls_and_contains_no_prompt_or_raw_credential(
    tmp_path,
) -> None:
    raw = healthy_running_snapshot()
    usage = unscoped_usage().model_copy(
        update={"operation": "toc.extract postgresql://user:secret@db/prod"}
    )
    payload = raw.model_dump(mode="python", by_alias=True)
    payload["unscoped_api_usages"] = [usage]
    payload["unscoped_api_usage_watermark"] = soak.UsageWatermark(
        created_at=usage.created_at,
        id=usage.id,
    )
    raw = soak.RawSnapshot.model_validate(payload)
    scope = valid_scope()
    writer = soak.ArtifactWriter(tmp_path, scope.run_id)

    writer.append(soak._evidence_sample(scope, raw, [], phase="watch"))

    encoded = writer.samples_path.read_text(encoding="utf-8")
    assert "user:secret" not in encoded
    assert "postgresql://" not in encoded
    assert "custom_prompts\"" not in encoded
    assert "AIza" not in encoded
