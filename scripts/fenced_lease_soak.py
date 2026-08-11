#!/usr/bin/env python3
"""Fail-closed controller for a separately authorized fenced-lease soak.

Task 2 intentionally contains only immutable JSON contracts and local fleet
attestation.  It opens no database, network, or model-provider client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import socket
import sys
from contextlib import asynccontextmanager
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO
from uuid import UUID

import psutil
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import Settings
from app.services import code_version, credential_id, flows, pricing


_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,44}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DB_REVISION_RE = re.compile(r"^[0-9a-z][0-9a-z_]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLAIN_GEMINI_FP_RE = re.compile(r"^gemini:[0-9a-f]{16}$")
_SAFE_REDACTED_FIELDS = {
    "claim_token",
    "credential_fingerprint",
    "forbidden_notion_mapping_keys",
}
_SECRET_FIELD_PARTS = (
    "gemini_api_key",
    "api_key",
    "token",
    "secret",
    "password",
    "database_url",
)
_REQUIRED_MODEL_OPERATION_KEYS = frozenset(
    {
        "phase.run",
        "lesson.extract",
        "lesson.extract.coverage",
        "lesson.extract.verify",
        "judge:",
        "solve:",
    }
)


class AttestationError(RuntimeError):
    """The host cannot prove the exact soak contract without leaking secrets."""


class ExitCode(IntEnum):
    PASS = 0
    PREFLIGHT_FAILED = 2
    HARD_STOP_READ_ONLY = 3
    HARD_STOP_ARMED = 4
    INCOMPLETE = 5
    OPERATIONAL_ERROR = 6


class PersistedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _reject_duplicates(values: Sequence[Any], *, label: str) -> Sequence[Any]:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")
    return values


class SoakScope(PersistedModel):
    run_id: str
    since: datetime
    batch_ids: list[UUID] = Field(min_length=1)
    job_ids: list[UUID] = Field(min_length=1)
    participant_hosts: list[str] = Field(min_length=1)
    target_running: int = Field(gt=0)
    expected_git_sha: str
    expected_code_version: int = Field(gt=0)
    expected_db_revision: str
    worker_concurrency: int = Field(ge=0)
    agent_max_concurrency: int = Field(gt=0)
    credential_max_concurrent_gemini: int = Field(ge=0)
    credential_slot_wait_seconds: int = Field(ge=1)
    legacy_gemini_var_must_be_absent: bool
    structured_output_enabled: bool
    required_book_sha256: dict[str, str] = Field(min_length=1)
    forbidden_notion_mapping_keys: list[str]
    expected_models_by_operation_prefix: dict[str, str] = Field(min_length=1)
    approved_incremental_cost_usd: Decimal = Field(gt=0)
    fleet_cost_limit_usd: Decimal = Field(gt=0)
    db_preflight_connection_limit: int = Field(gt=0)
    db_hard_stop_connection_limit: int = Field(gt=0)
    heartbeat_max_age_seconds: int = Field(gt=0)
    attestation_max_age_seconds: int = Field(gt=0)
    settle_seconds: int = Field(gt=0)

    @field_validator("run_id")
    @classmethod
    def _valid_run_id(cls, value: str) -> str:
        if not _RUN_ID_RE.fullmatch(value):
            raise ValueError("run_id must be lowercase alphanumeric/hyphen and <=45 chars")
        return value

    @field_validator("expected_git_sha")
    @classmethod
    def _valid_git_sha(cls, value: str) -> str:
        if not _GIT_SHA_RE.fullmatch(value):
            raise ValueError("expected_git_sha must be a 7-40 character lowercase hex SHA")
        return value

    @field_validator("expected_db_revision")
    @classmethod
    def _valid_db_revision(cls, value: str) -> str:
        if not _DB_REVISION_RE.fullmatch(value):
            raise ValueError("expected_db_revision must be a lowercase Alembic revision")
        return value

    @field_validator("since")
    @classmethod
    def _valid_since(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="since")

    @field_validator("batch_ids")
    @classmethod
    def _unique_batch_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(_reject_duplicates(value, label="batch id"))

    @field_validator("job_ids")
    @classmethod
    def _unique_job_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(_reject_duplicates(value, label="job id"))

    @field_validator("participant_hosts")
    @classmethod
    def _unique_hosts(cls, value: list[str]) -> list[str]:
        if any(not host.strip() or host != host.strip() for host in value):
            raise ValueError("participant host must be a stripped non-empty string")
        return list(_reject_duplicates(value, label="participant host"))

    @field_validator("forbidden_notion_mapping_keys")
    @classmethod
    def _unique_notion_keys(cls, value: list[str]) -> list[str]:
        if any(not key.strip() or key != key.strip() for key in value):
            raise ValueError("Notion mapping keys must be stripped and non-empty")
        return list(_reject_duplicates(value, label="Notion mapping key"))

    @field_validator("required_book_sha256")
    @classmethod
    def _valid_book_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_book_id, digest in value.items():
            book_id = str(UUID(raw_book_id))
            if book_id in normalized:
                raise ValueError("duplicate canonical book id")
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"invalid PDF SHA-256 for book {book_id}")
            normalized[book_id] = digest
        return normalized

    @field_validator("expected_models_by_operation_prefix")
    @classmethod
    def _valid_expected_models(cls, value: dict[str, str]) -> dict[str, str]:
        actual_keys = set(value)
        missing = sorted(_REQUIRED_MODEL_OPERATION_KEYS - actual_keys)
        unexpected = sorted(actual_keys - _REQUIRED_MODEL_OPERATION_KEYS)
        errors: list[str] = []
        if missing:
            errors.append(f"missing required keys: {missing}")
        if unexpected:
            errors.append(f"unexpected keys: {unexpected}")
        if errors:
            raise ValueError("; ".join(errors))
        if any(not model.strip() or model != model.strip() for model in value.values()):
            raise ValueError("expected models must be stripped non-empty model names")
        return value

    @model_validator(mode="after")
    def _valid_limits(self) -> "SoakScope":
        if self.db_preflight_connection_limit >= self.db_hard_stop_connection_limit:
            raise ValueError("db preflight connection limit must be below hard stop")
        if self.approved_incremental_cost_usd > self.fleet_cost_limit_usd:
            raise ValueError("approved incremental cost cannot exceed fleet cost limit")
        return self


class WorkerAttestation(PersistedModel):
    scope_sha256: str
    pc_id: str
    hostname: str
    observed_at: datetime
    git_sha: str
    code_version: int = Field(gt=0)
    worker_concurrency: int = Field(ge=0)
    agent_max_concurrency: int = Field(gt=0)
    credential_max_concurrent_gemini: int = Field(ge=0)
    credential_slot_wait_seconds: int = Field(ge=1)
    gemini_max_concurrency_present: bool
    structured_output_enabled: bool
    process_count_for_host: int = Field(ge=0)
    credential_fingerprint: str
    pdf_sha256_by_book: dict[str, str | None]
    notion_mapping_keys: list[str]

    @field_validator("scope_sha256")
    @classmethod
    def _valid_scope_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("scope_sha256 must be lowercase SHA-256")
        return value

    @field_validator("git_sha")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        if not _GIT_SHA_RE.fullmatch(value):
            raise ValueError("git_sha must be lowercase hex")
        return value

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="observed_at")

    @field_validator("credential_fingerprint")
    @classmethod
    def _valid_credential_fp(cls, value: str) -> str:
        if not _PLAIN_GEMINI_FP_RE.fullmatch(value):
            raise ValueError("credential fingerprint must be a plain Gemini key fingerprint")
        return value

    @field_validator("pdf_sha256_by_book")
    @classmethod
    def _valid_pdf_evidence(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        normalized: dict[str, str | None] = {}
        for raw_book_id, digest in value.items():
            book_id = str(UUID(raw_book_id))
            if digest is not None and not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"invalid PDF SHA-256 for book {book_id}")
            normalized[book_id] = digest
        return normalized


class FleetAttestation(PersistedModel):
    scope_sha256: str
    observed_at: datetime
    credential_fingerprint: str
    input_artifact_sha256: list[str] = Field(min_length=1)
    workers: list[WorkerAttestation] = Field(min_length=1)

    @field_validator("scope_sha256")
    @classmethod
    def _valid_scope_hash(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("scope_sha256 must be lowercase SHA-256")
        return value

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="observed_at")

    @field_validator("credential_fingerprint")
    @classmethod
    def _valid_credential_fp(cls, value: str) -> str:
        if not _PLAIN_GEMINI_FP_RE.fullmatch(value):
            raise ValueError("credential fingerprint must be a plain Gemini key fingerprint")
        return value

    @field_validator("input_artifact_sha256")
    @classmethod
    def _valid_input_hashes(cls, value: list[str]) -> list[str]:
        if any(not _SHA256_RE.fullmatch(digest) for digest in value):
            raise ValueError("input artifact digest must be lowercase SHA-256")
        return value


class Finding(PersistedModel):
    code: str
    hard: bool
    hard_stop: bool = False
    stage_failure: bool = False
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class SoakSnapshot(PersistedModel):
    run_id: str
    observed_at: datetime
    findings: list[Finding]

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="observed_at")


class StopReceipt(PersistedModel):
    run_id: str
    observed_at: datetime
    trigger_code: str
    paused_batch_ids: list[UUID]
    fleet_pause_set: bool

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="observed_at")


class SchemaSnapshot(PersistedModel):
    revision: str | None
    ledger_table: bool
    job_claim_token: bool
    phase_claim_token: bool


class BudgetSnapshot(PersistedModel):
    api_paused_reason: str | None
    min_worker_version: int | None


class JobSnapshot(PersistedModel):
    id: UUID
    batch_id: UUID | None
    book_id: UUID
    batch_book_id: UUID | None
    subject: str | None = None
    selected_phases: list[str] | None = None
    status: str
    attempts: int
    claim_token: UUID | None
    claimed_by: str | None = None
    created_at: datetime
    error_message: str | None = None
    last_error: str | None = None
    notion_archived_at: datetime | None = None
    notion_skip_reason: str | None = None
    phase_count: int = 0
    usage_count: int = 0
    lease_count: int = 0


class BookSnapshot(PersistedModel):
    id: UUID
    content_sha256: str


class ActiveJobSnapshot(PersistedModel):
    id: UUID
    status: str


class RegistryWorkerSnapshot(PersistedModel):
    pc_id: str
    hostname: str
    last_heartbeat: datetime
    status: str
    git_sha: str | None
    code_version: int | None
    can_gemini_api: bool


class DatabaseSnapshot(PersistedModel):
    total_connections: int
    max_connections: int
    superuser_reserved_connections: int
    idle_in_transaction_timeout_ms: int
    idle_in_transaction: list[dict[str, Any]]
    server_waits: list[dict[str, Any]]


class CredentialSlotSnapshot(PersistedModel):
    credential: str
    pc_id: str
    acquired_at: datetime
    slot_count: int = 1


class LeaseEventSnapshot(PersistedModel):
    job_id: UUID
    claim_token: UUID | None
    event_type: str
    owner: str | None = None
    created_at: datetime


class PhaseSnapshot(PersistedModel):
    job_id: UUID
    phase_name: str
    phase_order: int = 0
    status: str
    claim_token: UUID | None
    error_message: str | None = None
    validation_warnings: list[str] | None = None
    judge_status: str | None = None
    solver_status: str | None = None


class UsageSnapshot(PersistedModel):
    job_id: UUID | None
    provider: str
    operation: str = ""
    model_name: str | None
    auth_mode: str
    prompt_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    total_tokens: int = 0
    success: bool
    error_message: str | None = None


class PricedUsageRow(PersistedModel):
    job_id: UUID | None
    operation: str
    provider: str
    model_name: str | None
    prompt_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    cost_usd: Decimal


class UsageCost(PersistedModel):
    total_usd: Decimal
    rows: list[PricedUsageRow]


class ErrorClass(str, Enum):
    PROVIDER_429 = "provider_429"
    SLOT_EXHAUSTION = "slot_exhaustion"
    AUTH = "auth"
    ATTEMPT_TIMEOUT = "attempt_timeout"
    NETWORK = "network"
    OTHER = "other"


class RawSnapshot(PersistedModel):
    observed_at: datetime
    transaction_read_only: str
    schema_state: SchemaSnapshot = Field(alias="schema")
    budget: BudgetSnapshot
    jobs: list[JobSnapshot]
    books: dict[str, BookSnapshot]
    unrelated_active_jobs: list[ActiveJobSnapshot]
    workers: list[RegistryWorkerSnapshot]
    scrub_tombstones: list[str]
    db: DatabaseSnapshot
    credential_slots: list[CredentialSlotSnapshot]
    lease_events: list[LeaseEventSnapshot]
    phases: list[PhaseSnapshot]
    usages: list[UsageSnapshot]
    fleet_usages_24h: list[UsageSnapshot]

    @property
    def scope_job_ids(self) -> list[UUID]:
        return [job.id for job in self.jobs]


class SoakReadStore(Protocol):
    async def collect(self, scope: SoakScope) -> RawSnapshot: ...


def _finding(
    code: str,
    message: str,
    *,
    hard: bool = True,
    hard_stop: bool = False,
    stage_failure: bool = False,
    **evidence: Any,
) -> Finding:
    return Finding(
        code=code,
        hard=hard,
        hard_stop=hard_stop,
        stage_failure=stage_failure,
        message=message[:500],
        evidence=evidence,
    )


def _age_seconds(now: datetime, then: datetime) -> float:
    return (_aware_utc(now, field_name="observed_at") - then).total_seconds()


def _fleet_cost(rows: Sequence[UsageSnapshot]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        if row.auth_mode != "api":
            continue
        total += Decimal(
            str(
                pricing.cost_usd(
                    row.provider,
                    row.model_name,
                    {
                        "prompt_tokens": row.prompt_tokens,
                        "output_tokens": row.output_tokens,
                        "cached_tokens": row.cached_tokens,
                        "cache_creation_tokens": row.cache_creation_tokens,
                    },
                )
            )
        )
    return total


def classify_error(text_value: str) -> ErrorClass | None:
    """Classify persisted failure text without depending on provider envelopes."""
    message = (text_value or "").strip().lower()
    if not message:
        return None
    # Slot saturation strings begin with 429 in the worker path.  This more
    # specific class must therefore win over the generic provider-rate class.
    if "fleet credential slot wait exhausted" in message:
        return ErrorClass.SLOT_EXHAUSTION
    if (
        "resource_exhausted" in message
        or "resource exhausted" in message
        or re.search(r"(?:^|\D)429(?:\D|$)", message)
        or "rate limit" in message
        or "quota exceeded" in message
    ):
        return ErrorClass.PROVIDER_429
    if (
        "permission_denied" in message
        or "permission denied" in message
        or "unauthenticated" in message
        or "unauthorized" in message
        or "invalid api key" in message
        or re.search(r"(?:^|\D)(?:401|403)(?:\D|$)", message)
    ):
        return ErrorClass.AUTH
    if "per-attempt timeout" in message or "attempt timed out" in message:
        return ErrorClass.ATTEMPT_TIMEOUT
    if any(
        marker in message
        for marker in (
            "connection reset",
            "connection aborted",
            "connection refused",
            "connection closed",
            "broken pipe",
            "network is unreachable",
            "temporary failure in name resolution",
            "timed out connecting",
        )
    ):
        return ErrorClass.NETWORK
    return ErrorClass.OTHER


def price_scoped_usage(rows: Sequence[UsageSnapshot]) -> UsageCost:
    """Price persisted rows through the production price function, exactly once."""
    priced: list[PricedUsageRow] = []
    total = Decimal("0")
    for row in rows:
        raw_cost = (
            pricing.cost_usd(
                row.provider,
                row.model_name,
                {
                    "prompt_tokens": row.prompt_tokens,
                    "output_tokens": row.output_tokens,
                    "cached_tokens": row.cached_tokens,
                    "cache_creation_tokens": row.cache_creation_tokens,
                },
            )
            if row.auth_mode == "api"
            else 0.0
        )
        # pricing.cost_usd intentionally returns float for the wider app.  A
        # fixed 12-decimal money boundary removes binary artifacts while still
        # retaining sub-microcent token costs for exact cap comparisons.
        row_cost = Decimal(str(raw_cost)).quantize(Decimal("0.000000000001"))
        total += row_cost
        priced.append(
            PricedUsageRow(
                job_id=row.job_id,
                operation=row.operation,
                provider=row.provider,
                model_name=row.model_name,
                prompt_tokens=row.prompt_tokens,
                output_tokens=row.output_tokens,
                cached_tokens=row.cached_tokens,
                cache_creation_tokens=row.cache_creation_tokens,
                total_tokens=row.total_tokens,
                cost_usd=row_cost,
            )
        )
    return UsageCost(total_usd=total, rows=priced)


def _runtime_hard(code: str, message: str, **evidence: Any) -> Finding:
    return _finding(
        code,
        message,
        hard=True,
        hard_stop=True,
        stage_failure=True,
        **evidence,
    )


def _runtime_stage_failure(code: str, message: str, **evidence: Any) -> Finding:
    return _finding(
        code,
        message,
        hard=False,
        hard_stop=False,
        stage_failure=True,
        **evidence,
    )


def _append_runtime_finding(
    findings: list[Finding], finding: Finding
) -> None:
    """Keep evidence compact and every runtime code stable within one sample."""
    if not any(existing.code == finding.code for existing in findings):
        findings.append(finding)


def _expected_model(scope: SoakScope, operation: str) -> str | None:
    expected = scope.expected_models_by_operation_prefix
    if operation in {
        "phase.run",
        "lesson.extract",
        "lesson.extract.coverage",
        "lesson.extract.verify",
    }:
        return expected.get(operation)
    for prefix in ("judge:", "solve:"):
        if operation.startswith(prefix):
            return expected.get(prefix)
    return None


def _heartbeat_stale_hosts(scope: SoakScope, raw: RawSnapshot) -> set[str]:
    fresh_hosts: set[str] = set()
    for worker in raw.workers:
        age = _age_seconds(raw.observed_at, worker.last_heartbeat)
        if (
            worker.hostname in scope.participant_hosts
            and worker.status == "online"
            and 0 <= age <= scope.heartbeat_max_age_seconds
        ):
            fresh_hosts.add(worker.hostname)
    return set(scope.participant_hosts) - fresh_hosts


def evaluate_runtime(
    scope: SoakScope,
    attestation: FleetAttestation,
    raw: RawSnapshot,
    previous_samples: Sequence[RawSnapshot],
) -> list[Finding]:
    """Evaluate a live/terminal stage without I/O; safety drifts fail closed."""
    findings: list[Finding] = []
    scoped_ids = set(scope.job_ids)
    jobs = {job.id: job for job in raw.jobs if job.id in scoped_ids}

    # Lease-event invariants are intentionally independent: a reclaim must not
    # disappear behind the more generic claim-count finding.
    events_by_job: dict[UUID, list[LeaseEventSnapshot]] = {
        job_id: [] for job_id in scoped_ids
    }
    for event in raw.lease_events:
        if event.job_id in scoped_ids:
            events_by_job.setdefault(event.job_id, []).append(event)
    event_types = {event.event_type for event in raw.lease_events}
    if "lease_lost" in event_types:
        _append_runtime_finding(
            findings,
            _runtime_hard("lease_lost", "a worker recorded loss of a scoped lease"),
        )
    reclaimed = sorted(event_types & {"reclaimed_stale", "reclaimed_forced"})
    if reclaimed:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "job_reclaimed", "a scoped job was reclaimed during the soak",
                event_types=reclaimed,
            ),
        )
    bad_releases = sorted(
        event_types & {"released_retry", "released_failed", "released_cancelled"}
    )
    if bad_releases:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "job_retried_or_failed",
                "a scoped claim left the clean done path",
                event_types=bad_releases,
            ),
        )

    missing_jobs = sorted(str(job_id) for job_id in scoped_ids - set(jobs))
    if missing_jobs:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "claim_event_mismatch", "scoped jobs disappeared during observation",
                job_ids=missing_jobs,
            ),
        )

    claim_mismatches: list[str] = []
    missing_tokens: list[str] = []
    bad_job_states: list[str] = []
    claim_owners: set[str] = set()
    for job in jobs.values():
        claimed = job.status != "pending" or job.attempts > 0
        if claimed and job.claim_token is None:
            missing_tokens.append(str(job.id))
        if job.attempts > 1 or job.status in {"failed", "cancelled", "cancelling"}:
            bad_job_states.append(str(job.id))
        if not claimed or job.claim_token is None:
            continue
        job_events = events_by_job.get(job.id, [])
        claimed_events = [
            event
            for event in job_events
            if event.event_type == "claimed" and event.claim_token == job.claim_token
        ]
        done_events = [
            event
            for event in job_events
            if event.event_type == "released_done" and event.claim_token == job.claim_token
        ]
        expected_done = 1 if job.status == "done" else 0
        owner_identity_ok = (
            job.claimed_by is not None
            and job.claimed_by.endswith(f"@{scope.expected_git_sha}")
            and job.claimed_by.split(":", 1)[0] in scope.participant_hosts
            and len(claimed_events) == 1
            and claimed_events[0].owner == job.claimed_by
        )
        if (
            len(claimed_events) != 1
            or len(done_events) != expected_done
            or not owner_identity_ok
        ):
            claim_mismatches.append(str(job.id))
        for event in claimed_events:
            if event.owner:
                claim_owners.add(event.owner)
    if missing_tokens:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "running_without_token", "claimed job does not retain its lease token",
                job_ids=sorted(missing_tokens),
            ),
        )
    if bad_job_states:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "job_retried_or_failed", "scoped job retried, failed, or cancelled",
                job_ids=sorted(bad_job_states),
            ),
        )
    if claim_mismatches:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "claim_event_mismatch", "claim/release ledger differs from retained token",
                job_ids=sorted(claim_mismatches),
            ),
        )

    completed = len(jobs) == len(scoped_ids) and all(
        job.status == "done" for job in jobs.values()
    )
    running_peak = max(
        [sum(job.status == "running" for job in raw.jobs)]
        + [sum(job.status == "running" for job in sample.jobs) for sample in previous_samples]
    )
    required_owners = (
        math.ceil(len(scope.job_ids) / scope.worker_concurrency)
        if scope.worker_concurrency > 0
        else len(scope.job_ids) + 1
    )
    if completed and (
        running_peak < scope.target_running or len(claim_owners) < required_owners
    ):
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "claim_owner_underdistributed",
                "stage did not prove its target parallelism and owner distribution",
                observed_running_peak=running_peak,
                target_running=scope.target_running,
                distinct_claim_owners=len(claim_owners),
                required_claim_owners=required_owners,
            ),
        )

    # Phase rows are independently fenced.  Check orphan/duplicate/token
    # invariants on every sample, but require the full set only at terminal.
    orphaned = sorted(
        f"{phase.job_id}:{phase.phase_name}"
        for phase in raw.phases
        if phase.job_id not in scoped_ids
    )
    if orphaned:
        _append_runtime_finding(
            findings,
            _runtime_hard("orphan_phase", "phase row belongs outside exact scope", rows=orphaned),
        )
    phase_keys: set[tuple[UUID, str]] = set()
    order_keys: set[tuple[UUID, int]] = set()
    duplicate_rows: list[str] = []
    token_mismatches: list[str] = []
    phases_by_job: dict[UUID, list[PhaseSnapshot]] = {job_id: [] for job_id in scoped_ids}
    for phase in raw.phases:
        if phase.job_id not in scoped_ids:
            continue
        name_key = (phase.job_id, phase.phase_name)
        order_key = (phase.job_id, phase.phase_order)
        if name_key in phase_keys or order_key in order_keys:
            duplicate_rows.append(f"{phase.job_id}:{phase.phase_name}")
        phase_keys.add(name_key)
        order_keys.add(order_key)
        phases_by_job[phase.job_id].append(phase)
        job = jobs.get(phase.job_id)
        if job is None or phase.claim_token != job.claim_token:
            token_mismatches.append(f"{phase.job_id}:{phase.phase_name}")
    if duplicate_rows:
        _append_runtime_finding(
            findings,
            _runtime_hard("duplicate_phase", "duplicate phase name or order exists", rows=sorted(duplicate_rows)),
        )
    if token_mismatches:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "phase_token_mismatch", "phase token differs from retained job token",
                rows=sorted(token_mismatches),
            ),
        )

    incomplete: list[str] = []
    if completed:
        for job in jobs.values():
            try:
                content_phases = (
                    list(job.selected_phases)
                    if job.selected_phases is not None
                    else flows.flow_for(job.subject or "")
                )
            except (KeyError, ValueError):
                incomplete.append(str(job.id))
                continue
            expected_names = {"extract", *content_phases}
            observed = phases_by_job.get(job.id, [])
            observed_names = {phase.phase_name for phase in observed}
            if observed_names != expected_names or any(
                phase.status != "done" for phase in observed
            ):
                incomplete.append(str(job.id))
    if incomplete:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "phase_set_incomplete", "terminal job lacks its exact all-done phase set",
                job_ids=sorted(incomplete),
            ),
        )

    # A single stale sample can be scheduler jitter.  Two consecutive stale
    # samples are the hard condition promised by the plan.
    runtime_drift: list[str] = []
    attested_pc_ids: dict[str, str] = {}
    for worker in attestation.workers:
        if worker.hostname in attested_pc_ids:
            runtime_drift.append(worker.hostname)
        attested_pc_ids[worker.hostname] = worker.pc_id
    if (
        attestation.scope_sha256 != sha256_canonical(scope)
        or set(attested_pc_ids) != set(scope.participant_hosts)
    ):
        runtime_drift.append("attestation")
    for hostname in scope.participant_hosts:
        live_rows = [
            worker
            for worker in raw.workers
            if worker.hostname == hostname
            and worker.status == "online"
            and 0
            <= _age_seconds(raw.observed_at, worker.last_heartbeat)
            <= scope.heartbeat_max_age_seconds
        ]
        if len(live_rows) != 1:
            # Absence is handled by the two-sample heartbeat rule.  Multiple
            # fresh processes, however, violate the one-process attestation
            # immediately and cannot be treated as ordinary jitter.
            if len(live_rows) > 1:
                runtime_drift.append(hostname)
            continue
        worker = live_rows[0]
        if (
            worker.pc_id != attested_pc_ids.get(hostname)
            or worker.git_sha != scope.expected_git_sha
            or worker.code_version != scope.expected_code_version
            or not worker.can_gemini_api
        ):
            runtime_drift.append(hostname)
    if runtime_drift:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "worker_runtime_drift",
                "participant process identity/capability drifted after preflight",
                hosts=sorted(runtime_drift),
            ),
        )

    stale_now = _heartbeat_stale_hosts(scope, raw)
    if stale_now and previous_samples:
        stale_before = _heartbeat_stale_hosts(scope, previous_samples[-1])
        repeated = sorted(stale_now & stale_before)
        if repeated:
            _append_runtime_finding(
                findings,
                _runtime_hard(
                    "heartbeat_stale", "participant heartbeat stale for two samples",
                    hosts=repeated,
                ),
            )

    if raw.db.idle_in_transaction:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "db_idle_in_transaction", "database has idle-in-transaction sessions",
                sessions=raw.db.idle_in_transaction,
            ),
        )
    if raw.db.server_waits:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "db_server_wait", "database has non-client waits",
                waits=raw.db.server_waits,
            ),
        )
    if previous_samples and (
        raw.db.total_connections >= scope.db_hard_stop_connection_limit
        and previous_samples[-1].db.total_connections
        >= scope.db_hard_stop_connection_limit
    ):
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "db_connection_hard_stop", "connection hard threshold held for two samples",
                previous=previous_samples[-1].db.total_connections,
                current=raw.db.total_connections,
                threshold=scope.db_hard_stop_connection_limit,
            ),
        )

    active_slots = sum(slot.slot_count for slot in raw.credential_slots)
    if active_slots > scope.credential_max_concurrent_gemini:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "credential_slot_exhausted", "active credential slots exceed configured cap",
                active_slots=active_slots,
                limit=scope.credential_max_concurrent_gemini,
            ),
        )

    error_sources: list[tuple[str, str]] = []
    for job in raw.jobs:
        for field_name, value in (
            ("error_message", job.error_message),
            ("last_error", job.last_error),
        ):
            if value:
                error_sources.append((f"job:{job.id}:{field_name}", value))
    for phase in raw.phases:
        if phase.error_message:
            error_sources.append(
                (f"phase:{phase.job_id}:{phase.phase_name}", phase.error_message)
            )
    failed_usage_without_detail: list[dict[str, str | int]] = []
    for index, usage in enumerate(raw.usages):
        if usage.success:
            continue
        if usage.error_message and usage.error_message.strip():
            error_sources.append((f"usage:{index}:{usage.operation}", usage.error_message))
        else:
            failed_usage_without_detail.append(
                {
                    "source": f"usage:{index}:{usage.operation}",
                    "class": "missing_error_text",
                }
            )
    classified = [
        (source, category)
        for source, message in error_sources
        if (category := classify_error(message)) is not None
    ]
    if any(category is ErrorClass.SLOT_EXHAUSTION for _, category in classified):
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "credential_slot_exhausted", "credential-slot wait exhausted",
                sources=[
                    source
                    for source, category in classified
                    if category is ErrorClass.SLOT_EXHAUSTION
                ],
            ),
        )
    provider_errors: list[dict[str, str | int]] = [
        {"source": source, "class": category.value}
        for source, category in classified
        if category is not ErrorClass.SLOT_EXHAUSTION
    ]
    provider_errors.extend(failed_usage_without_detail)
    if provider_errors:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "provider_or_auth_error", "provider/auth/transport failure occurred",
                errors=provider_errors,
            ),
        )

    priced = price_scoped_usage(raw.usages)
    invalid_usage: list[int] = []
    transport_mismatches: list[dict[str, str]] = []
    routing_mismatches: list[dict[str, str | None]] = []
    for index, (usage, priced_row) in enumerate(zip(raw.usages, priced.rows, strict=True)):
        if usage.provider != "gemini" or usage.auth_mode != "api":
            transport_mismatches.append(
                {
                    "operation": usage.operation,
                    "observed_provider": usage.provider,
                    "observed_auth_mode": usage.auth_mode,
                }
            )
        is_expected_transport = usage.provider == "gemini" and usage.auth_mode == "api"
        if usage.success and is_expected_transport and (
            usage.total_tokens <= 0
            or usage.prompt_tokens + usage.output_tokens + usage.cached_tokens <= 0
            or (usage.provider, usage.model_name or "") not in pricing.PRICE_MAP
            or priced_row.cost_usd <= 0
        ):
            invalid_usage.append(index)
        expected_model = _expected_model(scope, usage.operation)
        if expected_model is None or usage.model_name != expected_model:
            routing_mismatches.append(
                {
                    "operation": usage.operation,
                    "expected_model": expected_model,
                    "observed_model": usage.model_name,
                }
            )
    if completed and not raw.usages:
        invalid_usage.append(-1)
    if invalid_usage:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "unpriced_or_tokenless_usage",
                "successful API usage is absent, unpriced, or tokenless",
                row_indexes=invalid_usage,
            ),
        )
    if transport_mismatches:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "operation_transport_mismatch",
                "scoped operation did not use the pinned Gemini API transport",
                rows=transport_mismatches,
            ),
        )
    if routing_mismatches:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "operation_model_mismatch", "API operation used an unexpected model",
                rows=routing_mismatches,
            ),
        )
    if priced.total_usd >= scope.approved_incremental_cost_usd:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "incremental_cost_cap", "scoped usage reached the approved hard cap",
                cost_usd=str(priced.total_usd),
                cap_usd=str(scope.approved_incremental_cost_usd),
            ),
        )

    archived = sorted(str(job.id) for job in raw.jobs if job.notion_archived_at is not None)
    if archived:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "unexpected_notion_archive", "scoped job was written to Notion",
                job_ids=archived,
            ),
        )
    missing_skip = sorted(
        str(job.id)
        for job in raw.jobs
        if job.status == "done" and not (job.notion_skip_reason or "").strip()
    )
    if missing_skip:
        _append_runtime_finding(
            findings,
            _runtime_hard(
                "notion_outcome_missing", "done job has neither archive nor skip outcome",
                job_ids=missing_skip,
            ),
        )

    major_rows = sorted(
        f"{phase.job_id}:{phase.phase_name}:{phase.judge_status}"
        for phase in raw.phases
        if phase.judge_status in {"major_shipped", "major_regen_failed"}
    )
    if major_rows:
        _append_runtime_finding(
            findings,
            _runtime_stage_failure(
                "quality_major_shipped", "judge left a major quality failure",
                rows=major_rows,
            ),
        )
    solver_rows = sorted(
        f"{phase.job_id}:{phase.phase_name}:{phase.solver_status}"
        for phase in raw.phases
        if phase.solver_status and "mismatch" in phase.solver_status.lower()
    )
    if solver_rows:
        _append_runtime_finding(
            findings,
            _runtime_stage_failure(
                "solver_mismatch", "answer-key solver left a mismatch",
                rows=solver_rows,
            ),
        )
    corrupted = sorted(
        f"{phase.job_id}:{phase.phase_name}"
        for phase in raw.phases
        if any("corrupt" in warning.lower() for warning in (phase.validation_warnings or []))
    )
    if corrupted:
        _append_runtime_finding(
            findings,
            _runtime_stage_failure(
                "validation_corruption", "validation reported corrupt output",
                rows=corrupted,
            ),
        )
    return findings


def evaluate_preflight(
    scope: SoakScope,
    attestation: FleetAttestation,
    raw: RawSnapshot,
) -> list[Finding]:
    """Evaluate the entire initial gate without I/O; every drift fails closed."""
    findings: list[Finding] = []
    schema_ok = (
        raw.transaction_read_only == "on"
        and raw.schema_state.revision == scope.expected_db_revision
        and raw.schema_state.ledger_table
        and raw.schema_state.job_claim_token
        and raw.schema_state.phase_claim_token
    )
    if not schema_ok:
        findings.append(_finding(
            "schema_revision_mismatch",
            "database revision or fenced-lease schema primitives differ from scope",
            expected_revision=scope.expected_db_revision,
            revision=raw.schema_state.revision,
            ledger_table=raw.schema_state.ledger_table,
            job_claim_token=raw.schema_state.job_claim_token,
            phase_claim_token=raw.schema_state.phase_claim_token,
            transaction_read_only=raw.transaction_read_only,
        ))

    expected_jobs = set(scope.job_ids)
    jobs_by_id: dict[UUID, list[JobSnapshot]] = {}
    for job in raw.jobs:
        jobs_by_id.setdefault(job.id, []).append(job)
    missing = sorted(str(job_id) for job_id in expected_jobs if job_id not in jobs_by_id)
    duplicates = sorted(str(job_id) for job_id, rows in jobs_by_id.items() if len(rows) != 1)
    if missing or duplicates or set(jobs_by_id) - expected_jobs:
        findings.append(_finding(
            "scope_job_missing", "scoped jobs are missing, duplicated, or unexpected",
            missing=missing, duplicates=duplicates,
            unexpected=sorted(str(item) for item in set(jobs_by_id) - expected_jobs),
        ))
    allowed_batches = set(scope.batch_ids)
    wrong_batch = sorted(str(job.id) for job in raw.jobs if (
        job.batch_id not in allowed_batches
        or job.batch_book_id != job.book_id
        or str(job.book_id) not in scope.required_book_sha256
    ))
    observed_batches = {job.batch_id for job in raw.jobs if job.batch_id is not None}
    if observed_batches != allowed_batches and not wrong_batch:
        wrong_batch = sorted(str(item) for item in allowed_batches ^ observed_batches)
    if wrong_batch:
        findings.append(_finding(
            "scope_job_wrong_batch", "scoped job belongs to a foreign batch",
            job_ids=wrong_batch,
        ))
    non_pristine = sorted(
        str(job.id)
        for job in raw.jobs
        if job.status != "pending"
        or job.attempts != 0
        or job.claim_token is not None
        or job.created_at < scope.since
        or job.phase_count != 0
        or job.usage_count != 0
        or job.lease_count != 0
    )
    if non_pristine:
        findings.append(_finding(
            "scope_job_not_pristine", "scoped jobs already carry execution state",
            job_ids=non_pristine,
        ))
    if raw.unrelated_active_jobs:
        findings.append(_finding(
            "unrelated_active_queue_not_empty",
            "unrelated pending/running/cancelling jobs exist",
            jobs=[job.model_dump(mode="json") for job in raw.unrelated_active_jobs],
        ))

    expected_pause = f"lease-soak-staging:{scope.run_id}"
    if raw.budget.api_paused_reason != expected_pause:
        findings.append(_finding(
            "staging_pause_missing_or_foreign",
            "fleet pause is missing or belongs to another operation",
            expected=expected_pause, observed=raw.budget.api_paused_reason,
        ))
    if raw.budget.min_worker_version != scope.expected_code_version:
        findings.append(_finding(
            "version_floor_mismatch", "worker version floor does not match soak identity",
            expected=scope.expected_code_version,
            observed=raw.budget.min_worker_version,
        ))

    expected_scope_hash = sha256_canonical(scope)
    attestation_age = _age_seconds(raw.observed_at, attestation.observed_at)
    stale_workers = sorted(
        worker.hostname
        for worker in attestation.workers
        if worker.scope_sha256 != expected_scope_hash
        or _age_seconds(raw.observed_at, worker.observed_at) < 0
        or _age_seconds(raw.observed_at, worker.observed_at)
        > scope.attestation_max_age_seconds
    )
    if (
        attestation.scope_sha256 != expected_scope_hash
        or attestation_age < 0
        or attestation_age > scope.attestation_max_age_seconds
        or stale_workers
    ):
        findings.append(_finding(
            "worker_attestation_stale", "fleet attestation is stale or for another scope",
            age_seconds=attestation_age,
            scope_matches=attestation.scope_sha256 == expected_scope_hash,
            stale_workers=stale_workers,
        ))

    tombstones = set(raw.scrub_tombstones)
    claimable: dict[str, RegistryWorkerSnapshot] = {}
    for worker in raw.workers:
        age = _age_seconds(raw.observed_at, worker.last_heartbeat)
        if (
            0 <= age <= scope.heartbeat_max_age_seconds
            and worker.status == "online"
            and worker.can_gemini_api
            and worker.code_version is not None
            and worker.code_version >= (raw.budget.min_worker_version or 2**31)
            and worker.hostname not in tombstones
        ):
            claimable[worker.pc_id] = worker

    attested_by_pc = {worker.pc_id: worker for worker in attestation.workers}
    rogue = sorted(set(claimable) - set(attested_by_pc))
    if rogue:
        findings.append(_finding(
            "unattested_claimable_worker", "claimable workers exist outside attestation",
            pc_ids=rogue,
        ))
    missing_registry = sorted(set(attested_by_pc) - set(claimable))
    registry_by_pc = {worker.pc_id: worker for worker in raw.workers}
    missing_registry.extend(
        sorted(
            worker.pc_id
            for worker in attestation.workers
            if worker.pc_id in registry_by_pc
            and registry_by_pc[worker.pc_id].hostname != worker.hostname
        )
    )
    missing_registry = sorted(set(missing_registry))
    if missing_registry:
        findings.append(_finding(
            "worker_registry_missing", "attested worker is absent, stale, parked, or not claimable",
            pc_ids=missing_registry,
        ))
    if set(scope.participant_hosts) != {worker.hostname for worker in attestation.workers}:
        findings.append(_finding(
            "worker_registry_missing", "attested hostname set differs from the scope",
            expected=sorted(scope.participant_hosts),
            observed=sorted(worker.hostname for worker in attestation.workers),
        ))

    wrong_sha: list[str] = []
    wrong_config: list[str] = []
    wrong_credential: list[str] = []
    wrong_pdf: list[str] = []
    notion_present: list[str] = []
    expected_config = (
        scope.worker_concurrency,
        scope.agent_max_concurrency,
        scope.credential_max_concurrent_gemini,
        scope.credential_slot_wait_seconds,
        scope.structured_output_enabled,
        1,
    )
    for worker in attestation.workers:
        registry = raw.workers and next(
            (row for row in raw.workers if row.pc_id == worker.pc_id), None
        )
        if (
            worker.git_sha != scope.expected_git_sha
            or worker.code_version != scope.expected_code_version
            or (registry is not None and registry.git_sha != scope.expected_git_sha)
        ):
            wrong_sha.append(worker.hostname)
        observed_config = (
            worker.worker_concurrency,
            worker.agent_max_concurrency,
            worker.credential_max_concurrent_gemini,
            worker.credential_slot_wait_seconds,
            worker.structured_output_enabled,
            worker.process_count_for_host,
        )
        if (
            observed_config != expected_config
            or (
                scope.legacy_gemini_var_must_be_absent
                and worker.gemini_max_concurrency_present
            )
        ):
            wrong_config.append(worker.hostname)
        if (
            worker.credential_fingerprint != attestation.credential_fingerprint
            or worker.credential_fingerprint != attestation.workers[0].credential_fingerprint
        ):
            wrong_credential.append(worker.hostname)
        for book_id, expected_hash in scope.required_book_sha256.items():
            if worker.pdf_sha256_by_book.get(book_id) != expected_hash:
                wrong_pdf.append(f"{worker.hostname}:{book_id}")
        if set(worker.notion_mapping_keys) & set(scope.forbidden_notion_mapping_keys):
            notion_present.append(worker.hostname)
    if wrong_sha:
        findings.append(_finding("worker_sha_mismatch", "worker code identity differs", hosts=wrong_sha))
    if wrong_config:
        findings.append(_finding("worker_config_mismatch", "worker process contract differs", hosts=wrong_config))
    if wrong_credential:
        findings.append(_finding("credential_fingerprint_mismatch", "credential fingerprints differ", hosts=wrong_credential))
    if wrong_pdf:
        findings.append(_finding("pdf_missing_or_mismatch", "worker PDF is absent or differs", entries=wrong_pdf))
    if notion_present:
        findings.append(_finding("notion_mapping_present", "forbidden Notion mappings are present", hosts=notion_present))

    required_processes = (
        math.ceil(scope.target_running / scope.worker_concurrency)
        if scope.worker_concurrency > 0
        else scope.target_running + 1
    )
    attested_hosts = {worker.hostname for worker in attestation.workers}
    if len(attested_by_pc) < required_processes or len(attested_hosts) < required_processes:
        findings.append(_finding(
            "worker_config_mismatch", "insufficient distinct worker processes for target",
            required=required_processes, processes=len(attested_by_pc), hosts=len(attested_hosts),
        ))

    checksum_drift: list[str] = []
    for book_id, expected_hash in scope.required_book_sha256.items():
        row = raw.books.get(book_id)
        if row is None or row.content_sha256 != expected_hash:
            checksum_drift.append(book_id)
    if checksum_drift:
        findings.append(_finding(
            "book_checksum_scope_mismatch", "authoritative book checksum differs from scope",
            book_ids=checksum_drift,
        ))

    if raw.db.total_connections > scope.db_preflight_connection_limit:
        findings.append(_finding(
            "db_connection_baseline_high", "database connection baseline exceeds preflight cap",
            observed=raw.db.total_connections,
            limit=scope.db_preflight_connection_limit,
        ))
    if raw.db.idle_in_transaction:
        findings.append(_finding(
            "db_idle_in_transaction", "idle-in-transaction sessions exist",
            sessions=raw.db.idle_in_transaction,
        ))
    if not 0 < raw.db.idle_in_transaction_timeout_ms <= 300_000:
        findings.append(_finding(
            "db_idle_in_transaction_timeout_unsafe",
            "effective idle-in-transaction timeout is disabled or exceeds five minutes",
            observed_ms=raw.db.idle_in_transaction_timeout_ms,
            maximum_ms=300_000,
        ))
    if raw.db.server_waits:
        findings.append(_finding(
            "db_server_wait", "non-client database waits exist", waits=raw.db.server_waits,
        ))
    if raw.credential_slots:
        findings.append(_finding(
            "credential_slot_baseline_nonzero", "credential slots are not empty",
            slots=[slot.model_dump(mode="json") for slot in raw.credential_slots],
        ))
    prior_cost = _fleet_cost(raw.fleet_usages_24h)
    projected = prior_cost + scope.approved_incremental_cost_usd
    unpriced = []
    for row in raw.fleet_usages_24h:
        if row.auth_mode != "api":
            continue
        resolved = row.model_name or pricing.agent_models.default_model(row.provider)
        if row.provider != "clodex" and (row.provider, resolved) not in pricing.PRICE_MAP:
            unpriced.append(f"{row.provider}:{resolved}")
    if projected > scope.fleet_cost_limit_usd or unpriced:
        findings.append(_finding(
            "fleet_cost_envelope_exceeded", "trailing fleet cost plus approved spend exceeds cap",
            trailing_24h_usd=str(prior_cost), projected_usd=str(projected),
            limit_usd=str(scope.fleet_cost_limit_usd),
            unpriced=sorted(set(unpriced)),
        ))
    return findings


def assert_scratch_database_url(database_url: str) -> None:
    """Fail before any test fixture can connect to a non-disposable database."""
    try:
        database = make_url(database_url).database or ""
    except Exception as exc:
        raise RuntimeError("scratch database required") from exc
    normalized = database.lower()
    if not normalized or ("scratch" not in normalized and not normalized.endswith("_test")):
        raise RuntimeError("scratch database required")


def _mapping_dicts(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def sanitize_query_prefix(value: str | None) -> str:
    """Keep SQL shape for diagnosis while removing literal-bearing evidence."""
    if not value:
        return ""
    sanitized = re.sub(r"'(?:''|[^'])*'", "'?'", value)
    sanitized = re.sub(r"(?i)\bbearer\s+\S+", "Bearer ?", sanitized)
    sanitized = re.sub(
        r"(?i)\b(?:postgres(?:ql)?|https?)://\S+", "<redacted-url>", sanitized
    )
    return sanitized[:160]


def sanitize_credential_identity(value: str) -> str:
    if _PLAIN_GEMINI_FP_RE.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"credential:sha256:{digest}"


def _sanitize_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["query_prefix"] = sanitize_query_prefix(row.get("query_prefix"))
    return rows


def _usage_from_row(row: Mapping[str, Any]) -> UsageSnapshot:
    return UsageSnapshot(
        job_id=row.get("job_id"),
        provider=row["provider"],
        operation=row.get("operation") or "",
        model_name=row.get("model_name"),
        auth_mode=row["auth_mode"],
        prompt_tokens=int(row.get("prompt_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        cached_tokens=int(row.get("cached_tokens") or 0),
        cache_creation_tokens=int(row.get("cache_creation_tokens") or 0),
        total_tokens=int(row.get("total_tokens") or 0),
        success=bool(row.get("success")),
        error_message=row.get("error_message"),
    )


class SqlSoakReadStore:
    """One-connection PostgreSQL snapshot store; all exposed connections are read-only."""

    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "application_name": f"hcga-soak:{os.getpid()}",
                    "idle_in_transaction_session_timeout": "300000",
                }
            },
        )

    @asynccontextmanager
    async def read_connection(self):
        async with self.engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                yield conn

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def collect(self, scope: SoakScope) -> RawSnapshot:
        params = {
            "job_ids": list(scope.job_ids),
            "book_ids": [UUID(item) for item in scope.required_book_sha256],
            "since": scope.since,
        }
        async with self.read_connection() as conn:
            observed_at = await conn.scalar(text("SELECT clock_timestamp()"))
            transaction_read_only = await conn.scalar(
                text("SELECT current_setting('transaction_read_only')")
            )
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            schema_row = (
                await conn.execute(text("""
                    SELECT
                      to_regclass('public.job_lease_events') IS NOT NULL AS ledger_table,
                      EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='homework_jobs'
                          AND column_name='claim_token'
                      ) AS job_claim_token,
                      EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='phase_outputs'
                          AND column_name='claim_token'
                      ) AS phase_claim_token
                """))
            ).mappings().one()
            budget_row = (
                await conn.execute(text("""
                    SELECT api_paused_reason, min_worker_version
                    FROM budget_state WHERE id = 1
                """))
            ).mappings().one_or_none()

            job_rows = _mapping_dicts(await conn.execute(text("""
                SELECT j.id, j.batch_id, j.book_id, b.book_id AS batch_book_id,
                       j.subject, j.selected_phases, j.status, j.attempts,
                       j.claim_token, j.claimed_by, j.created_at,
                       j.error_message, j.last_error,
                       j.notion_archived_at, j.notion_skip_reason,
                       (SELECT count(*) FROM phase_outputs p WHERE p.job_id=j.id) AS phase_count,
                       (SELECT count(*) FROM agent_usages u WHERE u.homework_job_id=j.id) AS usage_count,
                       (SELECT count(*) FROM job_lease_events e WHERE e.job_id=j.id) AS lease_count
                FROM homework_jobs j
                LEFT JOIN batches b ON b.id = j.batch_id
                WHERE j.id = ANY(CAST(:job_ids AS uuid[]))
                ORDER BY j.id
            """), params))
            book_rows = _mapping_dicts(await conn.execute(text("""
                SELECT id, content_sha256 FROM books
                WHERE id = ANY(CAST(:book_ids AS uuid[]))
                ORDER BY id
            """), params))
            unrelated = _mapping_dicts(await conn.execute(text("""
                SELECT id, status FROM homework_jobs
                WHERE status IN ('pending','running','cancelling')
                  AND NOT (id = ANY(CAST(:job_ids AS uuid[])))
                ORDER BY id
            """), params))
            worker_rows = _mapping_dicts(await conn.execute(text("""
                SELECT pc_id,
                       split_part(pc_id, ':', 1) AS hostname,
                       last_heartbeat, status,
                       capabilities->>'git_sha' AS git_sha,
                       CASE WHEN (capabilities->>'code_version') ~ '^[0-9]+$'
                            THEN (capabilities->>'code_version')::integer END AS code_version,
                       COALESCE((capabilities->'api'->>'gemini')::boolean, false)
                         AS can_gemini_api
                FROM workers
                ORDER BY pc_id
            """)))
            tombstone_rows = _mapping_dicts(await conn.execute(text("""
                SELECT hostname FROM sa_key_assignments
                WHERE scrub_requested_at IS NOT NULL
                ORDER BY hostname
            """)))

            settings_rows = _mapping_dicts(await conn.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN (
                  'max_connections', 'superuser_reserved_connections'
                )
            """)))
            settings_map = {row["name"]: row["setting"] for row in settings_rows}
            effective_idle_timeout_ms = int(await conn.scalar(text("""
                SELECT (
                  EXTRACT(epoch FROM current_setting(
                    'idle_in_transaction_session_timeout'
                  )::interval) * 1000
                )::bigint
            """)))
            backend_pid = await conn.scalar(text("SELECT pg_backend_pid()"))
            total_connections = int(await conn.scalar(text("SELECT count(*) FROM pg_stat_activity")))
            activity_params = {"controller_pid": backend_pid}
            idle_rows = _sanitize_activity_rows(_mapping_dicts(await conn.execute(text("""
                SELECT pid, application_name, client_addr::text AS client_addr,
                       EXTRACT(epoch FROM clock_timestamp()-state_change)::double precision AS age_s,
                       left(query, 160) AS query_prefix
                FROM pg_stat_activity
                WHERE pid <> :controller_pid AND state = 'idle in transaction'
                ORDER BY pid
            """), activity_params)))
            wait_rows = _sanitize_activity_rows(_mapping_dicts(await conn.execute(text("""
                SELECT pid, application_name, client_addr::text AS client_addr,
                       wait_event_type, wait_event,
                       left(query, 160) AS query_prefix
                FROM pg_stat_activity
                WHERE pid <> :controller_pid
                  AND wait_event_type IS NOT NULL
                  AND wait_event_type <> 'Client'
                ORDER BY pid
            """), activity_params)))
            slot_rows = _mapping_dicts(await conn.execute(text("""
                SELECT credential, pc_id, min(acquired_at) AS acquired_at,
                       count(*)::integer AS slot_count
                FROM credential_slots
                GROUP BY credential, pc_id
                ORDER BY credential, pc_id
            """)))
            for row in slot_rows:
                row["credential"] = sanitize_credential_identity(row["credential"])

            lease_rows = _mapping_dicts(await conn.execute(text("""
                SELECT job_id, claim_token, event_type, owner, created_at
                FROM job_lease_events
                WHERE job_id = ANY(CAST(:job_ids AS uuid[])) AND created_at >= :since
                ORDER BY created_at, id
            """), params))
            phase_rows = _mapping_dicts(await conn.execute(text("""
                SELECT job_id, phase_name, phase_order, status, claim_token,
                       error_message, validation_warnings, judge_status, solver_status
                FROM phase_outputs
                WHERE job_id = ANY(CAST(:job_ids AS uuid[]))
                ORDER BY job_id, phase_order
            """), params))
            usage_rows = _mapping_dicts(await conn.execute(text("""
                SELECT homework_job_id AS job_id, provider, operation, model_name, auth_mode,
                       prompt_tokens, output_tokens, cached_tokens,
                       cache_creation_tokens, total_tokens, success, error_message
                FROM agent_usages
                WHERE homework_job_id = ANY(CAST(:job_ids AS uuid[]))
                  AND created_at >= :since
                ORDER BY created_at, id
            """), params))
            fleet_params = {"cutoff": observed_at - timedelta(hours=24)}
            fleet_usage_rows = _mapping_dicts(await conn.execute(text("""
                SELECT homework_job_id AS job_id, provider, operation, model_name, auth_mode,
                       prompt_tokens, output_tokens, cached_tokens,
                       cache_creation_tokens, total_tokens, success, error_message
                FROM agent_usages
                WHERE auth_mode = 'api' AND started_at >= :cutoff
                ORDER BY started_at, id
            """), fleet_params))

        return RawSnapshot(
            observed_at=observed_at,
            transaction_read_only=transaction_read_only,
            schema=SchemaSnapshot(
                revision=revision,
                ledger_table=bool(schema_row["ledger_table"]),
                job_claim_token=bool(schema_row["job_claim_token"]),
                phase_claim_token=bool(schema_row["phase_claim_token"]),
            ),
            budget=BudgetSnapshot(
                api_paused_reason=(budget_row or {}).get("api_paused_reason"),
                min_worker_version=(budget_row or {}).get("min_worker_version"),
            ),
            jobs=[JobSnapshot.model_validate(row) for row in job_rows],
            books={str(row["id"]): BookSnapshot.model_validate(row) for row in book_rows},
            unrelated_active_jobs=[ActiveJobSnapshot.model_validate(row) for row in unrelated],
            workers=[RegistryWorkerSnapshot.model_validate(row) for row in worker_rows],
            scrub_tombstones=[str(row["hostname"]) for row in tombstone_rows],
            db=DatabaseSnapshot(
                total_connections=total_connections,
                max_connections=int(settings_map.get("max_connections", 0)),
                superuser_reserved_connections=int(
                    settings_map.get("superuser_reserved_connections", 0)
                ),
                idle_in_transaction_timeout_ms=effective_idle_timeout_ms,
                idle_in_transaction=idle_rows,
                server_waits=wait_rows,
            ),
            credential_slots=[CredentialSlotSnapshot.model_validate(row) for row in slot_rows],
            lease_events=[LeaseEventSnapshot.model_validate(row) for row in lease_rows],
            phases=[PhaseSnapshot.model_validate(row) for row in phase_rows],
            usages=[_usage_from_row(row) for row in usage_rows],
            fleet_usages_24h=[_usage_from_row(row) for row in fleet_usage_rows],
        )


