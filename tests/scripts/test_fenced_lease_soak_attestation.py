from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import psutil
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services import credential_id
from scripts import fenced_lease_soak as soak
from tests.scripts.test_fenced_lease_soak_contracts import (
    BOOK,
    valid_scope_dict,
)


UTC_NOW = datetime(2026, 8, 10, 12, 5, tzinfo=timezone.utc)


def sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


@dataclass
class FakeProcess:
    pid: int
    argv: list[str]
    env: dict[str, str]
    working_dir: Path
    state: str = "running"
    environ_error: Exception | None = None

    def status(self) -> str:
        return self.state

    def cmdline(self) -> list[str]:
        return self.argv

    def environ(self) -> dict[str, str]:
        if self.environ_error:
            raise self.environ_error
        return self.env

    def cwd(self) -> str:
        return str(self.working_dir)


def scope_for(tmp_path: Path, hosts: list[str] | None = None) -> soak.SoakScope:
    raw = valid_scope_dict()
    raw["participant_hosts"] = hosts or ["Host-02"]
    raw["required_book_sha256"] = {str(BOOK): sha256_bytes(b"pdf")}
    return soak.SoakScope.model_validate(raw)


def worker_env(tmp_path: Path, **updates: str) -> dict[str, str]:
    result = {
        "DATABASE_URL": "postgresql+asyncpg://not-emitted@db/test",
        "GEMINI_API_KEY": "plain-secret-key",
        "WORKER_CONCURRENCY": "2",
        "AGENT_MAX_CONCURRENCY": "4",
        "CREDENTIAL_MAX_CONCURRENT_GEMINI": "32",
        "CREDENTIAL_SLOT_WAIT_SECONDS": "120",
        "STRUCTURED_OUTPUT_ENABLED": "false",
        "VAR_DIR": str(tmp_path),
        "NOTION_SUBJECT_PAGES": '{"matematika|5":"page-secret"}',
    }
    result.update(updates)
    return result


def make_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "books" / str(BOOK) / "source.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"pdf")
    return pdf


def process(
    tmp_path: Path,
    *,
    pid: int = 4242,
    hostname: str = "Host-02",
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    state: str = "running",
    environ_error: Exception | None = None,
) -> FakeProcess:
    del hostname
    return FakeProcess(
        pid=pid,
        argv=argv or ["python", "-m", "app.services.worker"],
        env=env or worker_env(tmp_path),
        working_dir=tmp_path,
        state=state,
        environ_error=environ_error,
    )


def build_valid_local_attestation(
    tmp_path: Path,
    *,
    hostname: str = "Host-02",
    processes: list[FakeProcess] | None = None,
    worker_environ: dict[str, str] | None = None,
) -> soak.WorkerAttestation:
    make_pdf(tmp_path)
    processes = processes or [process(tmp_path, env=worker_environ)]
    return soak.build_local_attestation(
        scope_for(tmp_path, [hostname]),
        hostname=hostname,
        processes=processes,
        now=UTC_NOW,
        git_identity=(1001, "fedcba9"),
    )


def test_local_attestation_reports_effective_config_and_registry_identity(tmp_path):
    worker = build_valid_local_attestation(tmp_path)
    assert worker.pc_id == "Host-02:4242@fedcba9"
    assert worker.process_count_for_host == 1
    assert worker.worker_concurrency == 2
    assert worker.agent_max_concurrency == 4
    assert worker.credential_max_concurrent_gemini == 32
    assert worker.credential_slot_wait_seconds == 120
    assert worker.pdf_sha256_by_book[str(BOOK)] == sha256_bytes(b"pdf")
    assert worker.notion_mapping_keys == ["matematika|5"]
    assert worker.scope_sha256 == soak.sha256_canonical(scope_for(tmp_path))


def test_target_worker_environment_wins_over_helper_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "99")
    env = worker_env(tmp_path, AGENT_MAX_CONCURRENCY="4")
    worker = build_valid_local_attestation(tmp_path, worker_environ=env)
    assert worker.agent_max_concurrency == 4


def test_effective_contract_defaults_and_constraints_match_settings():
    contract = soak.effective_worker_contract({})
    assert contract.worker_concurrency == Settings.model_fields[
        "worker_concurrency"
    ].default
    assert contract.agent_max_concurrency == Settings.model_fields[
        "agent_max_concurrency"
    ].default
    assert contract.credential_max_concurrent_gemini == Settings.model_fields[
        "credential_max_concurrent_gemini"
    ].default
    assert contract.credential_slot_wait_seconds == Settings.model_fields[
        "credential_slot_wait_seconds"
    ].default
    assert contract.structured_output_enabled is Settings.model_fields[
        "structured_output_enabled"
    ].default
    with pytest.raises(ValidationError):
        soak.effective_worker_contract({"CREDENTIAL_SLOT_WAIT_SECONDS": "0"})


