# Fenced-Lease Soak Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed, stage-scoped controller that proves fenced job leases under real fleet load without launching work itself, and make abandoned database transactions self-terminate before they can consume PostgreSQL capacity or retain advisory locks.

**Architecture:** A single CLI, `scripts/fenced_lease_soak.py`, consumes two explicit JSON contracts: an immutable stage scope (the exact batches, jobs, start instant, expected models, target concurrency, expected code vintage, expected Alembic revision, and approved cost limits) and a fresh out-of-band fleet attestation (the exact worker process configuration, credential fingerprint, PDFs, and Notion mapping absence). Its default `preflight` and `watch` commands open PostgreSQL transactions as `READ ONLY`, evaluate pure finding rules over scoped snapshots, and append redacted JSONL evidence. The only write path is an optional, two-gesture `--arm-stop --confirm-arm lease-soak-stop:<run-id>` circuit breaker that may pause the named soak batches plus the fleet API gate, never cancel jobs, never alter unrelated batches, and never clear a pause.

**Tech Stack:** Python 3.13+, argparse, asyncio, Pydantic v2, SQLAlchemy asyncio/asyncpg, PostgreSQL 16, pytest/pytest-asyncio.

## Global Constraints

- The controller does **not** launch, retry, resume, cancel, archive, or regenerate a homework. It observes work created by a separately approved operator action.
- `preflight` and unarmed `watch` must execute `SET TRANSACTION READ ONLY` before every database snapshot. A regression that issues `INSERT`, `UPDATE`, or `DELETE` in either path is a release blocker.
- Scope is never inferred from “recent” rows alone. Every run requires non-empty `batch_ids`, non-empty `job_ids`, and an aware UTC `since` instant in the scope file. Every queried job, usage row, phase row, and lease event is filtered by those job IDs, with `since` as an additional lower bound for time-series tables.
- The first soak requires one exact, caller-supplied **final deployed** Git SHA, code version, and Alembic revision, derived from the externally gated merge/deployment immediately before the run. Every participating process, heartbeat, attestation, `claimed_by` suffix, and the fleet version floor must match the code pair; the live database must match the caller-supplied revision exactly while still proving the 0052 ledger/token primitives exist. `d6b1c9f` / v987 / `0052_job_lease_fencing` are only the pre-plan audit baseline; they are never runtime defaults because later gated lanes create newer identities. The remaining production configuration is exactly `WORKER_CONCURRENCY=2`, `AGENT_MAX_CONCURRENCY=4`, `CREDENTIAL_MAX_CONCURRENT_GEMINI=32`, `CREDENTIAL_SLOT_WAIT_SECONDS=120`, no `GEMINI_MAX_CONCURRENCY`, one worker process per host, one shared plain-key fingerprint, and `STRUCTURED_OUTPUT_ENABLED=false`. All values live in the scope file, not as hidden code defaults.
- The first production sequence is `4 -> 8 -> 12 -> 20 -> 40` independent fresh jobs. A level advances only after its jobs reach terminal state and a 60-second quiet-settle window passes all gates.
- The 40-job level is two 20-job batches so each remains below the configured `$50` per-batch cap. It is still one stage and one exact scope.
- The tooling lane is `$0`: unit tests, fake-store acceptance, and optional scratch-Postgres integration only. No provider call and no production job launch is authorized by this plan.
- A later billed soak is a separate operator gate. The 84-job series is forecast at `$96.30` median / `$125.70` historical p95 / `$202.74` historical maximum. Do not run it without an explicit hard-dollar approval; the proposed hard stop is `$135`, not an authorization.
- `--arm-stop` is not permission to spend. It only changes how the already-running watcher responds to a proven hard violation.
- A hard stop pauses exact soak batches and, if no foreign fleet pause exists, sets the fleet API pause. The run's own `lease-soak-staging:<run-id>` pause is not foreign and may be replaced by `lease-soak-stop:<run-id>`. It never mass-cancels running jobs, never deletes evidence, and never auto-unpauses.
- Notion safety is fail-closed. The scope lists forbidden English mapping keys and each attested worker must prove those keys absent. The expected terminal outcome is `notion_archived_at IS NULL` plus a non-empty `notion_skip_reason` for every scoped job.
- Fleet attestation is never hand-authored. Each participating host runs `attest-local` from the final deployed checkout; it reads effective settings/environment, discovers the one live worker process, derives its registry `pc_id`, fingerprints the effective Gemini credential without exposing it, hashes required PDFs, and emits only sanitized canonical JSON to stdout. `attest-aggregate` deterministically validates and combines those exact per-host artifacts; preflight then cross-checks them against live registry rows.
- Attestation trust terminates at the authenticated fleet-management channel that executes the command and captures stdout. The JSON is not a cryptographic remote-attestation claim; copying or editing it by hand is prohibited. Canonical input digests make accidental substitution/reordering visible, while the live registry cross-check detects stale/restarted worker processes.
- Never serialize the API key, database URL, environment values, or a reversible credential. Evidence may contain only the plain-key, non-reversible credential fingerprint already used by the limiter; a Vertex project identity is ineligible for this particular soak, not deleted or reassigned.
- PostgreSQL hardening is process-local: asyncpg `server_settings`, not `ALTER SYSTEM`, not `ALTER ROLE`, and no migration. Use `application_name` plus `idle_in_transaction_session_timeout=300000` (five minutes) on each newly opened head, worker, and soak-controller connection.
- Tests that need PostgreSQL are opt-in with `RUN_DB_INTEGRATION=1` and must point at a scratch database. Canonical unit tests must exercise every decision rule without that flag.
- Work proceeds test-first, one commit per task, with an independent review after each task. No implementation branch self-merges.

## Branch-Collision Gate Record (2026-08-10)

- Fetched all refs with `git fetch --all --prune` before creating this plan.
- Planning base/audit baseline: `origin/Nggaev-v2@d6b1c9f65e13ea5a6c2abd21b8a592303ece784b`, schema `0052_job_lease_fencing`. These record what was reviewed; the eventual final SHA/version/revision must replace all three in every paid-run scope and attestation.
- Existing `scripts/soak_watch_leases.sql@595911d` is a read-only 24-hour/7-day historical snapshot. It has no exact run scope, preflight, fleet attestation, cost cap, JSON evidence, or stop action. It remains useful as a human fallback and is not replaced in this lane.
- `origin/feat/fenced-job-leases@3253bb9` contains the already-merged fencing implementation and tests. It supplies the event vocabulary and invariants; it does not contain a controller.
- `origin/feat/model-config-3x-flash-exec@d62dc1f` changes old `stress_concurrency.py` / `stress_multimodel.py` probes and model configuration. Those probes make provider calls and do not overlap this controller.
- Open PRs `#108`, `#117`, and `#118` do not touch `scripts/fenced_lease_soak.py`, the planned tests, or `app/db.py`. Their authors are not `s1gmamale1`, but this lane still treats all PRs as read-only.
- No local/remote branch or worktree contains `scripts/fenced_lease_soak.py` or any planned test path.
- The scope amendment for DB session hardening triggered a second fetch/scan. No active branch or open PR modifies `app/db.py` or `tests/test_db_pool_config.py`. Several stale/active branches change `app/config.py`; this plan deliberately adds no setting there, avoiding ownership overlap.
- The attestation amendment triggered a third fetch/scan. No branch or open PR contains `scripts/fenced_lease_soak.py` or `tests/scripts/test_fenced_lease_soak_attestation.py`; the helper reads `app.config.Settings` field metadata and reuses `app.services.code_version`, `app.services.credential_id`, and the documented `storage.book_pdf_path` layout without modifying their actively shared paths. It explicitly does not trust module-global `settings` for a different process.
- The Task-3 correction triggered a fourth fetch/scan at `origin/Nggaev-v2@d6b1c9f`. No open PR owns the soak paths. The active solver/source worktrees reserve later schema revisions 0053/0054 without touching this controller, so the scope now carries the exact final revision instead of hardcoding the audit baseline.
- Isolated planning worktree: `/Users/macmini5/Documents/HCGA-fenced-soak-plan`, branch `plan/fenced-lease-soak-controller`. The shared checkout's untracked files are untouched.
- Baseline: `13 passed` for `tests/services/test_lease_types.py`, `tests/services/test_worker_version_gate.py`, and `tests/test_db_pool_config.py`.

## Round-3 Composition Corrections (2026-08-10)

- Repeated the mandatory read-only collision gate at
  `origin/Nggaev-v2@d6b1c9f` and local controller head `9c2adb6`. Open PRs
  `#108`, `#117`, and `#118` touch dashboard/content-JSON paths only; no
  branch or worktree overlaps the controller files. This branch remains the
  sole owner of `scripts/fenced_lease_soak.py` and its tests.
- `SoakScope` accepts only the authorized `4/8/12/20/40` stages. The job count
  must equal the target; `4/8/12/20` use one batch, while `40` uses exactly two
  batches whose live job distribution is exactly `20+20`. A watch hard-stops
  if simultaneous running work exceeds the authorized target.
- Every sample recomputes the complete claimable-worker set from the live
  registry, version floor, heartbeats, capabilities, and scrub tombstones. A
  newly claimable process outside the immutable attestation is a hard stop.
  The read store also persists unscoped job transitions and lease events since
  `scope.since`, so unrelated work that starts and terminates between samples
  cannot disappear from the evidence.
- Solver quality is outcome-aware: `mismatch_regen` is a successful repaired
  result. Only unresolved outcomes (`mismatch_shipped`,
  `mismatch_regen_failed`, and the incoming `mismatch_blocked`) fail a stage.
- PostgreSQL wait evidence is restricted to `client backend` rows while still
  reporting their non-Client lock/I/O waits. Armed writes use a 5-second lock
  timeout and 30-second statement timeout; the controller independently caps
  stop completion at 30 seconds and records `stop_failed` on failure or timeout.
