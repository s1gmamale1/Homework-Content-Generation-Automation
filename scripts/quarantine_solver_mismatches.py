"""Quarantine historically shipped solver-confirmed answer-key mismatches.

The default is a read-only dry run.  The sole mutating gesture is::

    --apply --expect-plan-hash HASH --manifest-out PATH

``DATABASE_URL`` must be present in the raw process environment.  This module
deliberately imports no ``app.*`` module, never calls Notion or a model, and
preserves every archival pointer/timestamp as evidence.

Only jobs pinned to the current four-role model/transport tuple are executable.
Rows carrying retired Gemini models are reported as evidence but excluded from
the plan hash, manifest, and writes; they need a separately designed in-place
restamp path.  After an operator applies a reviewed eligible manifest, each
affected job is ``failed`` and visible through the normal retry UI.  Retry it in
place so completed clean siblings stay cached and only the blocked phase
regenerates.  A clean retry of an unarchived job may then archive normally.  A
previously archived job must not be force-rearchived until the separately
shipped R26 collision repair has actually been executed on production; only
then use the existing guarded ``retry-archive?force=true`` path.  This script
itself never changes Notion.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


DATABASE_URL_ERROR = (
    "DATABASE_URL must be set explicitly — refusing to guess the target DB"
)
REMEDIATION_ERROR = (
    "historical quarantine: solver-confirmed answer-key mismatch was shipped"
)
CURRENT_ROLE_TUPLE = {
    "provider": "gemini",
    "model": "gemini-3.6-flash",
    "transport": "api",
    "extract_provider": "gemini",
    "extract_model": "gemini-3.5-flash-lite",
    "extract_transport": "api",
    "judge_provider": "gemini",
    "judge_model": "gemini-3.5-flash",
    "judge_transport": "api",
    "solver_provider": "gemini",
    "solver_model": "gemini-3.1-pro-preview",
    "solver_transport": "api",
}
RETIRED_GEMINI_MODELS = frozenset(
    {"gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"}
)


class PreflightError(RuntimeError):
    """The requested gesture is unsafe or incomplete."""


class PlanChangedError(RuntimeError):
    """The live candidate snapshot no longer matches the reviewed snapshot."""


class ApplyStateDriftError(RuntimeError):
    """A guarded row did not retain the expected state."""


class UnsupportedRoleTupleError(RuntimeError):
    """A historical candidate is neither current nor a known retired tuple."""


@dataclass(frozen=True)
class ManifestTarget:
    target: Path
    parent: Path


@dataclass(frozen=True)
class StagedManifest:
    target: Path
    temporary: Path


@dataclass(frozen=True)
class RemediationPhase:
    phase_output_id: UUID
    job_id: UUID
    phase_name: str
    phase_status: str
    solver_status: str
    output_sha256: str
    phase_completed_at: datetime | None


@dataclass(frozen=True)
class RemediationJob:
    job_id: UUID
    toc_entry_id: UUID
    job_status: str
    job_completed_at: datetime | None
    notion_archived_at: datetime | None
    notion_skip_reason: str | None
    claim_token: UUID | None
    notion_archived_job_id: UUID | None
    provider: str
    model: str
    transport: str
    extract_provider: str
    extract_model: str
    extract_transport: str
    judge_provider: str
    judge_model: str
    judge_transport: str
    solver_provider: str
    solver_model: str
    solver_transport: str
    phases: tuple[RemediationPhase, ...]


@dataclass(frozen=True)
class RemediationScope:
    eligible_current_tuple: tuple[RemediationJob, ...]
    blocked_retired_tuple: tuple[RemediationJob, ...]


_CANDIDATE_SQL = """
SELECT po.id AS phase_output_id,
       po.job_id,
       po.phase_name,
       po.status AS phase_status,
       po.solver_status,
       encode(sha256(convert_to(coalesce(po.output_md, ''), 'UTF8')), 'hex')
           AS output_sha256,
       po.completed_at AS phase_completed_at,
       j.toc_entry_id,
       j.status AS job_status,
       j.completed_at AS job_completed_at,
       j.notion_archived_at,
       j.notion_skip_reason,
       j.claim_token,
       j.provider,
       j.model,
       j.transport,
       j.extract_provider,
       j.extract_model,
       j.extract_transport,
       j.judge_provider,
       j.judge_model,
       j.judge_transport,
       j.solver_provider,
       j.solver_model,
       j.solver_transport,
       t.notion_archived_job_id
