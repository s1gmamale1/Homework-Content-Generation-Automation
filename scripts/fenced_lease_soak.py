#!/usr/bin/env python3
"""Fail-closed controller for a separately authorized fenced-lease soak.

Task 2 intentionally contains only immutable JSON contracts and local fleet
attestation.  It opens no database, network, or model-provider client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO
from uuid import UUID

import psutil
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import Settings
from app.services import code_version, credential_id


_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,44}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
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
        processes = list((process_source or _default_process_source)())
        workers = discover_worker_processes(processes)
        identity: tuple[int | None, str | None] | None = None
        if git_identity is not None:
            if len(workers) != 1:
                raise AttestationError(
                    f"expected exactly one worker process; discovered {len(workers)}"
                )
            try:
                env = dict(workers[0].environ())
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
                raise AttestationError("cannot read target worker environment") from exc
            identity = git_identity(env)
        try:
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