- Round-3 unit/fake-store verification is `$0`. PostgreSQL integration remains
  opt-in and must use a scratch URL; no production fallback is permitted.

## File Structure

- Modify `app/db.py`: pure connection-server-settings builder and application of those settings to the existing engine.
- Modify `tests/test_db_pool_config.py`: preserve pool bounds and pin per-role connection settings.
- Create `tests/integration/test_db_session_settings.py`: prove asyncpg applies the application name and idle-transaction timeout on a scratch PostgreSQL connection.
- Create `scripts/fenced_lease_soak.py`: contracts, read-only store, preflight rules, runtime snapshot rules, JSON evidence, watch loop, and armed stop writer. Keep this as one script because it is an operator tool with one CLI and one evidence format; pure functions and protocols provide test boundaries.
- Create `tests/scripts/test_fenced_lease_soak_contracts.py`: JSON contract validation, redaction, scope exactness, CLI write-gate tests.
- Create `tests/scripts/test_fenced_lease_soak_attestation.py`: hermetic effective-setting, process discovery, credential fingerprint, PDF hash, secret-absence, and deterministic aggregation tests.
- Create `tests/scripts/test_fenced_lease_soak_preflight.py`: migration, fleet, queue, DB, credential, PDF, Notion, and budget preflight bite tests.
- Create `tests/scripts/test_fenced_lease_soak_snapshot.py`: lease/token/phase/error/cost/quality decision-rule bite tests.
- Create `tests/scripts/test_fenced_lease_soak_watch.py`: deterministic watch/settle/JSONL/pass/fail tests.
- Create `tests/scripts/test_fenced_lease_soak_stop.py`: exact-row stop mutation and foreign-pause preservation tests.
- Create `tests/integration/test_fenced_lease_soak_db.py`: scratch-Postgres proof of read-only enforcement, scoped SQL, and exact stop writes.

---

### Task 1: Bound and identify every application database session

**Files:**
- Modify: `app/db.py`
- Modify: `tests/test_db_pool_config.py`
- Create: `tests/integration/test_db_session_settings.py`

**Interfaces:**
- Produces: `_connection_server_settings(*, worker_concurrency: int, hostname: str, pid: int) -> dict[str, str]`.
- Produces: `_engine_options(*, worker_concurrency: int, hostname: str, pid: int) -> dict[str, object]`, merged into the existing `create_async_engine` call.
- Contract: role is `head` only when `worker_concurrency == 0`; every positive value is `worker`. `application_name` is `hcga-<role>:<hostname>:<pid>`, truncated to PostgreSQL's 63-byte identifier limit. Timeout is the string `"300000"` milliseconds.

- [ ] **Step 1: Add unit RED tests for the server settings and unchanged pool sizes**

Append these tests to `tests/test_db_pool_config.py`:

```python
from app.db import _connection_server_settings, _engine_options


def test_head_connection_is_identifiable_and_times_out_idle_transactions():
    got = _connection_server_settings(
        worker_concurrency=0, hostname="head-mini", pid=101
    )
    assert got == {
        "application_name": "hcga-head:head-mini:101",
        "idle_in_transaction_session_timeout": "300000",
    }


def test_worker_connection_is_process_identifiable_and_name_is_bounded():
    got = _connection_server_settings(
        worker_concurrency=2, hostname="x" * 100, pid=202
    )
    assert got["application_name"].startswith("hcga-worker:")
    assert got["application_name"].endswith(":202")
    assert len(got["application_name"].encode("utf-8")) <= 63
    assert got["idle_in_transaction_session_timeout"] == "300000"


def test_engine_options_preserve_worker_pool_and_apply_asyncpg_server_settings():
    got = _engine_options(worker_concurrency=2, hostname="host-40", pid=303)
    assert got["pool_size"] == 2
    assert got["max_overflow"] == 2
    assert got["connect_args"] == {
        "server_settings": {
            "application_name": "hcga-worker:host-40:303",
            "idle_in_transaction_session_timeout": "300000",
        }
    }
```

- [ ] **Step 2: Run the unit RED tests**

Run:

```bash
uv run pytest tests/test_db_pool_config.py -q
```

Expected: collection fails because `_connection_server_settings` and `_engine_options` do not exist.

- [ ] **Step 3: Implement pure server settings and apply them to the engine**

Refactor `app/db.py` to this shape while preserving `pool_pre_ping=True` and `pool_recycle=1800`:

```python
import os
import socket


_IDLE_IN_TRANSACTION_TIMEOUT_MS = 300_000


def _application_name(*, worker_concurrency: int, hostname: str, pid: int) -> str:
    role = "head" if worker_concurrency == 0 else "worker"
    suffix = f":{pid}"
    prefix = f"hcga-{role}:"
    budget = 63 - len(prefix.encode()) - len(suffix.encode())
    host_bytes = hostname.encode("utf-8")[:max(budget, 0)]
    safe_host = host_bytes.decode("utf-8", errors="ignore")
    return f"{prefix}{safe_host}{suffix}"


def _connection_server_settings(
    *, worker_concurrency: int, hostname: str, pid: int
) -> dict[str, str]:
    return {
        "application_name": _application_name(
            worker_concurrency=worker_concurrency, hostname=hostname, pid=pid
        ),
        "idle_in_transaction_session_timeout": str(
            _IDLE_IN_TRANSACTION_TIMEOUT_MS
        ),
    }


def _engine_options(
    *, worker_concurrency: int, hostname: str, pid: int
) -> dict[str, object]:
    return {
        **_pool_config(worker_concurrency=worker_concurrency),
        "connect_args": {
            "server_settings": _connection_server_settings(
                worker_concurrency=worker_concurrency,
                hostname=hostname,
                pid=pid,
            )
        },
    }


engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    **_engine_options(
        worker_concurrency=settings.worker_concurrency,
        hostname=socket.gethostname(),
        pid=os.getpid(),
    ),
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

- [ ] **Step 4: Add a scratch-Postgres integration test for actual settings**

Create `tests/integration/test_db_session_settings.py`:

```python
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import _connection_server_settings


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="set RUN_DB_INTEGRATION=1 with a scratch DATABASE_URL",
)


@pytest.mark.asyncio
async def test_asyncpg_applies_session_settings():
    expected = _connection_server_settings(
        worker_concurrency=2, hostname="soak-test", pid=404
    )
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        pool_size=1,
        max_overflow=0,
        connect_args={"server_settings": expected},
    )
    try:
        async with engine.connect() as conn:
            app_name = await conn.scalar(text("select current_setting('application_name')"))
            idle_timeout = await conn.scalar(
                text("select current_setting('idle_in_transaction_session_timeout')")
            )
        assert app_name == "hcga-worker:soak-test:404"
        assert idle_timeout == "5min"
    finally:
        await engine.dispose()
```

- [ ] **Step 5: Run green tests**

Run:

```bash
uv run pytest tests/test_db_pool_config.py tests/integration/test_db_session_settings.py -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  uv run pytest tests/integration/test_db_session_settings.py -q
```

Expected: unit tests pass; integration is skipped without the flag and passes against scratch PostgreSQL with `application_name=hcga-worker:soak-test:404` and timeout `5min`.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/db.py tests/test_db_pool_config.py tests/integration/test_db_session_settings.py
git commit -m "fix(db): bound and identify idle transactions"
```

**Operational semantics:** the timeout applies only to a transaction sitting idle, not a long SQL statement and not a model call (model calls occur outside transactions). When it fires, PostgreSQL closes that connection; `pool_pre_ping=True` replaces it on the next checkout. Existing pooled connections do not inherit the setting, so deployment requires coordinated head/worker restarts. Rollback is a code revert plus the same restarts; no database rollback exists or is needed.

---

### Task 2: Pin immutable contracts and derive trusted local attestations

**Files:**
- Create: `scripts/fenced_lease_soak.py`
- Create: `tests/scripts/test_fenced_lease_soak_contracts.py`
- Create: `tests/scripts/test_fenced_lease_soak_attestation.py`

**Interfaces:**
- Produces Pydantic models `SoakScope`, `FleetAttestation`, `WorkerAttestation`, `Finding`, `SoakSnapshot`, and `StopReceipt`.
- Produces `load_scope(source: Path | Literal["-"], *, stdin: TextIO = sys.stdin) -> SoakScope`, `load_attestation(path: Path) -> FleetAttestation`, `redacted_model_dump(model: BaseModel) -> dict`, and `parse_args(argv: Sequence[str]) -> argparse.Namespace`.
- Produces a narrow `ProcessView` protocol (`pid`, `status()`, `cmdline()`, `environ()`, `cwd()`), `discover_worker_processes(processes: Iterable[ProcessView]) -> list[ProcessView]`, `effective_worker_contract(worker_env: Mapping[str, str]) -> EffectiveWorkerContract`, `build_local_attestation(scope, *, hostname, processes, now, git_identity=None) -> WorkerAttestation`, `canonical_json(model) -> str`, and `aggregate_attestations(scope, workers, *, now) -> FleetAttestation`.
- Produces injectable `main(argv, *, process_source=None, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, hostname=None, now=None, git_identity=None) -> int`; production defaults use `psutil`, `socket`, the real clock, and git, while tests inject all of them and prove the command never opens a database or network client.
- `SoakScope` fields: `run_id`, aware-UTC `since`, exact `batch_ids`, exact `job_ids`, exact `participant_hosts`, `target_running`, `expected_git_sha`, `expected_code_version`, `expected_db_revision`, expected four concurrency knobs, `legacy_gemini_var_must_be_absent`, `structured_output_enabled`, `required_book_sha256`, `forbidden_notion_mapping_keys`, `expected_models_by_operation_prefix`, `approved_incremental_cost_usd`, `fleet_cost_limit_usd`, `db_preflight_connection_limit`, `db_hard_stop_connection_limit`, `heartbeat_max_age_seconds`, `attestation_max_age_seconds`, and `settle_seconds`. The model map must contain exactly `phase.run`, `lesson.extract`, `lesson.extract.coverage`, `lesson.extract.verify`, `judge:`, and `solve:` with stripped non-empty model values; missing, extra, or blank entries fail while parsing the scope, before preflight can open a store or incur spend.
- `FleetAttestation` fields: `scope_sha256`, `observed_at`, `credential_fingerprint`, ordered `input_artifact_sha256`, and non-empty ordered `workers`.
- Each `WorkerAttestation` fields: `scope_sha256`, exact `pc_id`, `hostname`, `observed_at`, `git_sha`, `code_version`, `worker_concurrency`, `agent_max_concurrency`, `credential_max_concurrent_gemini`, `credential_slot_wait_seconds`, `gemini_max_concurrency_present`, `structured_output_enabled`, `process_count_for_host`, `credential_fingerprint`, `pdf_sha256_by_book: dict[str, str | None]`, and `notion_mapping_keys`.