FROM phase_outputs po
JOIN homework_jobs j ON j.id = po.job_id
JOIN toc_entries t ON t.id = j.toc_entry_id
WHERE po.solver_status = 'mismatch_shipped'
  AND po.status = 'done'
  AND j.status = 'done'
ORDER BY po.completed_at NULLS LAST, po.id
"""


def preflight_database_url(environ: Mapping[str, str]) -> str:
    database_url = environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise PreflightError(DATABASE_URL_ERROR)
    return database_url


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _phase_state(phase: RemediationPhase) -> dict:
    return {
        "phase_output_id": str(phase.phase_output_id),
        "job_id": str(phase.job_id),
        "phase_name": phase.phase_name,
        "phase_status": phase.phase_status,
        "solver_status": phase.solver_status,
        "output_sha256": phase.output_sha256,
        "phase_completed_at": _timestamp(phase.phase_completed_at),
    }


def _role_state(job: RemediationJob) -> dict[str, str]:
    return {field: getattr(job, field) for field in CURRENT_ROLE_TUPLE}


def _job_state(job: RemediationJob) -> dict:
    return {
        "job_id": str(job.job_id),
        "toc_entry_id": str(job.toc_entry_id),
        "job_status": job.job_status,
        "job_completed_at": _timestamp(job.job_completed_at),
        "notion_archived_at": _timestamp(job.notion_archived_at),
        "notion_skip_reason": job.notion_skip_reason,
        "claim_token": str(job.claim_token) if job.claim_token else None,
        "notion_archived_job_id": (
            str(job.notion_archived_job_id) if job.notion_archived_job_id else None
        ),
        **_role_state(job),
        "phases": sorted(
            (_phase_state(phase) for phase in job.phases),
            key=lambda phase: phase["phase_output_id"],
        ),
    }


def split_scope(jobs: Sequence[RemediationJob]) -> RemediationScope:
    """Separate executable current tuples from evidence-only retired tuples."""
    eligible: list[RemediationJob] = []
    retired: list[RemediationJob] = []
    for job in sorted(jobs, key=lambda candidate: str(candidate.job_id)):
        roles = _role_state(job)
        if roles == CURRENT_ROLE_TUPLE:
            eligible.append(job)
            continue
        has_retired_role = any(
            roles[f"{prefix}provider"] == "gemini"
            and roles[f"{prefix}model"] in RETIRED_GEMINI_MODELS
            for prefix in ("", "extract_", "judge_", "solver_")
        )
        if has_retired_role:
            retired.append(job)
            continue
        raise UnsupportedRoleTupleError(
            f"job {job.job_id} has unsupported historical role tuple: {roles}"
        )
    return RemediationScope(tuple(eligible), tuple(retired))


def expected_state(plan: Sequence[RemediationJob]) -> dict:
    return {
        "jobs": sorted(
            (_job_state(job) for job in plan), key=lambda job: job["job_id"]
        )
    }


def plan_hash(plan: Sequence[RemediationJob]) -> str:
    canonical = json.dumps(
        expected_state(plan), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest_for_plan(plan: Sequence[RemediationJob]) -> dict:
    return {
        "version": 1,
        "plan_hash": plan_hash(plan),
        **expected_state(plan),
    }


def validate_manifest_target(path: str | Path) -> ManifestTarget:
    """Validate destination semantics before opening a write transaction."""
    target = Path(path)
    parent = target.parent
    if not target.name or target.name in {".", ".."}:
        raise PreflightError("--manifest-out must name a file")
    if not parent.exists() or not parent.is_dir():
        raise PreflightError(
            f"--manifest-out parent directory does not exist: {parent}"
        )
    if target.exists() or target.is_symlink():
        raise PreflightError(
            f"--manifest-out already exists; refusing to overwrite: {target}"
        )
    if not os.access(parent, os.W_OK | os.X_OK):
        raise PreflightError(
            f"--manifest-out parent directory is not writable: {parent}"
        )
    return ManifestTarget(target=target, parent=parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_manifest_durable(
    target: ManifestTarget, manifest: Mapping
) -> StagedManifest:
    """Write+fsync a hidden artifact without publishing the final path."""
    temporary = target.parent / f".{target.target.name}.{uuid4().hex}.staged"
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(target.parent)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return StagedManifest(target=target.target, temporary=temporary)


def discard_staged_manifest(staged: StagedManifest | None) -> None:
    if staged is None or not staged.temporary.exists():
        return
    staged.temporary.unlink()
    _fsync_directory(staged.temporary.parent)


async def publish_staged_manifest(staged: StagedManifest) -> None:
    """Publish only after commit; an error deliberately retains the temp."""
    # Hard-link publication is atomic and refuses to overwrite a destination
    # created after preflight.  Both names point to the already-fsynced inode.
    os.link(staged.temporary, staged.target)
    _fsync_directory(staged.target.parent)
    staged.temporary.unlink()


async def load_scope(
    conn: AsyncConnection, *, for_update: bool = False
) -> RemediationScope:
    suffix = " FOR UPDATE OF po, j, t" if for_update else ""
    rows = (await conn.execute(text(_CANDIDATE_SQL + suffix))).mappings().all()
    by_job: dict[UUID, list] = {}
    for row in rows:
        by_job.setdefault(row["job_id"], []).append(row)

    jobs: list[RemediationJob] = []
    for job_id, job_rows in by_job.items():
        first = job_rows[0]
        phases = tuple(
            RemediationPhase(
                phase_output_id=row["phase_output_id"],
                job_id=row["job_id"],
                phase_name=row["phase_name"],
                phase_status=row["phase_status"],
                solver_status=row["solver_status"],
                output_sha256=row["output_sha256"],
                phase_completed_at=row["phase_completed_at"],
            )
            for row in job_rows
        )
        jobs.append(
            RemediationJob(
                job_id=job_id,
                toc_entry_id=first["toc_entry_id"],
                job_status=first["job_status"],
                job_completed_at=first["job_completed_at"],
                notion_archived_at=first["notion_archived_at"],
                notion_skip_reason=first["notion_skip_reason"],
                claim_token=first["claim_token"],
                notion_archived_job_id=first["notion_archived_job_id"],
                provider=first["provider"],
                model=first["model"],
                transport=first["transport"],
                extract_provider=first["extract_provider"],
                extract_model=first["extract_model"],
                extract_transport=first["extract_transport"],
                judge_provider=first["judge_provider"],
                judge_model=first["judge_model"],
                judge_transport=first["judge_transport"],
                solver_provider=first["solver_provider"],
                solver_model=first["solver_model"],
                solver_transport=first["solver_transport"],
                phases=phases,
            )
        )
    return split_scope(jobs)


async def load_plan(
    conn: AsyncConnection, *, for_update: bool = False
) -> tuple[RemediationJob, ...]:
    """Load only the executable current-tuple plan (compatibility helper)."""
    return (await load_scope(conn, for_update=for_update)).eligible_current_tuple


_PHASE_UPDATE = text(
    """
    UPDATE phase_outputs
       SET status = 'failed',
           solver_status = 'mismatch_blocked',
           error_message = :error_message
     WHERE id = :phase_output_id
       AND job_id = :job_id
       AND phase_name = :phase_name
       AND status = :phase_status
       AND solver_status = :solver_status
       AND completed_at IS NOT DISTINCT FROM :phase_completed_at
       AND encode(sha256(convert_to(coalesce(output_md, ''), 'UTF8')), 'hex')
           = :output_sha256
    """
)

_JOB_UPDATE = text(
    """
    UPDATE homework_jobs AS j
       SET status = 'failed',
           error_message = :error_message,
           last_error = :error_message,
           claim_token = NULL,
           claimed_at = NULL,
           claimed_by = NULL
     WHERE j.id = :job_id
       AND j.toc_entry_id = :toc_entry_id
       AND j.status = :job_status
       AND j.completed_at IS NOT DISTINCT FROM :job_completed_at
       AND j.notion_archived_at IS NOT DISTINCT FROM :notion_archived_at
       AND j.notion_skip_reason IS NOT DISTINCT FROM :notion_skip_reason
       AND j.claim_token IS NOT DISTINCT FROM :claim_token
       AND j.provider = :provider
       AND j.model = :model
       AND j.transport = :transport
       AND j.extract_provider = :extract_provider
       AND j.extract_model = :extract_model
       AND j.extract_transport = :extract_transport
       AND j.judge_provider = :judge_provider
       AND j.judge_model = :judge_model
       AND j.judge_transport = :judge_transport
       AND j.solver_provider = :solver_provider
       AND j.solver_model = :solver_model
       AND j.solver_transport = :solver_transport
       AND EXISTS (
           SELECT 1
             FROM toc_entries AS t
            WHERE t.id = :toc_entry_id
              AND t.notion_archived_job_id IS NOT DISTINCT FROM :notion_archived_job_id
       )
    """
)


async def apply_plan(conn: AsyncConnection, plan: Sequence[RemediationJob]) -> None:
    for job in plan:
        for phase in job.phases:
            result = await conn.execute(
                _PHASE_UPDATE,
                {
                    **_phase_state(phase),
                    "phase_output_id": phase.phase_output_id,
                    "job_id": phase.job_id,
                    "phase_completed_at": phase.phase_completed_at,
                    "error_message": REMEDIATION_ERROR,
                },
            )
            if result.rowcount != 1:
                raise ApplyStateDriftError(
                    f"phase {phase.phase_output_id} changed; expected 1 row, "
                    f"updated {result.rowcount}"
                )

        result = await conn.execute(
            _JOB_UPDATE,
            {
                "job_id": job.job_id,
                "toc_entry_id": job.toc_entry_id,
                "job_status": job.job_status,
                "job_completed_at": job.job_completed_at,
                "notion_archived_at": job.notion_archived_at,
                "notion_skip_reason": job.notion_skip_reason,
                "claim_token": job.claim_token,
                "notion_archived_job_id": job.notion_archived_job_id,
                **_role_state(job),
                "error_message": REMEDIATION_ERROR,
            },
        )
        if result.rowcount != 1:
            raise ApplyStateDriftError(
                f"job {job.job_id} changed; expected 1 row, updated {result.rowcount}"
            )


def _summary(plan: Sequence[RemediationJob]) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    phases = tuple(phase for job in plan for phase in job.phases)
    return {
        "total_phases": len(phases),
        "total_jobs": len(plan),
        "recent_phases": sum(
            phase.phase_completed_at is not None
            and (
                phase.phase_completed_at.replace(tzinfo=timezone.utc)
                if phase.phase_completed_at.tzinfo is None
                else phase.phase_completed_at
            )
            >= cutoff
            for phase in phases
        ),
        "archived_jobs": sum(job.notion_archived_at is not None for job in plan),
    }


def print_scope(scope: RemediationScope) -> None:
    plan = scope.eligible_current_tuple
    for job in plan:
        print(
            f"eligible job={job.job_id} status={job.job_status} "
            f"archived={job.notion_archived_at is not None}"
        )
        for phase in job.phases:
            print(
                f"  phase={phase.phase_output_id} name={phase.phase_name} "
                f"solver={phase.solver_status}"
            )
    for job in scope.blocked_retired_tuple:
        print(
            f"blocked-retired job={job.job_id} status={job.job_status} "
            f"roles={json.dumps(_role_state(job), sort_keys=True)}"
        )
    summary = _summary(plan)
    category_counts = {
        "eligible_current_tuple": len(scope.eligible_current_tuple),
        "blocked_retired_tuple": len(scope.blocked_retired_tuple),
    }
    print(
        " ".join(
            f"{key}={value}" for key, value in {**summary, **category_counts}.items()
        )
    )
    print(f"plan-hash={plan_hash(plan)}")


def print_plan(plan: Sequence[RemediationJob]) -> None:
    """Compatibility reporter for an already-eligible plan."""
    print_scope(RemediationScope(tuple(plan), ()))


async def run(
    *,
    database_url: str,
    apply: bool = False,
    expect_plan_hash: str | None = None,
    manifest_out: str | Path | None = None,
) -> int:
    if apply and (not expect_plan_hash or manifest_out is None):
        print(
            "ERROR: --apply requires --expect-plan-hash and --manifest-out",
            file=sys.stderr,
        )
        return 2

    manifest_target: ManifestTarget | None = None
    if apply:
        try:
            manifest_target = validate_manifest_target(manifest_out)
        except PreflightError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    engine = create_async_engine(database_url, future=True)
    committed_plan: tuple[RemediationJob, ...] | None = None
    staged_manifest: StagedManifest | None = None
    try:
        if not apply:
            async with engine.connect() as conn:
                # Make the production dry-run structurally read-only at the
                # PostgreSQL transaction boundary, not merely by convention.
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                scope = await load_scope(conn)
            print_scope(scope)
            print("DRY RUN — nothing was written")
            return 0

        try:
            async with engine.begin() as conn:
                scope = await load_scope(conn, for_update=True)
                plan = scope.eligible_current_tuple
                fresh_hash = plan_hash(plan)
                if fresh_hash != expect_plan_hash:
                    raise PlanChangedError(
                        "plan changed since dry-run: "
                        f"expected {expect_plan_hash}, current {fresh_hash}"
                    )
                await apply_plan(conn, plan)
                assert manifest_target is not None
                staged_manifest = stage_manifest_durable(
                    manifest_target, manifest_for_plan(plan)
                )
                committed_plan = plan
        except (PlanChangedError, ApplyStateDriftError) as exc:
            discard_staged_manifest(staged_manifest)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
        except OSError as exc:
            discard_staged_manifest(staged_manifest)
            print(
                f"ERROR: manifest staging failed; database rolled back: {exc}",
                file=sys.stderr,
            )
            return 3
        except BaseException:
            discard_staged_manifest(staged_manifest)
            raise

        # Deliberately after engine.begin() exits: the final path becomes
        # visible only after PostgreSQL has committed successfully.
        assert staged_manifest is not None
        try:
            await publish_staged_manifest(staged_manifest)
        except OSError as exc:
            print(
                "ERROR: DATABASE COMMITTED but manifest publication failed; "
                f"the durable manifest remains at {staged_manifest.temporary}: {exc}",
                file=sys.stderr,
            )
            return 4
        print(
            f"applied: quarantined {sum(len(j.phases) for j in committed_plan or ())} "
            f"phase(s) across {len(committed_plan or ())} job(s)"
        )
        return 0
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run historical solver mismatch quarantine. Writes only with "
            "--apply plus a reviewed plan hash and manifest path."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-plan-hash")
    parser.add_argument("--manifest-out", type=Path)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parse_args(argv)
    try:
        database_url = preflight_database_url(os.environ if environ is None else environ)
        if args.apply and not args.expect_plan_hash:
            raise PreflightError("--apply requires --expect-plan-hash")
        if args.apply and args.manifest_out is None:
            raise PreflightError("--apply requires --manifest-out")
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(
        run(
            database_url=database_url,
            apply=args.apply,
            expect_plan_hash=args.expect_plan_hash,
            manifest_out=args.manifest_out,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