def test_local_attestation_never_emits_secrets_or_notion_values(tmp_path):
    env = worker_env(
        tmp_path,
        AUTH_TOKEN="operator-secret",
        DATABASE_URL="postgresql+asyncpg://secret@db/edu_copy",
    )
    worker = build_valid_local_attestation(tmp_path, worker_environ=env)
    encoded = soak.canonical_json(worker)
    assert "plain-secret-key" not in encoded
    assert "postgresql" not in encoded
    assert "operator-secret" not in encoded
    assert "page-secret" not in encoded
    assert worker.credential_fingerprint == credential_id.credential_for("gemini", env)


def test_local_attestation_rejects_vertex_project_identity(tmp_path):
    env = worker_env(tmp_path)
    env.pop("GEMINI_API_KEY")
    env.update(
        {
            "GOOGLE_APPLICATION_CREDENTIALS": "/private/sa.json",
            "GOOGLE_CLOUD_PROJECT": "project-visible-name",
        }
    )
    with pytest.raises(soak.AttestationError, match="plain Gemini API key"):
        build_valid_local_attestation(tmp_path, worker_environ=env)


@pytest.mark.parametrize("count", [0, 2])
def test_local_attestation_fails_unless_exactly_one_worker_process(tmp_path, count):
    make_pdf(tmp_path)
    processes = [process(tmp_path, pid=index + 1) for index in range(count)]
    with pytest.raises(soak.AttestationError, match="exactly one worker process"):
        soak.build_local_attestation(
            scope_for(tmp_path),
            hostname="Host-02",
            processes=processes,
            now=UTC_NOW,
            git_identity=(1001, "fedcba9"),
        )


def test_local_attestation_fails_when_target_environment_is_unreadable(tmp_path):
    make_pdf(tmp_path)
    worker = process(
        tmp_path,
        environ_error=psutil.AccessDenied(pid=1),
    )
    with pytest.raises(soak.AttestationError, match="worker environment"):
        soak.build_local_attestation(
            scope_for(tmp_path),
            hostname="Host-02",
            processes=[worker],
            now=UTC_NOW,
            git_identity=(1001, "fedcba9"),
        )


def test_uv_wrapper_and_zombie_are_not_counted_as_worker_processes(tmp_path):
    processes = [
        process(
            tmp_path,
            pid=10,
            argv=["uv", "run", "python", "-m", "app.services.worker"],
        ),
        process(tmp_path, pid=11),
        process(tmp_path, pid=12, state=psutil.STATUS_ZOMBIE),
    ]
    assert [item.pid for item in soak.discover_worker_processes(processes)] == [11]


def test_worker_discovery_requires_adjacent_exact_module_pair(tmp_path):
    processes = [
        process(
            tmp_path,
            pid=10,
            argv=["python", "-m", "other", "app.services.worker"],
        ),
        process(
            tmp_path,
            pid=11,
            argv=["python", "note-app.services.worker"],
        ),
    ]
    assert soak.discover_worker_processes(processes) == []


def test_relative_var_dir_is_anchored_at_target_worker_cwd(tmp_path):
    env = worker_env(tmp_path, VAR_DIR="relative-var")
    pdf = tmp_path / "relative-var" / "books" / str(BOOK) / "source.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    worker = build_valid_local_attestation(tmp_path, worker_environ=env)
    assert worker.pdf_sha256_by_book[str(BOOK)] == sha256_bytes(b"pdf")


def test_missing_pdf_is_explicit_null_evidence(tmp_path):
    scope = scope_for(tmp_path)
    worker = soak.build_local_attestation(
        scope,
        hostname="Host-02",
        processes=[process(tmp_path)],
        now=UTC_NOW,
        git_identity=(1001, "fedcba9"),
    )
    assert worker.pdf_sha256_by_book[str(BOOK)] is None


def test_local_attestation_rejects_host_outside_scope(tmp_path):
    make_pdf(tmp_path)
    with pytest.raises(soak.AttestationError, match="participant_hosts"):
        soak.build_local_attestation(
            scope_for(tmp_path, ["Host-03"]),
            hostname="Host-02",
            processes=[process(tmp_path)],
            now=UTC_NOW,
            git_identity=(1001, "fedcba9"),
        )