- [ ] **Step 1: Write contract RED tests**

Create tests that instantiate the real models. Include these load-bearing cases:

```python
def test_scope_rejects_empty_or_overlapping_identity_fields(tmp_path):
    raw = valid_scope_dict()
    raw["job_ids"] = []
    with pytest.raises(ValidationError):
        soak.SoakScope.model_validate(raw)


def test_scope_requires_aware_utc_since():
    raw = valid_scope_dict()
    raw["since"] = "2026-08-10T12:00:00"  # no offset
    with pytest.raises(ValidationError):
        soak.SoakScope.model_validate(raw)


def test_attestation_rejects_raw_secret_fields():
    raw = valid_attestation_dict()
    raw["workers"][0]["gemini_api_key"] = "secret"
    with pytest.raises(ValidationError):
        soak.FleetAttestation.model_validate(raw)


def test_final_deployed_identity_is_caller_supplied_not_baked_in():
    raw = valid_scope_dict()
    raw["expected_git_sha"] = "fedcba9"
    raw["expected_code_version"] = 1001
    raw["expected_db_revision"] = "0054_source_integrity"
    scope = soak.SoakScope.model_validate(raw)
    assert scope.expected_git_sha == "fedcba9"
    assert scope.expected_code_version == 1001
    assert scope.expected_db_revision == "0054_source_integrity"


def test_scope_pins_exact_participating_hosts():
    raw = valid_scope_dict()
    raw["participant_hosts"] = ["Host-02", "Host-02"]
    with pytest.raises(ValidationError, match="duplicate participant host"):
        soak.SoakScope.model_validate(raw)


def test_unarmed_watch_is_read_only_by_construction():
    args = soak.parse_args([
        "watch", "--scope", "scope.json", "--attestation", "fleet.json",
        "--artifact-dir", "out",
    ])
    assert args.arm_stop is False
    assert args.confirm_arm is None


@pytest.mark.parametrize("confirm", [None, "wrong", "lease-soak-stop:other-run"])
def test_arm_stop_requires_exact_second_gesture(confirm):
    argv = [
        "watch", "--scope", "scope.json", "--attestation", "fleet.json",
        "--artifact-dir", "out", "--arm-stop",
    ]
    if confirm is not None:
        argv += ["--confirm-arm", confirm]
    with pytest.raises(SystemExit):
        soak.validate_arm_confirmation(
            soak.parse_args(argv), run_id="stage-04-20260810"
        )
```

- [ ] **Step 2: Run contract RED tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_contracts.py -q
```

Expected: import fails because the script does not exist.

- [ ] **Step 3: Write local-attestation and aggregation RED tests**

Create `tests/scripts/test_fenced_lease_soak_attestation.py` with injected settings, environment, clock, processes, and temporary PDFs. Do not read the developer machine's real `.env` or process table in tests.

```python
def test_local_attestation_reports_effective_config_and_registry_identity(tmp_path):
    scope = valid_scope(
        participant_hosts=["Host-02"],
        expected_git_sha="fedcba9",
        expected_code_version=1001,
        required_book_sha256={str(BOOK): sha256_bytes(b"pdf")},
    )
    pdf = tmp_path / "books" / str(BOOK) / "source.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    worker_env = {
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
    worker = soak.build_local_attestation(
        scope,
        hostname="Host-02",
        processes=[process(
            pid=4242,
            cmdline=["python", "-m", "app.services.worker"],
            environ=worker_env,
            cwd=tmp_path,
        )],
        now=UTC_NOW,
        git_identity=(1001, "fedcba9"),
    )
    assert worker.pc_id == "Host-02:4242@fedcba9"
    assert worker.process_count_for_host == 1
    assert worker.worker_concurrency == 2
    assert worker.agent_max_concurrency == 4
    assert worker.credential_max_concurrent_gemini == 32
    assert worker.credential_slot_wait_seconds == 120
    assert worker.pdf_sha256_by_book[str(BOOK)] == sha256_bytes(b"pdf")
    assert worker.notion_mapping_keys == ["matematika|5"]
    assert worker.scope_sha256 == soak.sha256_canonical(scope)


def test_target_worker_environment_wins_over_helper_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "99")
    worker = build_valid_local_attestation(
        tmp_path, worker_environ={"AGENT_MAX_CONCURRENCY": "4"}
    )
    assert worker.agent_max_concurrency == 4


def test_effective_contract_defaults_and_constraints_match_settings():
    contract = soak.effective_worker_contract({})
    assert contract.worker_concurrency == Settings.model_fields["worker_concurrency"].default
    assert contract.agent_max_concurrency == Settings.model_fields["agent_max_concurrency"].default
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
    env = {
        "GEMINI_API_KEY": "plain-secret-key",
        "DATABASE_URL": "postgresql+asyncpg://secret@db/edu_copy",
        "AUTH_TOKEN": "operator-secret",
    }
    worker = build_valid_local_attestation(
        tmp_path, worker_environ=env, notion_pages={"matematika|5": "page-secret"}
    )
    encoded = soak.canonical_json(worker)
    assert "plain-secret-key" not in encoded
    assert "postgresql" not in encoded
    assert "operator-secret" not in encoded
    assert "page-secret" not in encoded
    assert worker.credential_fingerprint == credential_id.credential_for("gemini", env)


def test_local_attestation_rejects_vertex_project_identity(tmp_path):
    env = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/private/sa.json",
        "GOOGLE_CLOUD_PROJECT": "project-visible-name",
    }
    with pytest.raises(soak.AttestationError, match="plain Gemini API key"):
        build_valid_local_attestation(tmp_path, worker_environ=env)


@pytest.mark.parametrize(
    "processes",
    [
        [],
        [
            process(pid=1, cmdline=["python", "-m", "app.services.worker"]),
            process(pid=2, cmdline=["python", "-m", "app.services.worker"]),
        ],
    ],
)
def test_local_attestation_fails_unless_exactly_one_worker_process(processes):
    with pytest.raises(soak.AttestationError, match="exactly one worker process"):
        build_valid_local_attestation(processes=processes)


def test_local_attestation_fails_when_target_environment_is_unreadable():
    worker = process(
        pid=1,
        cmdline=["python", "-m", "app.services.worker"],
        environ_error=psutil.AccessDenied(pid=1),
    )
    with pytest.raises(soak.AttestationError, match="worker environment"):
        build_valid_local_attestation(processes=[worker])


def test_uv_wrapper_is_not_counted_as_a_second_worker_process():
    processes = [
        process(pid=10, cmdline=["uv", "run", "python", "-m", "app.services.worker"]),
        process(pid=11, cmdline=["python", "-m", "app.services.worker"]),
    ]
    assert [p.pid for p in soak.discover_worker_processes(processes)] == [11]


