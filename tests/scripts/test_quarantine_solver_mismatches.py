"""Pure safety-contract tests for the solver-mismatch quarantine script."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID


T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
PHASE_ID = UUID("10000000-0000-0000-0000-000000000001")
JOB_ID = UUID("20000000-0000-0000-0000-000000000001")
TOC_ID = UUID("30000000-0000-0000-0000-000000000001")
ARCHIVED_JOB_ID = UUID("40000000-0000-0000-0000-000000000001")
TOKEN = UUID("50000000-0000-0000-0000-000000000001")


def _phase():
    from scripts.quarantine_solver_mismatches import RemediationPhase

    return RemediationPhase(
        phase_output_id=PHASE_ID,
        job_id=JOB_ID,
        phase_name="memory-check",
        phase_status="done",
        solver_status="mismatch_shipped",
        output_sha256="a" * 64,
        phase_completed_at=T0,
    )


def _job():
    from scripts.quarantine_solver_mismatches import RemediationJob

    return RemediationJob(
        job_id=JOB_ID,
        toc_entry_id=TOC_ID,
        job_status="done",
        job_completed_at=T0,
        notion_archived_at=T0,
        notion_skip_reason=None,
        claim_token=TOKEN,
        notion_archived_job_id=ARCHIVED_JOB_ID,
        phases=(_phase(),),
    )


def _hash_for(job) -> str:
    from scripts.quarantine_solver_mismatches import plan_hash

    return plan_hash((job,))


def test_missing_raw_database_url_refuses_before_app_config_import():
    """Removing the raw-target guard would allow .env to select production."""
    import subprocess
    import sys

    probe = r"""
import importlib.abc
import sys

class BlockAppConfig(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.config" or fullname.startswith("app.config."):
            raise RuntimeError("app.config imported before raw DATABASE_URL preflight")
        return None

sys.meta_path.insert(0, BlockAppConfig())
from scripts import quarantine_solver_mismatches as script
raise SystemExit(script.main([], environ={}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 2
    assert "DATABASE_URL must be set explicitly" in completed.stderr
    assert "app.config imported" not in completed.stderr


def test_apply_requires_both_reviewed_hash_and_manifest_path(capsys):
    """A bare --apply must never get far enough to connect to a database."""
    from scripts import quarantine_solver_mismatches as script

    env = {"DATABASE_URL": "postgresql+asyncpg://unused/unused"}
    assert script.main(["--apply"], environ=env) == 2
    assert "--expect-plan-hash" in capsys.readouterr().err
    assert script.main(
        ["--apply", "--expect-plan-hash", "a" * 64], environ=env
    ) == 2
    assert "--manifest-out" in capsys.readouterr().err


def test_plan_hash_is_deterministic_and_covers_every_expected_state_field():
    """Dropping any guarded field from the snapshot must change this test."""
    phase = _phase()
    job = _job()
    baseline = _hash_for(job)

    variants = (
        replace(job, job_status="failed"),
        replace(job, notion_archived_at=None),
        replace(job, notion_archived_job_id=None),
        replace(job, phases=(replace(phase, phase_status="failed"),)),
        replace(job, phases=(replace(phase, solver_status="mismatch_blocked"),)),
        replace(job, phases=(replace(phase, output_sha256="b" * 64),)),
    )
    assert len({baseline, *(_hash_for(v) for v in variants)}) == 1 + len(variants)
    assert baseline == _hash_for(job)


def test_plan_hash_is_independent_of_job_and_phase_query_order():
    """Database row order must not create a different reviewed gesture."""
    from scripts.quarantine_solver_mismatches import RemediationJob, plan_hash

    first = _job()
    second_phase = replace(
        _phase(),
        phase_output_id=UUID("10000000-0000-0000-0000-000000000002"),
        phase_name="boss-arena",
    )
    second = RemediationJob(
        job_id=UUID("20000000-0000-0000-0000-000000000002"),
        toc_entry_id=UUID("30000000-0000-0000-0000-000000000002"),
        job_status="done",
        job_completed_at=T0,
        notion_archived_at=None,
        notion_skip_reason="not attempted",
        claim_token=None,
        notion_archived_job_id=None,
        phases=(second_phase,),
    )
    multi = replace(first, phases=(first.phases[0], second_phase))
    reversed_multi = replace(first, phases=tuple(reversed(multi.phases)))

    assert plan_hash((first, second)) == plan_hash((second, first))
    assert plan_hash((multi,)) == plan_hash((reversed_multi,))