def test_attest_local_cli_emits_one_sanitized_json_line_without_io(tmp_path):
    make_pdf(tmp_path)
    stdout, stderr = io.StringIO(), io.StringIO()
    rc = soak.main(
        ["attest-local", "--scope", "-"],
        process_source=lambda: [process(tmp_path)],
        stdin=io.StringIO(soak.canonical_json(scope_for(tmp_path))),
        stdout=stdout,
        stderr=stderr,
        hostname="Host-02",
        now=lambda: UTC_NOW,
        git_identity=lambda env: (1001, "fedcba9"),
    )
    assert rc == 0
    assert stdout.getvalue().count("\n") == 1
    assert stderr.getvalue() == ""
    assert "plain-secret-key" not in stdout.getvalue()
    assert soak.WorkerAttestation.model_validate_json(stdout.getvalue())


def test_attest_local_cli_fails_without_echoing_invalid_target_secrets(tmp_path):
    make_pdf(tmp_path)
    env = worker_env(
        tmp_path,
        NOTION_SUBJECT_PAGES='{"english|8":"super-secret-page",broken',
        DATABASE_URL="postgresql+asyncpg://db-secret@db/edu_copy",
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    rc = soak.main(
        ["attest-local", "--scope", "-"],
        process_source=lambda: [process(tmp_path, env=env)],
        stdin=io.StringIO(soak.canonical_json(scope_for(tmp_path))),
        stdout=stdout,
        stderr=stderr,
        hostname="Host-02",
        now=lambda: UTC_NOW,
        git_identity=lambda target_env: (1001, "fedcba9"),
    )
    assert rc == soak.ExitCode.OPERATIONAL_ERROR
    assert stdout.getvalue() == ""
    diagnostic = stderr.getvalue()
    assert "super-secret-page" not in diagnostic
    assert "db-secret" not in diagnostic
    assert "plain-secret-key" not in diagnostic
    assert diagnostic == "attestation failed: target worker contract is invalid\n"


def test_attest_local_cli_rejects_secret_bearing_scope_without_echo_or_process_read(
    tmp_path,
):
    raw = valid_scope_dict()
    raw["api_key"] = "must-never-echo"
    process_reads = 0

    def processes():
        nonlocal process_reads
        process_reads += 1
        return [process(tmp_path)]

    stdout, stderr = io.StringIO(), io.StringIO()
    rc = soak.main(
        ["attest-local", "--scope", "-"],
        process_source=processes,
        stdin=io.StringIO(json.dumps(raw)),
        stdout=stdout,
        stderr=stderr,
        hostname="Host-02",
        now=lambda: UTC_NOW,
    )
    assert rc == soak.ExitCode.OPERATIONAL_ERROR
    assert process_reads == 0
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "attestation failed: scope is invalid\n"
    assert "must-never-echo" not in stderr.getvalue()


def worker_for_aggregate(
    tmp_path: Path,
    *,
    hostname: str,
    pid: int,
    scope: soak.SoakScope,
    now: datetime = UTC_NOW,
) -> soak.WorkerAttestation:
    make_pdf(tmp_path)
    return soak.build_local_attestation(
        scope,
        hostname=hostname,
        processes=[process(tmp_path, pid=pid)],
        now=now,
        git_identity=(1001, "fedcba9"),
    )


def test_aggregation_is_order_independent_and_canonical(tmp_path):
    scope = scope_for(tmp_path, ["Host-02", "Host-03"])
    h2 = worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope)
    h3 = worker_for_aggregate(tmp_path, hostname="Host-03", pid=3, scope=scope)
    a = soak.aggregate_attestations(scope, [h3, h2], now=UTC_NOW)
    b = soak.aggregate_attestations(scope, [h2, h3], now=UTC_NOW)
    assert soak.canonical_json(a) == soak.canonical_json(b)
    assert [worker.hostname for worker in a.workers] == ["Host-02", "Host-03"]
    assert a.input_artifact_sha256 == sorted(a.input_artifact_sha256)


def test_aggregation_rejects_missing_duplicate_or_unexpected_hosts(tmp_path):
    scope = scope_for(tmp_path, ["Host-02", "Host-03"])
    h2 = worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope)
    with pytest.raises(soak.AttestationError, match="participant host set mismatch"):
        soak.aggregate_attestations(scope, [h2], now=UTC_NOW)
    duplicate = h2.model_copy(update={"pc_id": "Host-02:3@fedcba9"})
    with pytest.raises(soak.AttestationError, match="duplicate hostname"):
        soak.aggregate_attestations(scope, [h2, duplicate], now=UTC_NOW)