def test_attest_local_cli_emits_one_sanitized_json_line_without_io(tmp_path):
    stdout, stderr = io.StringIO(), io.StringIO()
    rc = soak.main(
        ["attest-local", "--scope", "-"],
        process_source=lambda: [valid_worker_process(tmp_path)],
        stdin=io.StringIO(canonical_scope_json()),
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


def test_aggregation_is_order_independent_and_canonical(tmp_path):
    scope = valid_scope(participant_hosts=["Host-02", "Host-03"])
    h2 = valid_worker(hostname="Host-02", pid=2)
    h3 = valid_worker(hostname="Host-03", pid=3)
    a = soak.aggregate_attestations(scope, [h3, h2], now=UTC_NOW)
    b = soak.aggregate_attestations(scope, [h2, h3], now=UTC_NOW)
    assert soak.canonical_json(a) == soak.canonical_json(b)
    assert [w.hostname for w in a.workers] == ["Host-02", "Host-03"]
    assert a.input_artifact_sha256 == sorted(a.input_artifact_sha256)


def test_aggregation_rejects_missing_duplicate_or_unexpected_hosts():
    scope = valid_scope(participant_hosts=["Host-02", "Host-03"])
    with pytest.raises(soak.AttestationError, match="participant host set mismatch"):
        soak.aggregate_attestations(
            scope, [valid_worker(hostname="Host-02")], now=UTC_NOW
        )
    with pytest.raises(soak.AttestationError, match="duplicate hostname"):
        soak.aggregate_attestations(
            scope,
            [valid_worker(hostname="Host-02", pid=1), valid_worker(hostname="Host-02", pid=2)],
            now=UTC_NOW,
        )


def test_aggregation_rejects_artifact_from_another_scope():
    scope = valid_scope(participant_hosts=["Host-02"])
    other = valid_scope(participant_hosts=["Host-02"], job_ids=[uuid4()])
    worker = valid_worker(
        hostname="Host-02", scope_sha256=soak.sha256_canonical(other)
    )
    with pytest.raises(soak.AttestationError, match="scope digest"):
        soak.aggregate_attestations(scope, [worker], now=UTC_NOW)
```

- [ ] **Step 4: Run all Task 2 RED tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_contracts.py \
  tests/scripts/test_fenced_lease_soak_attestation.py -q
```

Expected: failures because the contracts and attestation helpers do not exist.

- [ ] **Step 5: Implement exact models and four-command CLI**

Use `extra="forbid"` on every persisted model. Normalize all UUID lists by rejecting duplicates, not silently deduplicating them. Validate `run_id` against `^[a-z0-9][a-z0-9-]{0,44}$` (45 characters keeps both pause reasons inside `String(64)`), `expected_git_sha` against `^[0-9a-f]{7,40}$`, `expected_code_version > 0`, `expected_db_revision` against `^[0-9a-z][0-9a-z_]{0,127}$`, validate `since.tzinfo`, convert to UTC, require `target_running > 0`, require `db_preflight_connection_limit < db_hard_stop_connection_limit`, require `approved_incremental_cost_usd > 0`, and require all PDF SHA-256 values to match `^[0-9a-f]{64}$`. The module must not define a baked-in expected SHA, code-version, or database-revision constant; all three come only from `SoakScope`. `redacted_model_dump` recursively removes values whose field names match secret-bearing names (`gemini_api_key`, `api_key`, `token`, `secret`, `password`, `database_url`) while explicitly retaining the safe contract fields `credential_fingerprint`, `forbidden_notion_mapping_keys`, and `claim_token`. Free-text job, phase, usage, and validation errors persist only an error class plus SHA-256 digest—never raw or sanitized excerpts.

The CLI surface is exact:

```text
fenced_lease_soak.py attest-local --scope PATH|-
fenced_lease_soak.py attest-aggregate --scope PATH|- --input PATH [--input PATH ...]
fenced_lease_soak.py preflight --scope PATH --attestation PATH --artifact-dir DIR
fenced_lease_soak.py watch --scope PATH --attestation PATH --artifact-dir DIR
                              [--interval-seconds 2]
                              [--arm-stop --confirm-arm lease-soak-stop:<run-id>]
```

Implement these stable exit codes:

```python
class ExitCode(IntEnum):
    PASS = 0
    PREFLIGHT_FAILED = 2
    HARD_STOP_READ_ONLY = 3
    HARD_STOP_ARMED = 4
    INCOMPLETE = 5
    OPERATIONAL_ERROR = 6
```

The parser must reject `--confirm-arm` without `--arm-stop`. `watch` with `--arm-stop` must reject unless `--confirm-arm` equals `f"lease-soak-stop:{scope.run_id}"` after the scope is loaded.

- [ ] **Step 6: Implement the local attestation without a network or database dependency**

`attest-local` does not assume the helper process inherited the worker's environment. After identifying the one live worker PID, read that process's environment and cwd through `psutil.Process.environ()` / `.cwd()`; fail closed on access denial. Build `EffectiveWorkerContract` from those target-process values, never from module-global `settings`. Define the contract as a dedicated Pydantic model whose aliases are the uppercase environment names, whose defaults are taken from the corresponding `Settings.model_fields`, and whose constraints/types mirror `Settings` for `worker_concurrency`, `agent_max_concurrency`, `credential_max_concurrent_gemini`, `credential_slot_wait_seconds`, `structured_output_enabled`, `var_dir`, and `notion_subject_pages`. A drift test pins those shared defaults and constraints. This catches a scheduled-task/export override that differs from the helper shell.

Derive credential identity with the existing pure `credential_id.credential_for("gemini", worker_env)`; never inspect or emit the key. This soak is specifically the shared plain-API-key deployment: require `GEMINI_API_KEY` to be non-empty and the resulting identity to match `^gemini:[0-9a-f]{16}$`. Reject a Vertex-only identity such as `gemini:<project-id>` rather than publishing a reversible project identifier or silently mixing billing pools. This does not remove or mutate any preserved Vertex assignment; it only makes a Vertex-backed host ineligible for this soak scope. Call `code_version.detect(env=worker_env)` so a worker's explicit version override is included, while git SHA comes from the deployed checkout running the command. The later exact registry `pc_id` cross-check rejects a running process that has not restarted onto that checkout.

Resolve `VAR_DIR` from the target worker environment/default. If relative, anchor it at the target worker's cwd, then use `<var_dir>/books/<book-id>/source.pdf` (the same contract as `storage.book_pdf_path`) and stream SHA-256 in 1 MiB chunks. Missing files are represented as `null` hashes so aggregation/preflight fail closed.

Discover worker processes with `psutil.process_iter(["pid", "status", "cmdline"])`. Exclude zombies, the attestation command itself, and wrapper parents such as `uv`: the executable basename must be `python`, `python3`, `python.exe`, or start with `python3.`. A live process matches only when its normalized argv contains the adjacent module pair `-m`, `app.services.worker`; do not substring-match arbitrary command text. Require exactly one matching Python PID. Construct `pc_id = f"{hostname}:{worker_pid}@{git_sha}"`, which is the same shape published by `worker._worker_id`. Do not emit command lines or environment values.

`gemini_max_concurrency_present` is derived from key presence in the target worker environment; it does not emit the deprecated value. Parse `NOTION_SUBJECT_PAGES` through `EffectiveWorkerContract` and emit only sorted top-level subject/grade keys, never page IDs or nested selector values. Bind every local artifact to the exact canonical `SoakScope` with `scope_sha256 = sha256(canonical_json(scope))`; this prevents an otherwise-valid artifact from a different stage/job/PDF scope being reused accidentally. Validate the produced model shape and require the local hostname to be in `scope.participant_hosts` before writing canonical JSON to stdout; cross-host/scope equality is enforced by aggregation so a missing PDF can remain explicit as `null` evidence instead of disappearing behind a generic local error. Diagnostics go to stderr. The command has no output-file flag, opens no database engine, imports no transport/provider client, and makes no HTTP request. `main(...)` receives injectable process/stdin/stdout/clock/hostname/git providers so the CLI path itself—not only helpers—is RED/GREEN tested without reading the developer machine.

- [ ] **Step 7: Implement deterministic aggregation**

`attest-aggregate` reads one or more per-host JSON artifacts, validates each as `WorkerAttestation`, and rejects:

- any worker `scope_sha256` different from the canonical input scope digest;
- a host set different from `scope.participant_hosts`;
- duplicate hostname or `pc_id`;
- any `process_count_for_host != 1`;
- stale `observed_at`;
- mixed/final-identity mismatch;
- config mismatch against scope;
- mixed/missing credential fingerprints;
- missing or wrong PDF hash;
- any forbidden Notion mapping key.

Compute each input digest over its parsed canonical `WorkerAttestation` JSON, not original whitespace. Sort workers by `(hostname, pc_id)` and digests lexicographically. Set the fleet `scope_sha256` to the canonical input scope digest and `observed_at` to the maximum worker timestamp. Emit only `canonical_json(FleetAttestation) + "\n"` to stdout. Reversing input argument order must be byte-identical.

Preflight's registry cross-check is exact and deterministic: aggregated hostname/`pc_id` pairs must equal `scope.participant_hosts` plus the live claimable registry rows. The DB remains the authority for heartbeat freshness/status/capability; the local artifact is the authority for process configuration, filesystem hashes, and environment-derived fingerprint/mapping keys. Neither alone is sufficient.

- [ ] **Step 8: Run green Task 2 tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_contracts.py \
  tests/scripts/test_fenced_lease_soak_attestation.py -q
```

Expected: all tests pass with no network, provider call, or database access. Captured stdout contains only canonical sanitized JSON.

- [ ] **Step 9: Commit Task 2**

```bash
git add scripts/fenced_lease_soak.py \
  tests/scripts/test_fenced_lease_soak_contracts.py \
  tests/scripts/test_fenced_lease_soak_attestation.py
git commit -m "feat(soak): derive trusted fleet attestations"
```

---

### Task 3: Build the read-only SQL store and fail-closed preflight

**Files:**
- Modify: `scripts/fenced_lease_soak.py`
- Create: `tests/scripts/test_fenced_lease_soak_preflight.py`
- Create: `tests/integration/test_fenced_lease_soak_db.py`

**Interfaces:**
- Produces protocol `SoakReadStore.collect(scope: SoakScope) -> RawSnapshot`.
- Produces `SqlSoakReadStore(database_url: str)` with pool size 1 / overflow 0 and application name `hcga-soak:<pid>`.
- Produces pure `evaluate_preflight(scope, attestation, raw) -> list[Finding]`.
- Produces `assert_scratch_database_url(database_url: str) -> None` for integration-test setup; it accepts database names containing `scratch` or ending `_test`, and rejects `edu_copy`, `edu_homework`, blank, or any other name before a seed/write fixture opens a connection.
- Every finding has a stable code, `hard: bool`, human message, and JSON evidence dictionary.

- [ ] **Step 1: Write pure preflight RED tests**

Use a `healthy_raw_snapshot()` factory and mutate one fact per test. Pin these codes:

```text
schema_revision_mismatch
scope_job_missing
scope_job_wrong_batch
scope_job_not_pristine
unrelated_active_queue_not_empty
staging_pause_missing_or_foreign
version_floor_mismatch
worker_attestation_stale
worker_registry_missing
worker_sha_mismatch
worker_config_mismatch
unattested_claimable_worker
credential_fingerprint_mismatch
pdf_missing_or_mismatch
book_checksum_scope_mismatch
notion_mapping_present
db_connection_baseline_high
db_idle_in_transaction
db_idle_in_transaction_timeout_unsafe
db_server_wait
credential_slot_baseline_nonzero
fleet_cost_envelope_exceeded
```

Representative bite tests:

```python
def test_preflight_fails_when_a_same_version_unattested_worker_can_claim():
    scope = valid_scope(expected_git_sha="fedcba9", expected_code_version=1001)
    raw = healthy_raw_snapshot()
    raw.workers.append(claimable_worker(
        pc_id="rogue:9@fedcba9", git_sha="fedcba9", code_version=1001
    ))
    findings = soak.evaluate_preflight(scope, valid_attestation(), raw)
    assert "unattested_claimable_worker" in hard_codes(findings)


@pytest.mark.parametrize("status", ["pending", "running", "cancelling"])
def test_preflight_rejects_unrelated_active_queue_rows(status):
    raw = healthy_raw_snapshot()
    raw.unrelated_jobs.append(job_row(status=status))
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert "unrelated_active_queue_not_empty" in hard_codes(findings)


def test_scoped_fresh_pending_jobs_are_expected_under_staging_pause():
    raw = healthy_raw_snapshot()
    assert {job.status for job in raw.jobs} == {"pending"}
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert "unrelated_active_queue_not_empty" not in hard_codes(findings)
    assert "scope_job_not_pristine" not in hard_codes(findings)


def test_preflight_requires_zero_idle_in_transaction_even_below_pool_limit():
    raw = healthy_raw_snapshot()
    raw.db.idle_in_transaction = [
        {"pid": 87104, "application_name": "hcga-worker:Host-40:1", "age_s": 301}
    ]
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert "db_idle_in_transaction" in hard_codes(findings)


@pytest.mark.parametrize("timeout_ms", [0, 300_001, 900_000])
def test_preflight_rejects_disabled_or_over_five_minute_idle_timeout(timeout_ms):
    raw = healthy_raw_snapshot()
    raw.db.idle_in_transaction_timeout_ms = timeout_ms
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert "db_idle_in_transaction_timeout_unsafe" in hard_codes(findings)


def test_preflight_uses_caller_supplied_exact_database_revision():
    scope = valid_scope(expected_db_revision="0054_source_integrity")
    raw = healthy_raw_snapshot()
    raw.schema.revision = "0054_source_integrity"
    findings = soak.evaluate_preflight(scope, attestation_for(scope), raw)
    assert "schema_revision_mismatch" not in hard_codes(findings)


def test_preflight_rejects_non_pristine_scoped_job():
    raw = healthy_raw_snapshot()
    raw.jobs[0].attempts = 1
    raw.jobs[0].claim_token = uuid4()
    findings = soak.evaluate_preflight(valid_scope(), valid_attestation(), raw)
    assert "scope_job_not_pristine" in hard_codes(findings)


def test_preflight_rejects_scope_pdf_hash_that_disagrees_with_book_row():
    scope = valid_scope(required_book_sha256={str(BOOK): "a" * 64})
    raw = healthy_raw_snapshot()
    raw.books[BOOK].content_sha256 = "b" * 64
    findings = soak.evaluate_preflight(scope, valid_attestation(), raw)
    assert "book_checksum_scope_mismatch" in hard_codes(findings)
```

- [ ] **Step 2: Run pure preflight RED tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_preflight.py -q
```

Expected: failures because `RawSnapshot`, the store protocol, and evaluator do not exist.

- [ ] **Step 3: Implement the read-only store**

Each call opens a connection and executes:

```python
async with self.engine.connect() as conn:
    async with conn.begin():
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        # SELECT-only snapshot queries follow
```

The store must issue bound-parameter queries, never interpolate UUIDs or timestamps. Required data:

1. `alembic_version.version_num`, existence of `job_lease_events`, and `claim_token` columns on both tables.
2. `budget_state` pause and exact version floor.
3. Exact scoped jobs joined to batches and books (including authoritative `books.content_sha256`); count of `pending`, `running`, or `cancelling` jobs outside the scope. Scoped freshly-created `pending` jobs are the expected staged state and are not counted as unrelated queue activity.
4. Workers fresh by the configured registry stale window, including `pc_id`, heartbeat age, status, capability `git_sha`, `code_version`, and Gemini API capability.
5. SA scrub tombstones needed to distinguish a technically online but parked hostname.
6. `pg_settings` values for `max_connections` and `superuser_reserved_connections`, plus the controller connection's effective `idle_in_transaction_session_timeout` normalized to integer milliseconds with `extract(epoch from current_setting(...)::interval)`. PostgreSQL cannot inspect another backend's per-session GUC; exact deployed SHA plus Task 1's hardcoded engine settings prove the worker-side contract, while this runtime read proves the controller's own connection is protected.
7. `pg_stat_activity`: total sessions, `idle in transaction` rows with PID/application/client/age/query prefix, and non-client wait events. Exclude the controller's own PID only from the idle/wait offender lists, not from the total count.
8. Fresh and stale `credential_slots`, grouped by credential fingerprint and holder process.
9. Scoped `job_lease_events`, `phase_outputs`, and `agent_usages` since `scope.since`.
10. Fleet API usage rows in the trailing 24 hours for the global envelope check.

Do not query worker `.env` values from PostgreSQL; exact configuration comes from the authenticated-channel-captured attestation and is cross-checked against the same `pc_id` heartbeat row. A worker is “claimable” for this gate only when heartbeat is fresh, status is `online`, Gemini API capability is true, its code version is at/above the floor, and its hostname has no scrub tombstone.

- [ ] **Step 4: Implement pure preflight evaluation**

The healthy gate requires:

- schema revision exactly equals caller-supplied `scope.expected_db_revision`, both token columns and the ledger table are present, and no revision value is baked into the controller;
- every scope job exists once, belongs to one of the scope batches, was created at/after `since`, is pending with attempts 0 / null token, and has zero phase, usage, or lease rows;
- every `required_book_sha256` value equals the corresponding scoped book's persisted `content_sha256`, so an operator cannot accidentally bless a uniformly wrong local file hash;
- scoped jobs may be `pending` under the exact staging pause; zero **unrelated** `pending`, `running`, or `cancelling` jobs may exist;
- fleet API pause is exactly `lease-soak-staging:<run-id>` for the initial preflight; a null pause is accepted only after the already-running watcher has emitted `READY_TO_RELEASE` and observed the operator clear that exact reason. Any other pause reason is preserved and is a hard preflight failure;
- floor equals expected code version;
- attestation is at most `attestation_max_age_seconds` old (300 seconds in the first scope) and heartbeats are at most `heartbeat_max_age_seconds` old (60 seconds in the first scope);
- every claimable worker appears in the attestation, and every attested worker appears claimable;
- attested hostnames equal `scope.participant_hosts`, and each attested `(hostname, pc_id)` exactly matches one live registry row; a locally correct config with a stale/restarted PID therefore fails instead of being silently accepted;
- at least `ceil(target_running / expected_worker_concurrency)` distinct worker processes and hosts;
- exact SHA/config/credential/PDF/Notion values match the scope;
- DB total is at most `db_preflight_connection_limit`, zero idle-in-transaction, zero non-client waits, and the controller's effective idle-in-transaction timeout is nonzero and at most 300,000 ms;
- zero fresh or stale credential slots;
- priced trailing-24h fleet cost plus the approved incremental cap is at most `fleet_cost_limit_usd`.

- [ ] **Step 5: Add real-Postgres read-only and scope tests**

In `tests/integration/test_fenced_lease_soak_db.py`, seed only a scratch DB and test:

```python
@pytest.mark.asyncio
async def test_collect_starts_a_read_only_transaction(store, seeded_scope):
    raw = await store.collect(seeded_scope)
    assert raw.scope_job_ids == seeded_scope.job_ids
    assert raw.transaction_read_only == "on"
    assert raw.db.idle_in_transaction_timeout_ms == 300_000


@pytest.mark.asyncio
async def test_read_store_cannot_write(store):
    async with store.read_connection() as conn:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await conn.execute(text("update homework_jobs set priority=priority"))


@pytest.mark.asyncio
async def test_collect_excludes_unscoped_usage_and_lease_events(
    store, seeded_scope, unscoped_job
):
    raw = await store.collect(seeded_scope)
    assert unscoped_job.id not in {r.job_id for r in raw.usages}
    assert unscoped_job.id not in {r.job_id for r in raw.lease_events}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://edu@127.0.0.1/edu_copy",
        "postgresql+asyncpg://edu@127.0.0.1/edu_homework",
        "postgresql+asyncpg://edu@127.0.0.1/postgres",
    ],
)
def test_seed_fixtures_refuse_non_scratch_database(url):
    with pytest.raises(RuntimeError, match="scratch database required"):
        soak.assert_scratch_database_url(url)
```

- [ ] **Step 6: Run green preflight tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_preflight.py \
  tests/integration/test_fenced_lease_soak_db.py -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  uv run pytest tests/integration/test_fenced_lease_soak_db.py -q
```

Expected: pure tests pass; integration skips without the flag and passes against scratch PostgreSQL.

- [ ] **Step 7: Commit Task 3**

```bash
git add scripts/fenced_lease_soak.py \
  tests/scripts/test_fenced_lease_soak_preflight.py \
  tests/integration/test_fenced_lease_soak_db.py
git commit -m "feat(soak): add fail-closed read-only preflight"
```

---

### Task 4: Evaluate lease, token, phase, provider, and cost integrity

**Files:**
- Modify: `scripts/fenced_lease_soak.py`
- Create: `tests/scripts/test_fenced_lease_soak_snapshot.py`

**Interfaces:**
- Produces `evaluate_runtime(scope, attestation, raw, previous_samples) -> list[Finding]`.
- Produces `price_scoped_usage(rows) -> UsageCost` using `app.services.pricing.cost_usd`.
- Produces `classify_error(text: str) -> ErrorClass | None` with stable values `provider_429`, `slot_exhaustion`, `auth`, `attempt_timeout`, `network`, and `other`.
- Findings distinguish `hard_stop=True` from `stage_failure=True`. Hard stops trigger the armed circuit breaker immediately. Quality failures latch while paid work already in flight finishes under the live watcher; at terminal they fail the read-only stage or trigger the armed exact-scope pause.

- [ ] **Step 1: Write error/cost RED tests**

```python
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("429 RESOURCE_EXHAUSTED", "provider_429"),
        ("fleet credential slot wait exhausted", "slot_exhaustion"),
        ("403 PERMISSION_DENIED invalid API key", "auth"),
        ("per-attempt timeout after 600s", "attempt_timeout"),
        ("connection reset by peer", "network"),
    ],
)
def test_error_classes_are_stable(message, expected):
    assert soak.classify_error(message).value == expected