class EffectiveWorkerContract(BaseModel):
    """Only non-secret target-process settings needed by the soak contract."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    worker_concurrency: int = Field(
        default=Settings.model_fields["worker_concurrency"].default,
        alias="WORKER_CONCURRENCY",
        ge=0,
    )
    agent_max_concurrency: int = Field(
        default=Settings.model_fields["agent_max_concurrency"].default,
        alias="AGENT_MAX_CONCURRENCY",
        gt=0,
    )
    credential_max_concurrent_gemini: int = Field(
        default=Settings.model_fields["credential_max_concurrent_gemini"].default,
        alias="CREDENTIAL_MAX_CONCURRENT_GEMINI",
        ge=0,
    )
    credential_slot_wait_seconds: int = Field(
        default=Settings.model_fields["credential_slot_wait_seconds"].default,
        alias="CREDENTIAL_SLOT_WAIT_SECONDS",
        ge=1,
    )
    structured_output_enabled: bool = Field(
        default=Settings.model_fields["structured_output_enabled"].default,
        alias="STRUCTURED_OUTPUT_ENABLED",
    )
    var_dir: str = Field(
        default=Settings.model_fields["var_dir"].default,
        alias="VAR_DIR",
    )
    notion_subject_pages: dict[str, str | dict[str, str]] = Field(
        default_factory=dict,
        alias="NOTION_SUBJECT_PAGES",
    )


class ProcessView(Protocol):
    pid: int

    def status(self) -> str: ...

    def cmdline(self) -> list[str]: ...

    def environ(self) -> Mapping[str, str]: ...

    def cwd(self) -> str: ...


def canonical_json(model: BaseModel | Mapping[str, Any]) -> str:
    if isinstance(model, BaseModel):
        value = model.model_dump(mode="json")
    else:
        value = dict(model)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_canonical(model: BaseModel | Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()


def _is_secret_field(name: str) -> bool:
    lower = name.lower()
    return lower not in _SAFE_REDACTED_FIELDS and any(
        part in lower for part in _SECRET_FIELD_PARTS
    )


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact(item)
            for key, item in value.items()
            if not _is_secret_field(str(key))
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def redacted_model_dump(model: BaseModel) -> dict[str, Any]:
    return _redact(model.model_dump(mode="json"))


def load_scope(
    source: Path | Literal["-"] | str,
    *,
    stdin: TextIO = sys.stdin,
) -> SoakScope:
    if str(source) == "-":
        encoded = stdin.read()
    else:
        encoded = Path(source).read_text(encoding="utf-8")
    return SoakScope.model_validate_json(encoded)


def load_attestation(path: Path | str) -> FleetAttestation:
    return FleetAttestation.model_validate_json(Path(path).read_text(encoding="utf-8"))


def effective_worker_contract(worker_env: Mapping[str, str]) -> EffectiveWorkerContract:
    selected: dict[str, Any] = {}
    for field in EffectiveWorkerContract.model_fields.values():
        alias = str(field.alias)
        if alias in worker_env:
            selected[alias] = worker_env[alias]
    notion = selected.get("NOTION_SUBJECT_PAGES")
    if isinstance(notion, str):
        selected["NOTION_SUBJECT_PAGES"] = json.loads(notion)
    return EffectiveWorkerContract.model_validate(selected)


def _process_argv(process: ProcessView) -> list[str]:
    try:
        return list(process.cmdline())
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return []


def discover_worker_processes(processes: Iterable[ProcessView]) -> list[ProcessView]:
    matches: list[ProcessView] = []
    python_names = {"python", "python3", "python.exe"}
    for process in processes:
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        argv = _process_argv(process)
        if not argv:
            continue
        executable = Path(argv[0]).name.lower()
        if executable not in python_names and not executable.startswith("python3."):
            continue
        if not any(
            argv[index] == "-m" and argv[index + 1] == "app.services.worker"
            for index in range(len(argv) - 1)
        ):
            continue
        matches.append(process)
    return matches


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_local_attestation(
    scope: SoakScope,
    *,
    hostname: str,
    processes: Iterable[ProcessView],
    now: datetime,
    git_identity: tuple[int | None, str | None] | None = None,
) -> WorkerAttestation:
    if hostname not in scope.participant_hosts:
        raise AttestationError(f"hostname {hostname!r} is not in participant_hosts")

    workers = discover_worker_processes(processes)
    if len(workers) != 1:
        raise AttestationError(
            f"expected exactly one worker process; discovered {len(workers)}"
        )
    process = workers[0]
    try:
        worker_env = dict(process.environ())
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        raise AttestationError("cannot read target worker environment") from exc
    try:
        worker_cwd = Path(process.cwd())
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
        raise AttestationError("cannot read target worker cwd") from exc

    try:
        contract = effective_worker_contract(worker_env)
    except (json.JSONDecodeError, ValidationError) as exc:
        # Pydantic/JSON diagnostics include the rejected input value.  Target
        # process values can contain Notion page IDs or other secrets, so the
        # public error is deliberately field-agnostic.
        raise AttestationError("target worker contract is invalid") from exc
    if not worker_env.get("GEMINI_API_KEY", "").strip():
        raise AttestationError("soak requires a plain Gemini API key")
    fingerprint = credential_id.credential_for("gemini", worker_env)
    if fingerprint is None or not _PLAIN_GEMINI_FP_RE.fullmatch(fingerprint):
        raise AttestationError("soak requires a plain Gemini API key fingerprint")

    detected_version, detected_sha = git_identity or code_version.detect(env=worker_env)
    if detected_version is None or detected_sha is None:
        raise AttestationError("cannot derive worker code version and git identity")

    var_dir = Path(contract.var_dir)
    if not var_dir.is_absolute():
        var_dir = worker_cwd / var_dir
    pdf_hashes: dict[str, str | None] = {}
    for book_id in sorted(scope.required_book_sha256):
        pdf_path = var_dir / "books" / book_id / "source.pdf"
        pdf_hashes[book_id] = _stream_sha256(pdf_path) if pdf_path.is_file() else None

    return WorkerAttestation(
        scope_sha256=sha256_canonical(scope),
        pc_id=f"{hostname}:{process.pid}@{detected_sha}",
        hostname=hostname,
        observed_at=now,
        git_sha=detected_sha,
        code_version=detected_version,
        worker_concurrency=contract.worker_concurrency,
        agent_max_concurrency=contract.agent_max_concurrency,
        credential_max_concurrent_gemini=contract.credential_max_concurrent_gemini,
        credential_slot_wait_seconds=contract.credential_slot_wait_seconds,
        gemini_max_concurrency_present="GEMINI_MAX_CONCURRENCY" in worker_env,
        structured_output_enabled=contract.structured_output_enabled,
        process_count_for_host=len(workers),
        credential_fingerprint=fingerprint,
        pdf_sha256_by_book=pdf_hashes,
        notion_mapping_keys=sorted(contract.notion_subject_pages),
    )


def aggregate_attestations(
    scope: SoakScope,
    workers: Iterable[WorkerAttestation],
    *,
    now: datetime,
) -> FleetAttestation:
    expected_scope_hash = sha256_canonical(scope)
    worker_list = list(workers)
    hostnames = [worker.hostname for worker in worker_list]
    pc_ids = [worker.pc_id for worker in worker_list]
    if len(hostnames) != len(set(hostnames)):
        raise AttestationError("duplicate hostname in local attestations")
    if len(pc_ids) != len(set(pc_ids)):
        raise AttestationError("duplicate pc_id in local attestations")
    if set(hostnames) != set(scope.participant_hosts):
        raise AttestationError("participant host set mismatch")

    fingerprints: set[str] = set()
    forbidden_notion = set(scope.forbidden_notion_mapping_keys)
    for worker in worker_list:
        if worker.scope_sha256 != expected_scope_hash:
            raise AttestationError(f"scope digest mismatch for {worker.hostname}")
        age = (_aware_utc(now, field_name="now") - worker.observed_at).total_seconds()
        if age < 0 or age > scope.attestation_max_age_seconds:
            raise AttestationError(f"stale attestation for {worker.hostname}")
        if (
            worker.git_sha != scope.expected_git_sha
            or worker.code_version != scope.expected_code_version
        ):
            raise AttestationError(f"final deployed identity mismatch for {worker.hostname}")
        if worker.process_count_for_host != 1:
            raise AttestationError(f"{worker.hostname} does not have exactly one worker process")
        expected_config = (
            scope.worker_concurrency,
            scope.agent_max_concurrency,
            scope.credential_max_concurrent_gemini,
            scope.credential_slot_wait_seconds,
            scope.structured_output_enabled,
        )
        actual_config = (
            worker.worker_concurrency,
            worker.agent_max_concurrency,
            worker.credential_max_concurrent_gemini,
            worker.credential_slot_wait_seconds,
            worker.structured_output_enabled,
        )
        if actual_config != expected_config:
            raise AttestationError(f"configuration mismatch for {worker.hostname}")
        if (
            scope.legacy_gemini_var_must_be_absent
            and worker.gemini_max_concurrency_present
        ):
            raise AttestationError(f"legacy Gemini concurrency variable on {worker.hostname}")
        fingerprints.add(worker.credential_fingerprint)
        if worker.pdf_sha256_by_book != scope.required_book_sha256:
            raise AttestationError(f"PDF hash mismatch for {worker.hostname}")
        leaked_keys = forbidden_notion.intersection(worker.notion_mapping_keys)
        if leaked_keys:
            raise AttestationError(
                f"forbidden Notion mapping present on {worker.hostname}: {sorted(leaked_keys)}"
            )
    if len(fingerprints) != 1:
        raise AttestationError("credential fingerprint mismatch across workers")

    ordered_workers = sorted(worker_list, key=lambda worker: (worker.hostname, worker.pc_id))
    input_digests = sorted(sha256_canonical(worker) for worker in worker_list)
    return FleetAttestation(
        scope_sha256=expected_scope_hash,
        observed_at=max(worker.observed_at for worker in worker_list),
        credential_fingerprint=next(iter(fingerprints)),
        input_artifact_sha256=input_digests,
        workers=ordered_workers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("attest-local")
    local.add_argument("--scope", required=True)

    aggregate = subparsers.add_parser("attest-aggregate")
    aggregate.add_argument("--scope", required=True)
    aggregate.add_argument("--input", action="append", required=True)

    for command in ("preflight", "watch"):
        child = subparsers.add_parser(command)
        child.add_argument("--scope", required=True)
        child.add_argument("--attestation", required=True)
        child.add_argument("--artifact-dir", required=True)
        if command == "watch":
            child.add_argument("--interval-seconds", type=float, default=2.0)
            child.add_argument("--arm-stop", action="store_true")
            child.add_argument("--confirm-arm")
    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "watch" and args.confirm_arm is not None and not args.arm_stop:
        parser.error("--confirm-arm requires --arm-stop")
    return args


def validate_arm_confirmation(args: argparse.Namespace, *, run_id: str) -> None:
    if not getattr(args, "arm_stop", False):
        return
    expected = f"lease-soak-stop:{run_id}"
    if getattr(args, "confirm_arm", None) != expected:
        raise SystemExit(f"--confirm-arm must equal {expected}")


def _default_process_source() -> Iterable[ProcessView]:
    return psutil.process_iter()


def main(
    argv: Sequence[str] | None = None,
    *,
    process_source: Callable[[], Iterable[ProcessView]] | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    hostname: str | None = None,
    now: Callable[[], datetime] | None = None,
    git_identity: Callable[[Mapping[str, str]], tuple[int | None, str | None]] | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    clock = now or (lambda: datetime.now(timezone.utc))

    if args.command == "attest-local":
        try:
            scope = load_scope(args.scope, stdin=stdin)
        except (ValidationError, json.JSONDecodeError, OSError):
            stderr.write("attestation failed: scope is invalid\n")
            return int(ExitCode.OPERATIONAL_ERROR)
        try:
            processes = list((process_source or _default_process_source)())
            workers = discover_worker_processes(processes)
            identity: tuple[int | None, str | None] | None = None
            if git_identity is not None:
                if len(workers) != 1:
                    raise AttestationError(
                        "expected exactly one worker process; "
                        f"discovered {len(workers)}"
                    )
                try:
                    env = dict(workers[0].environ())
                except (
                    psutil.AccessDenied,
                    psutil.NoSuchProcess,
                    psutil.ZombieProcess,
                ) as exc:
                    raise AttestationError(
                        "cannot read target worker environment"
                    ) from exc
                identity = git_identity(env)
            worker = build_local_attestation(
                scope,
                hostname=hostname or socket.gethostname(),
                processes=processes,
                now=clock(),
                git_identity=identity,
            )
        except AttestationError as exc:
            stderr.write(f"attestation failed: {exc}\n")
            return int(ExitCode.OPERATIONAL_ERROR)
        stdout.write(canonical_json(worker) + "\n")
        return int(ExitCode.PASS)

    if args.command == "attest-aggregate":
        try:
            scope = load_scope(args.scope, stdin=stdin)
        except (ValidationError, json.JSONDecodeError, OSError):
            stderr.write("attestation failed: scope is invalid\n")
            return int(ExitCode.OPERATIONAL_ERROR)
        try:
            workers = [
                WorkerAttestation.model_validate_json(
                    Path(path).read_text(encoding="utf-8")
                )
                for path in args.input
            ]
            fleet = aggregate_attestations(scope, workers, now=clock())
        except AttestationError as exc:
            stderr.write(f"attestation failed: {exc}\n")
            return int(ExitCode.OPERATIONAL_ERROR)
        except (ValidationError, json.JSONDecodeError, OSError):
            # Validation errors include rejected input values.  Never print a
            # malformed artifact because it may contain an attempted secret.
            stderr.write("attestation failed: input artifact is invalid\n")
            return int(ExitCode.OPERATIONAL_ERROR)
        stdout.write(canonical_json(fleet) + "\n")
        return int(ExitCode.PASS)

    # Task 3+ owns the database-backed commands.  Keeping these inert here is
    # stronger than a placeholder that might accidentally open live state.
    return int(ExitCode.INCOMPLETE)


if __name__ == "__main__":  # pragma: no cover - exercised through injected main
    raise SystemExit(main())