def test_aggregation_rejects_artifact_from_another_scope(tmp_path):
    scope = scope_for(tmp_path)
    other_raw = valid_scope_dict()
    other_raw["job_ids"] = ["44444444-4444-4444-4444-444444444444"]
    other = soak.SoakScope.model_validate(other_raw)
    worker = worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope)
    worker = worker.model_copy(update={"scope_sha256": soak.sha256_canonical(other)})
    with pytest.raises(soak.AttestationError, match="scope digest"):
        soak.aggregate_attestations(scope, [worker], now=UTC_NOW)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("git_sha", "abcdef0", "identity"),
        ("code_version", 999, "identity"),
        ("agent_max_concurrency", 5, "configuration"),
        ("process_count_for_host", 2, "exactly one"),
        ("notion_mapping_keys", ["english|8"], "Notion"),
    ],
)
def test_aggregation_rejects_worker_contract_mismatch(tmp_path, field, value, match):
    scope = scope_for(tmp_path)
    worker = worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope)
    worker = worker.model_copy(update={field: value})
    with pytest.raises(soak.AttestationError, match=match):
        soak.aggregate_attestations(scope, [worker], now=UTC_NOW)


def test_aggregation_rejects_mixed_credential_fingerprints(tmp_path):
    scope = scope_for(tmp_path, ["Host-02", "Host-03"])
    h2 = worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope)
    h3 = worker_for_aggregate(tmp_path, hostname="Host-03", pid=3, scope=scope)
    h3 = h3.model_copy(
        update={"credential_fingerprint": "gemini:fedcba9876543210"}
    )
    with pytest.raises(soak.AttestationError, match="credential fingerprint mismatch"):
        soak.aggregate_attestations(scope, [h2, h3], now=UTC_NOW)


def test_aggregation_rejects_stale_artifact_or_wrong_pdf(tmp_path):
    scope = scope_for(tmp_path)
    worker = worker_for_aggregate(
        tmp_path,
        hostname="Host-02",
        pid=2,
        scope=scope,
        now=UTC_NOW - timedelta(seconds=scope.attestation_max_age_seconds + 1),
    )
    with pytest.raises(soak.AttestationError, match="stale"):
        soak.aggregate_attestations(scope, [worker], now=UTC_NOW)

    fresh = worker.model_copy(
        update={
            "observed_at": UTC_NOW,
            "pdf_sha256_by_book": {str(BOOK): "b" * 64},
        }
    )
    with pytest.raises(soak.AttestationError, match="PDF"):
        soak.aggregate_attestations(scope, [fresh], now=UTC_NOW)


def test_attest_aggregate_cli_is_order_independent(tmp_path):
    scope = scope_for(tmp_path, ["Host-02", "Host-03"])
    workers = [
        worker_for_aggregate(tmp_path, hostname="Host-02", pid=2, scope=scope),
        worker_for_aggregate(tmp_path, hostname="Host-03", pid=3, scope=scope),
    ]
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(soak.canonical_json(scope), encoding="utf-8")
    paths = []
    for index, worker in enumerate(workers):
        path = tmp_path / f"worker-{index}.json"
        path.write_text(soak.canonical_json(worker), encoding="utf-8")
        paths.append(path)

    outputs = []
    for order in (paths, list(reversed(paths))):
        stdout = io.StringIO()
        argv = ["attest-aggregate", "--scope", str(scope_path)]
        for path in order:
            argv += ["--input", str(path)]
        assert soak.main(argv, stdout=stdout, now=lambda: UTC_NOW) == 0
        outputs.append(stdout.getvalue())
    assert outputs[0] == outputs[1]
    assert outputs[0].count("\n") == 1


def test_attest_aggregate_cli_rejects_secret_bearing_artifact_without_echo(tmp_path):
    scope = scope_for(tmp_path)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(soak.canonical_json(scope), encoding="utf-8")
    artifact_path = tmp_path / "worker.json"
    raw = worker_for_aggregate(
        tmp_path, hostname="Host-02", pid=2, scope=scope
    ).model_dump(mode="json")
    raw["gemini_api_key"] = "must-never-echo"
    artifact_path.write_text(json.dumps(raw), encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()
    rc = soak.main(
        [
            "attest-aggregate",
            "--scope",
            str(scope_path),
            "--input",
            str(artifact_path),
        ],
        stdout=stdout,
        stderr=stderr,
        now=lambda: UTC_NOW,
    )
    assert rc == soak.ExitCode.OPERATIONAL_ERROR
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "attestation failed: input artifact is invalid\n"
    assert "must-never-echo" not in stderr.getvalue()