def test_successful_api_usage_must_be_token_bearing_and_priced():
    usage = usage_row(model_name="unknown", success=True, total_tokens=0)
    scope = valid_scope()
    findings = soak.evaluate_runtime(
        scope, valid_attestation(scope), raw_with_usage(usage), []
    )
    assert "unpriced_or_tokenless_usage" in hard_codes(findings)
```

- [ ] **Step 2: Write fencing/phase RED tests**

Pin at least these codes:

```text
lease_lost
job_reclaimed
job_retried_or_failed
claim_event_mismatch
claim_owner_underdistributed
running_without_token
phase_token_mismatch
duplicate_phase
orphan_phase
phase_set_incomplete
heartbeat_stale
db_connection_hard_stop
db_idle_in_transaction
db_server_wait
credential_slot_exhausted
provider_or_auth_error
unpriced_or_tokenless_usage
incremental_cost_cap
unexpected_notion_archive
notion_outcome_missing
quality_major_shipped
solver_mismatch
```

Representative tests:

```python
def test_old_claim_cannot_leave_a_phase_with_foreign_token():
    raw = healthy_completed_snapshot(target=4)
    raw.phases[0].claim_token = uuid4()
    scope = valid_scope(target=4)
    findings = soak.evaluate_runtime(scope, valid_attestation(scope), raw, [])
    assert "phase_token_mismatch" in hard_codes(findings)


def test_two_high_db_samples_are_hard_but_one_is_not():
    scope = valid_scope(db_hard_stop_connection_limit=85)
    first = healthy_running_snapshot(db_total=85)
    attestation = valid_attestation(scope)
    one = soak.evaluate_runtime(scope, attestation, first, [])
    assert "db_connection_hard_stop" not in hard_codes(one)
    second = healthy_running_snapshot(db_total=86)
    two = soak.evaluate_runtime(scope, attestation, second, [first])
    assert "db_connection_hard_stop" in hard_codes(two)


def test_quality_failure_quarantines_but_does_not_emergency_pause():
    raw = healthy_completed_snapshot(target=4)
    raw.phases[0].judge_status = "major_shipped"
    finding = by_code(
        soak.evaluate_runtime(
            valid_scope(target=4), valid_attestation(valid_scope(target=4)), raw, []
        ),
        "quality_major_shipped",
    )
    assert finding.stage_failure is True
    assert finding.hard_stop is False
```

- [ ] **Step 3: Run RED tests**

Run:

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_snapshot.py -q
```

Expected: failures because runtime evaluators do not exist.

- [ ] **Step 4: Implement exact runtime rules**

For a clean completed stage:

- observed maximum simultaneous `running` jobs reaches `target_running`;
- every job has attempts 1 and exactly one `claimed` plus one `released_done` event for its retained token;
- no `lease_lost`, `reclaimed_stale`, `reclaimed_forced`, `released_retry`, `released_failed`, or `released_cancelled` event exists;
- distinct claim owners are at least `ceil(job_count / expected_worker_concurrency)`;
- every running/terminal claimed job retains a non-null token;
- every phase token equals its job token;
- each job has exactly `extract` plus `flow_for(job.subject)` (or the exact selected subset if `selected_phases` is non-null), with unique phase names and orders, all done;
- every participant's live registry `pc_id` still equals the immutable preflight-attested `pc_id`, and no heartbeat exceeds the configured age for two samples;
- no DB hard threshold, idle transaction, or non-client wait appears;
- active credential slots never exceed `expected_credential_max_concurrent_gemini`; any slot-wait exhaustion text is an immediate hard stop;
- every scoped usage row is pinned to provider `gemini` and auth mode `api`; every failed row hard-stops even when its error text is blank; every successful row has a known price, positive tokens, and positive calculated cost;
- exact operation/model routing matches `expected_models_by_operation_prefix`: exact keys `phase.run`, `lesson.extract`, `lesson.extract.coverage`, and `lesson.extract.verify`, plus prefixes `judge:` and `solve:`; any unknown operation remains fail-closed;
- cumulative scoped cost is below the approved cap;
- every scoped job has no Notion stamp and has a non-empty skip reason;
- `major_shipped`, `major_regen_failed`, solver mismatch, or validation corruption latches the stage as failed for distribution. It does not interrupt in-flight jobs, but an armed watcher pauses the exact scope once all scoped jobs are terminal so escalation cannot proceed accidentally.

Calculate cost in Python with `pricing.cost_usd`, never a hand-maintained SQL rate table. Preserve raw token counts/model names in evidence but not provider envelopes.

- [ ] **Step 5: Mutation/bite proof the critical rules**

Temporarily mutate each condition locally and confirm its named test fails:

1. Remove `phase.claim_token != job.claim_token` detection.
2. Change the cost comparison from `>=` to `>`.
3. Remove `reclaimed_stale` from the hard event set.
4. Treat `major_shipped` as clean.

Restore each mutation before proceeding. Record the four failing test names in the task review notes; do not commit the mutations.

- [ ] **Step 6: Run green runtime tests**

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_snapshot.py -q
```

- [ ] **Step 7: Commit Task 4**

```bash
git add scripts/fenced_lease_soak.py tests/scripts/test_fenced_lease_soak_snapshot.py
git commit -m "feat(soak): enforce lease and paid-usage integrity"
```

---

### Task 5: Add deterministic watch, quiet settle, and crash-safe JSON evidence

**Files:**
- Modify: `scripts/fenced_lease_soak.py`
- Create: `tests/scripts/test_fenced_lease_soak_watch.py`

**Interfaces:**
- Produces `ArtifactWriter(artifact_dir: Path, run_id: str)` with append-only JSONL samples and atomic final summary.
- Produces `run_preflight(...) -> ExitCode` and `run_watch(..., clock, sleep) -> ExitCode` with injected clock/sleep for deterministic tests.
- Artifact files: `<run-id>.samples.jsonl` and `<run-id>.summary.json`.

- [ ] **Step 1: Write watch-loop RED tests**

```python
@pytest.mark.asyncio
async def test_watch_reaches_target_then_requires_sixty_clean_seconds(tmp_path):
    store = FakeStore([
        pristine_staged_snapshot(pause_reason="lease-soak-staging:stage-04"),
        healthy_running_snapshot(running=4, pause_reason=None),
        healthy_completed_snapshot(target=4, at_seconds=0),
        healthy_completed_snapshot(target=4, at_seconds=30),
        healthy_completed_snapshot(target=4, at_seconds=60),
    ])
    code = await soak.run_watch(
        scope=valid_scope(target=4, settle_seconds=60),
        attestation=valid_attestation(workers=2),
        store=store,
        writer=soak.ArtifactWriter(tmp_path, "stage-04"),
        stopper=None,
        clock=FakeClock(),
        sleep=FakeSleep(),
        interval_seconds=2,
    )
    assert code == soak.ExitCode.PASS


@pytest.mark.asyncio
async def test_read_only_hard_stop_records_and_exits_without_writes(tmp_path):
    store = FakeStore([snapshot_with_lease_lost()])
    stopper = AsyncMock()
    code = await run_unarmed_watch(store, stopper, tmp_path)
    assert code == soak.ExitCode.HARD_STOP_READ_ONLY
    stopper.assert_not_awaited()


def test_artifact_is_redacted_append_only_and_summary_is_atomic(tmp_path):
    writer = soak.ArtifactWriter(tmp_path, "stage-04")
    writer.append(sample_with_safe_fingerprint())
    writer.finish(summary_pass())
    assert len((tmp_path / "stage-04.samples.jsonl").read_text().splitlines()) == 1
    assert not (tmp_path / "stage-04.summary.json.tmp").exists()
    text = (tmp_path / "stage-04.summary.json").read_text()
    assert "GEMINI_API_KEY" not in text
    assert "DATABASE_URL" not in text
```

- [ ] **Step 2: Run watch RED tests**

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_watch.py -q
```

- [ ] **Step 3: Implement the loop and artifact writer**

Rules:

- `preflight` writes one sample plus final summary and exits 0 only with zero hard findings.
- `watch` runs the same pristine-job preflight while the exact staging pause is still active. It emits `READY_TO_RELEASE`, keeps sampling read-only, and waits for that exact pause to become null. This closes the launch-to-monitor race: the operator clears the staging pause only after the watcher is live. It refuses to monitor a dirty stage rather than “watching through” a bad baseline.
- `watch` passes the immutable preflight `FleetAttestation` into every runtime evaluation; a same-host replacement process with a different `pc_id` is drift, even when SHA/version/capability still match.
- Append one canonical JSON object per sample, flush and `os.fsync` before sleeping.
- On first terminal sample, start the settle clock. Any new lease event, usage row, phase change, heartbeat breach, or finding resets the settle clock.
- A stage cannot pass unless the observed running peak reached its target.
- On completion, write the summary to a same-directory temporary file, flush/fsync, and `os.replace` it into place.
- Before `READY_TO_RELEASE`, SIGINT/SIGTERM writes `verdict="incomplete"`, preserves samples, and exits 5 without mutation. From the instant release authorization is emitted, an armed watcher shields and completes the exact-scope pause on cancellation or operational failure; a read-only watcher still records an incomplete/error verdict without mutation.
- On any hard finding, write the offending sample and summary before returning the hard-stop exit code.

- [ ] **Step 4: Run green watch tests**

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_watch.py -q
```

- [ ] **Step 5: Commit Task 5**

```bash
git add scripts/fenced_lease_soak.py tests/scripts/test_fenced_lease_soak_watch.py
git commit -m "feat(soak): record deterministic staged evidence"
```

---

### Task 6: Add the guarded, exact-scope stop writer

**Files:**
- Modify: `scripts/fenced_lease_soak.py`
- Create: `tests/scripts/test_fenced_lease_soak_stop.py`
- Modify: `tests/integration/test_fenced_lease_soak_db.py`

**Interfaces:**
- Produces protocol `SoakStopper.pause(scope: SoakScope, trigger: Finding) -> StopReceipt`.
- Produces protocol `SoakWriteStore` for one locked transaction, `SqlSoakWriteStore` as the only class allowed to execute SQL writes, and `GuardedStopper(write_store: SoakWriteStore)` as the decision layer used by the CLI.
- Pause reason is exactly `lease-soak-stop:<run-id>` and must fit `Batch.paused_reason` / `BudgetState.api_paused_reason` 64-character columns; contract validation limits `run_id` accordingly.

- [ ] **Step 1: Write pure stop RED tests**

```python
@pytest.mark.asyncio
async def test_armed_stop_pauses_exact_batches_and_fleet_but_never_jobs():
    writer = FakeWriteStore()
    stopper = soak.GuardedStopper(writer)
    receipt = await stopper.pause(valid_scope(batch_ids=[B1, B2]), lease_lost_finding())
    assert writer.paused_batches == {B1, B2}
    assert writer.fleet_pause_reason == "lease-soak-stop:stage-04"
    assert writer.job_updates == []
    assert receipt.cancelled_jobs == 0


@pytest.mark.asyncio
async def test_foreign_fleet_and_batch_pauses_are_preserved():
    writer = FakeWriteStore(
        fleet_reason="manual-operator", batch_reasons={B1: "manual"}
    )
    receipt = await soak.GuardedStopper(writer).pause(
        valid_scope(batch_ids=[B1, B2]), lease_lost_finding()
    )
    assert writer.fleet_pause_reason == "manual-operator"
    assert writer.batch_reasons[B1] == "manual"
    assert writer.batch_reasons[B2] == "lease-soak-stop:stage-04"
    assert receipt.foreign_fleet_pause_preserved is True


@pytest.mark.asyncio
async def test_unarmed_watch_can_never_construct_sql_stopper(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unarmed path constructed a SQL writer")

    monkeypatch.setattr(soak, "SqlSoakWriteStore", forbidden)
    assert await soak.async_main(unarmed_args()) in {
        soak.ExitCode.PASS,
        soak.ExitCode.HARD_STOP_READ_ONLY,
    }
```

- [ ] **Step 2: Run stop RED tests**

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_stop.py -q
```

- [ ] **Step 3: Implement one transactional stop action**

Inside one write transaction:

1. Lock `budget_state id=1` and every scope batch row `FOR UPDATE` in sorted UUID order.
2. Abort without writing if any scope batch is missing.
3. Re-read all scope job-to-batch memberships; abort if any job moved outside the exact batch set.
4. If fleet pause is null, set it to this run's stop reason. If it already equals the stop reason, leave it. If it equals this run's `lease-soak-staging:<run-id>` reason, replace it with the stop reason. If it holds any other reason, preserve it and record that fact.
5. For each exact batch: if unpaused, pause with this run's reason; if already paused by this run, leave it; if paused by another reason, preserve it.
6. Commit once and return row counts/reasons. No job-table statement is permitted.

Do not import or call `cancel_all_in_batch`, `retry_job`, `resume_failed_in_batch`, `clear_api_paused`, `unpause_batch`, or `unpause_by_reason` anywhere in the script.

`async_main` constructs `SqlSoakWriteStore` and wraps it in `GuardedStopper` only after both arm checks pass. Unarmed paths pass `stopper=None`; they do not construct a write-capable engine.

- [ ] **Step 4: Add scratch-DB exact-write tests**

```python
@pytest.mark.asyncio
async def test_stop_mutates_only_exact_batches_and_budget_state(
    seeded_scope, unrelated_batch, sql_stopper
):
    receipt = await sql_stopper.pause(seeded_scope, lease_lost_finding())
    assert receipt.batches_paused == len(seeded_scope.batch_ids)
    assert await batch_reason(unrelated_batch.id) is None
    assert await count_job_status_changes() == 0


@pytest.mark.asyncio
async def test_stop_rolls_back_everything_on_scope_drift(
    seeded_scope, sql_stopper
):
    await move_one_job_to_another_batch()
    with pytest.raises(soak.ScopeDrift):
        await sql_stopper.pause(seeded_scope, lease_lost_finding())
    assert await all_scope_batch_reasons() == [None, None]
    assert await fleet_pause_reason() is None
```

- [ ] **Step 5: Run green stop tests**

```bash
uv run pytest tests/scripts/test_fenced_lease_soak_stop.py \
  tests/integration/test_fenced_lease_soak_db.py -q
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  uv run pytest tests/integration/test_fenced_lease_soak_db.py -q
```

- [ ] **Step 6: Mutation-proof the no-cancel and foreign-pause guards**

Temporarily add a job status update: the exact-write integration test must fail. Temporarily overwrite a foreign pause: the preservation unit test must fail. Restore both mutations before committing.

- [ ] **Step 7: Commit Task 6**

```bash
git add scripts/fenced_lease_soak.py \
  tests/scripts/test_fenced_lease_soak_stop.py \
  tests/integration/test_fenced_lease_soak_db.py
git commit -m "feat(soak): pause exact scope on armed hard stops"
```

---

### Task 7: End-to-end `$0` acceptance and operator handoff

**Files:**
- Modify: `tests/scripts/test_fenced_lease_soak_watch.py`
- Modify: `tests/integration/test_fenced_lease_soak_db.py`
- Modify: `scripts/fenced_lease_soak.py` only if acceptance exposes a defect

**Interfaces:**
- This task freezes the CLI and artifact schema. Later paid operation consumes it without code edits.

- [ ] **Step 1: Add a full fake-store stage progression test**

Drive the real `async_main` for four independent fake stages (the 40 stage uses two batch IDs):

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "jobs", "batches", "workers"),
    [(4, 4, 1, 2), (8, 8, 1, 4), (12, 12, 1, 6),
     (20, 20, 1, 10), (40, 40, 2, 20)],
)
async def test_full_stage_reaches_target_settles_and_emits_pass(
    target, jobs, batches, workers, tmp_path
):
    scope_path = write_scope(tmp_path, target=target, jobs=jobs, batches=batches)
    attestation_path = write_attestation(tmp_path, workers=workers)
    fake = full_clean_stage_store(target=target, jobs=jobs)
    code = await soak.async_main(
        ["watch", "--scope", str(scope_path), "--attestation",
         str(attestation_path), "--artifact-dir", str(tmp_path)],
        store_factory=lambda *_: fake,
        clock=FakeClock(),
        sleep=FakeSleep(),
    )
    assert code == soak.ExitCode.PASS
    summary = json.loads(next(tmp_path.glob("*.summary.json")).read_text())
    assert summary["verdict"] == "pass"
    assert summary["peaks"]["running_jobs"] == target
    assert summary["lease_events"]["claimed"] == jobs
    assert summary["lease_events"]["released_done"] == jobs
```

- [ ] **Step 2: Add a full armed-stop acceptance test**

The real CLI receives a healthy sample followed by a foreign-token phase. Assert it writes the violating sample, pauses only the two named batches and fleet, writes a receipt, preserves every job status, and exits 4.

- [ ] **Step 3: Run the complete focused suite**

```bash
uv run pytest \
  tests/test_db_pool_config.py \
  tests/integration/test_db_session_settings.py \
  tests/scripts/test_fenced_lease_soak_contracts.py \
  tests/scripts/test_fenced_lease_soak_attestation.py \
  tests/scripts/test_fenced_lease_soak_preflight.py \
  tests/scripts/test_fenced_lease_soak_snapshot.py \
  tests/scripts/test_fenced_lease_soak_watch.py \
  tests/scripts/test_fenced_lease_soak_stop.py \
  tests/integration/test_fenced_lease_soak_db.py -q
```

Expected: all unit tests pass; DB tests skip without `RUN_DB_INTEGRATION=1`.

- [ ] **Step 4: Run scratch-Postgres acceptance**

```bash
RUN_DB_INTEGRATION=1 DATABASE_URL="$SCRATCH_DATABASE_URL" \
  uv run pytest tests/integration/test_db_session_settings.py \
    tests/integration/test_fenced_lease_soak_db.py -q
```

Expected: read-only enforcement, application/timeout settings, exact scope, and armed stop writes all pass. This is `$0`; no model function is imported or called.

- [ ] **Step 5: Run canonical verification and safety scans**

```bash
uv run pytest -q
rg -n 'cancel_all_in_batch|retry_job|resume_failed_in_batch|clear_api_paused|unpause_batch|unpause_by_reason' \
  scripts/fenced_lease_soak.py
rg -n 'run_phase|_gemini|google\.genai|generate_content|anthropic|openai' \
  scripts/fenced_lease_soak.py
rg -n 'GEMINI_API_KEY|DATABASE_URL|PRIVATE|secret|password' \
  tests/scripts/test_fenced_lease_soak_*.py
git diff --check origin/Nggaev-v2...
```

Expected: canonical suite green; the forbidden mutation and provider-call greps return no script hits; secret strings exist only in negative test fixtures; diff check is clean.

- [ ] **Step 6: Re-run the collision gate and request review**

```bash
git fetch --all --prune
git worktree list --porcelain
gh pr list --state open --limit 100 \
  --json number,title,headRefName,baseRefName,author,isDraft,mergeStateStatus
git diff --name-status origin/Nggaev-v2...
```

If the base or an overlapping lane moved, stop and establish integration order before pushing. Otherwise use `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch`. Open a PR for external gate; do not self-merge.

- [ ] **Step 7: Commit Task 7**

```bash
git add scripts/fenced_lease_soak.py \
  tests/scripts/test_fenced_lease_soak_watch.py \
  tests/integration/test_fenced_lease_soak_db.py
git commit -m "test(soak): prove staged lease controller end to end"
```

## Deployment, Rollback, and Paid Operator Order

### `$0` code deployment

1. Merge the externally gated controller PR.
2. From a fully fetched, non-shallow checkout of the final merged `Nggaev-v2`, derive the only identity allowed in this soak:

```bash
git fetch origin Nggaev-v2
git switch Nggaev-v2
git pull --ff-only origin Nggaev-v2
test "$(git rev-parse --is-shallow-repository)" = false
FINAL_GIT_SHA="$(git rev-parse --short HEAD)"
FINAL_CODE_VERSION="$(git rev-list --count HEAD)"
printf 'FINAL_GIT_SHA=%s\nFINAL_CODE_VERSION=%s\n' \
  "$FINAL_GIT_SHA" "$FINAL_CODE_VERSION"
```

Write those exact caller-supplied values into every stage scope and fleet attestation. Do not copy the planning baseline `d6b1c9f`/987.

3. Pull that same final merge on the head and every participating worker. Verify each checkout reports exactly `$FINAL_GIT_SHA` and `$FINAL_CODE_VERSION`; then set the fleet floor to exactly `$FINAL_CODE_VERSION`:

```bash
curl --fail-with-body -X PUT "$HEAD_URL/api/v1/workers/version-floor" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "{\"value\":$FINAL_CODE_VERSION}"
```

A same-version/different-SHA worker is not eligible even though the numeric claim gate would admit it.
4. Restart the head and workers so new physical connections receive `application_name` and the five-minute idle-transaction timeout.
5. Verify the restarted process settings on the head and on one worker host using that host's real `.env`:

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from app.db import engine

async def main():
    async with engine.connect() as conn:
        values = await conn.execute(text(
            "select current_setting('application_name'), "
            "current_setting('idle_in_transaction_session_timeout')"
        ))
        print(tuple(values.one()))
    await engine.dispose()

asyncio.run(main())
PY
```

Expected: the head prints `hcga-head:...` and `5min`; the worker prints `hcga-worker:...` and `5min`. Before any paid launch require zero `idle in transaction` rows and DB total connections `<=70`.

6. Verify the authenticated fleet-management channel can execute `uv run python scripts/fenced_lease_soak.py --help` and capture stdout from every participant. No remote filesystem read is required by the attestation flow.
7. Run `attest-local`, `attest-aggregate`, and `preflight` against hermetic fixture scopes in the focused/canonical tests. This remains `$0`; real fleet artifacts are produced only after a real staged scope exists.

Rollback: revert the PR, deploy the revert to head/workers, and restart so new connections stop receiving the settings. Existing connections retain their old per-session settings until closed. The controller creates no migration and no persistent application state unless explicitly armed during a later soak. If an armed stop fired, rollback does **not** clear it; the operator must inspect the evidence and separately clear only the exact pause reason after deciding it is safe.

### Later paid soak — explicit approval required

This plan does not authorize the following commands. They are the exact handoff once an operator approves a hard cap.

For each stage, first hold API claims with the exact operator-owned staging pause. The current app has no public endpoint for this global gate, so use a guarded SQL update and verify exactly one returned row before launching:

```bash
psql "$DATABASE_URL_PSQL" -X -v ON_ERROR_STOP=1 <<'SQL'
update budget_state
set api_paused_at = now(),
    api_paused_reason = 'lease-soak-staging:stage-04-20260810'
where id = 1 and api_paused_at is null
returning id, api_paused_reason;
SQL
```

If the command returns zero rows, stop: a foreign pause already exists and must not be overwritten. With staging active, create fresh jobs through the normal batch API, then capture exact resolved batch and job IDs plus the exact participant hostname list into `scope-<level>.json`.

On each listed participant, use the authenticated fleet-management channel to execute this command from the final deployed checkout. Feed the identical scope bytes on stdin and capture stdout centrally; the worker needs neither a copied scope file nor a writable artifact directory:

```bash
uv run python scripts/fenced_lease_soak.py attest-local --scope - \
  < scope-04.json > Host-02.attestation.json
```

The `Host-02` filename is central bookkeeping only; the artifact's hostname/`pc_id` comes from the worker itself. Repeat for every `participant_hosts` entry. Any stderr, nonzero exit, missing host, or second worker process blocks the stage.

Aggregate the collected artifacts in any input order; canonical sorting makes the output byte-identical:

```bash
uv run python scripts/fenced_lease_soak.py attest-aggregate \
  --scope scope-04.json \
  --input Host-03.attestation.json \
  --input Host-02.attestation.json \
  > fleet-attestation-04.json
```

Pass that generated artifact—not hand-authored JSON—to preflight:

```bash
uv run python scripts/fenced_lease_soak.py preflight \
  --scope ops/soak/scope-04.json \
  --attestation ops/soak/fleet-attestation-04.json \
  --artifact-dir ops/soak/evidence
```

After `preflight` exits 0, start the watcher **while the staging pause is still present**:

```bash
uv run python scripts/fenced_lease_soak.py watch \
  --scope ops/soak/scope-04.json \
  --attestation ops/soak/fleet-attestation-04.json \
  --artifact-dir ops/soak/evidence \
  --interval-seconds 2 \
  --arm-stop \
  --confirm-arm lease-soak-stop:stage-04-20260810
```

Wait for the watcher to print `READY_TO_RELEASE`. In a second terminal, clear only this run's staging reason:

```bash
psql "$DATABASE_URL_PSQL" -X -v ON_ERROR_STOP=1 <<'SQL'
update budget_state
set api_paused_at = null, api_paused_reason = null
where id = 1
  and api_paused_reason = 'lease-soak-staging:stage-04-20260810'
returning id;
SQL
```

Require exactly one returned row. Zero means the state changed after preflight; leave the watcher/evidence intact and investigate instead of clearing another reason. The live watcher observes the transition and begins stage timing without a claim-monitor race.

Repeat with independent fresh scopes for 8, 12, 20, then 40. Never reuse completed jobs to fake load. The 40 scope names two 20-job batches.

Expected launch routing in every scope:

```json
{
  "phase.run": "gemini-3.6-flash",
  "lesson.extract": "gemini-3.5-flash-lite",
  "lesson.extract.coverage": "gemini-3.5-flash",
  "lesson.extract.verify": "gemini-3.5-flash-lite",
  "judge:": "gemini-3.5-flash",
  "solve:": "gemini-3.1-pro-preview"
}
```

This example records the intended final deployed stack, but it is not an evergreen source of truth. Generate and verify each soak scope from the final deployed configuration only after every model/config dependency has landed; do not copy these values blindly into a scope prepared against a different deployment.

Candidate books, after rechecking live TOCs/PDFs, are:

```text
57c00a8e-1521-4a1b-bf86-0908b7c0f86f
5b41e082-3378-46e3-882a-03b2d8357516
cedded0f-731f-4cd5-b47b-2a79f563a7f9
f087fb01-0c9d-4762-9d14-2898d7f30e67
0ab32b4d-a48a-437b-bcd1-bc31b35604a8
```

Use English output on these Russian math/geometry sources only after every participating attestation proves the corresponding English Notion mapping absent and the same PDF checksum present. Do not force-regenerate the existing Russian jobs.

Hard-stop triggers: any unrelated active job after release authorization, lease loss/reclaim, token mismatch, duplicate/orphan phase, retry/failure/cancel, provider 429, slot exhaustion, authentication error, attempt timeout, unexpected Notion archive, cost at/above the approved cap, heartbeat older than 60 seconds for two samples, any idle-in-transaction session, or DB connections at/above 85 for two samples. In armed mode, a latched terminal quality/solver/corruption failure and any post-authorization incomplete or operational exit also exact-scope pause.

Stage pass: target concurrency observed; every job claimed once/released done once/attempts 1; owner distribution consistent with W=2; exact 12 phases done with matching tokens; no hard or quality findings; successful usage token-bearing and priced; no unexpected Notion write; DB/heartbeat/slot limits clean; and 60 seconds of quiet settle. Escalation is forbidden after a failed stage.

## Self-Review Record

- **Spec coverage:** DB recurring-risk hardening is Task 1; exact immutable scope, executable secret-free local attestation, deterministic aggregation, and two-gesture mutation gate are Task 2; migration/floor/config/registry-attestation/DB/slot/queue preflight is Task 3; fencing/error/pricing/phase/Notion gates Task 4; JSON evidence and settle Task 5; exact stop writes Task 6; synthetic 4→40 and scratch-DB acceptance Task 7. `$0` and paid gates are separated in Global Constraints and the operator handoff.
- **Placeholder scan:** no `TBD`, `TODO`, “implement later”, “similar to”, or unspecified error-handling steps remain. Every task names files, interfaces, failing tests, implementation shape, commands, and expected outcomes.
- **Type consistency:** `SoakScope`, `FleetAttestation`, `RawSnapshot`, `Finding`, `ArtifactWriter`, `SoakReadStore`, `SoakStopper`, `StopReceipt`, and `ExitCode` keep the same names/signatures across all tasks. The only DB writer is `SqlSoakStopper`; the default store is read-only.
- **Safety correction:** exact worker configuration cannot be truthfully inferred from today's heartbeat blob or trusted when hand-authored. The plan requires `attest-local` to derive a fresh sanitized artifact from each process, deterministic aggregation over the exact host set, and a live `pc_id`/SHA/version cross-check against PostgreSQL; it does not manufacture confidence from incomplete registry data.
- **Cost correction:** the historical `$135` proposal is a stop limit, not granted authority. The implementation and acceptance lane remain `$0`.
