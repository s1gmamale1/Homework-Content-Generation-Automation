"""Real-Postgres safety tests for historical solver-mismatch quarantine.

Requires a disposable migrated database via RUN_DB_INTEGRATION=1 and
DATABASE_URL.  No model, Notion, or other external service is used.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real scratch Postgres",
)

NOW = datetime.now(timezone.utc)


async def _truncate() -> None:
    _require_disposable_database_url(os.environ["DATABASE_URL"])
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE phase_outputs, homework_jobs, toc_entries, books "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


def _require_disposable_database_url(database_url: str) -> None:
    """Fail before a session exists unless the DB name is visibly disposable."""
    database = (make_url(database_url).database or "").lower()
    if not (
        "scratch" in database
        or database.startswith("test_")
        or re.search(r"(?:^|_)test(?:$|_)", database)
    ):
        raise RuntimeError(
            f"refusing destructive integration cleanup on non-test database {database!r}"
        )


@pytest.fixture(autouse=True)
async def _clean_db():
    await _truncate()
    yield
    await _truncate()


async def test_cleanup_guard_refuses_production_like_url_before_truncate(
    monkeypatch,
):
    called = False

    def _record_session_open():
        nonlocal called
        called = True
        raise AssertionError("SessionLocal opened before the scratch-DB guard")

    # Scope the patches inside the test body so the autouse fixture's
    # teardown sees the original scratch URL and cleanup function.
    with monkeypatch.context() as scoped:
        scoped.setenv(
            "DATABASE_URL", "postgresql+asyncpg://edu:secret@db.internal/edu_copy"
        )
        scoped.setattr(
            "app.db.SessionLocal",
            _record_session_open,
        )
        with pytest.raises(RuntimeError, match="non-test database 'edu_copy'"):
            await _truncate()
    assert called is False


async def _seed_job(
    *,
    phase_names=("memory-check",),
    archived=True,
    completed_at=NOW,
    mismatch=True,
):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        book = Book(
            subject="matematika",
            grade="6",
            original_filename=f"{completed_at.timestamp()}.pdf",
            content_sha256=("a" if mismatch else "b") * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(
            book_id=book.id,
            section_title="Historical mismatch",
            order_index=0,
            page_start=1,
        )
        session.add(toc)
        await session.flush()
        job = HomeworkJob(
            book_id=book.id,
            toc_entry_id=toc.id,
            subject=book.subject,
            status="done",
            provider="gemini",
            model="gemini-2.5-flash",
            transport="api",
            output_language="uz",
            completed_at=completed_at,
            notion_archived_at=completed_at if archived else None,
            notion_skip_reason="historical evidence" if archived else None,
        )
        session.add(job)
        await session.flush()
        if archived:
            toc.notion_archived_job_id = job.id
        phases = []
        for order, phase_name in enumerate(phase_names):
            phase = PhaseOutput(
                job_id=job.id,
                phase_name=phase_name,
                phase_order=order,
                prompt_hash=f"hash-{order}",
                model_name="gemini-2.5-pro",
                output_md=f"# retained wrong answer {order}",
                tokens_input=100 + order,
                tokens_output=200 + order,
                validation_warnings=[f"warning-{order}"],
                status="done",
                solver_status="mismatch_shipped" if mismatch else "ok",
                completed_at=completed_at,
            )
            session.add(phase)
            phases.append(phase)
        await session.commit()
        return job.id, toc.id, tuple(p.id for p in phases)


async def _snapshot() -> tuple:
    from sqlalchemy import text

    from app.db import SessionLocal

    async with SessionLocal() as session:
        phases = (
            await session.execute(
                text("SELECT to_jsonb(p) FROM phase_outputs p ORDER BY p.id")
            )
        ).scalars().all()
        jobs = (
            await session.execute(
                text("SELECT to_jsonb(j) FROM homework_jobs j ORDER BY j.id")
            )
        ).scalars().all()
        toc = (
            await session.execute(
                text("SELECT to_jsonb(t) FROM toc_entries t ORDER BY t.id")
            )
        ).scalars().all()
    return phases, jobs, toc


async def _load_plan():
    from sqlalchemy.ext.asyncio import create_async_engine

    from scripts.quarantine_solver_mismatches import load_plan

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            return await load_plan(conn)
    finally:
        await engine.dispose()


async def test_dry_run_lists_counts_and_ids_without_writing(capsys):
    from scripts.quarantine_solver_mismatches import run

    recent_job, _toc, recent_phases = await _seed_job(
        phase_names=("memory-check", "boss-arena"), archived=True
    )
    old_job, _toc2, old_phases = await _seed_job(
        archived=False, completed_at=NOW - timedelta(days=30)
    )
    await _seed_job(mismatch=False)
    before = await _snapshot()

    assert await run(database_url=os.environ["DATABASE_URL"]) == 0
    assert await _snapshot() == before
    out = capsys.readouterr().out
    assert "total_phases=3" in out
    assert "total_jobs=2" in out
    assert "recent_phases=2" in out
    assert "archived_jobs=1" in out
    for value in (recent_job, old_job, *recent_phases, *old_phases):
        assert str(value) in out
    assert "plan-hash=" in out


async def test_guarded_apply_is_atomic_retains_evidence_and_is_idempotent(tmp_path):
    from sqlalchemy import text

    from app.db import SessionLocal
    from scripts.quarantine_solver_mismatches import plan_hash, run

    job_id, toc_id, phase_ids = await _seed_job(
        phase_names=("memory-check", "boss-arena"), archived=True
    )
    clean_job_id, _clean_toc, clean_phase_ids = await _seed_job(mismatch=False)
    plan = await _load_plan()
    reviewed_hash = plan_hash(plan)
    manifest = tmp_path / "quarantine.json"

    assert await run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=manifest,
    ) == 0
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert payload["plan_hash"] == reviewed_hash
    assert payload["jobs"][0]["job_id"] == str(job_id)

    async with SessionLocal() as session:
        phases = (
            await session.execute(
                text(
                    "SELECT id,status,solver_status,error_message,output_md,"
                    "tokens_input,tokens_output,validation_warnings,completed_at "
                    "FROM phase_outputs WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": list(phase_ids)},
            )
        ).all()
        job = (
            await session.execute(
                text(
                    "SELECT status,error_message,last_error,notion_archived_at,"
                    "notion_skip_reason,claim_token,claimed_at,claimed_by "
                    "FROM homework_jobs WHERE id=:id"
                ),
                {"id": job_id},
            )
        ).one()
        pointer = await session.scalar(
            text("SELECT notion_archived_job_id FROM toc_entries WHERE id=:id"),
            {"id": toc_id},
        )
        clean = await session.scalar(
            text("SELECT status FROM homework_jobs WHERE id=:id"),
            {"id": clean_job_id},
        )
        clean_phase = await session.scalar(
            text("SELECT status FROM phase_outputs WHERE id=:id"),
            {"id": clean_phase_ids[0]},
        )

    assert {p.status for p in phases} == {"failed"}
    assert {p.solver_status for p in phases} == {"mismatch_blocked"}
    assert all(p.error_message for p in phases)
    assert {p.output_md for p in phases} == {
        "# retained wrong answer 0",
        "# retained wrong answer 1",
    }
    assert {p.tokens_input for p in phases} == {100, 101}
    assert {p.tokens_output for p in phases} == {200, 201}
    assert all(p.validation_warnings for p in phases)
    assert all(p.completed_at is not None for p in phases)
    assert job.status == "failed"
    assert job.error_message and job.last_error
    assert job.notion_archived_at is not None
    assert job.notion_skip_reason == "historical evidence"
    assert job.claim_token is None and job.claimed_at is None and job.claimed_by is None
    assert pointer == job_id
    assert clean == "done" and clean_phase == "done"

    assert await _load_plan() == ()
    assert await run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=tmp_path / "second.json",
    ) == 3
    assert not (tmp_path / "second.json").exists()


async def test_state_drift_aborts_without_partial_writes(tmp_path):
    from sqlalchemy import text

    from app.db import SessionLocal
    from scripts.quarantine_solver_mismatches import plan_hash, run

    first_job, _toc, first_phase = await _seed_job()
    second_job, _toc2, second_phase = await _seed_job(
        completed_at=NOW - timedelta(days=1)
    )
    reviewed_hash = plan_hash(await _load_plan())

    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE homework_jobs SET notion_archived_at=NULL WHERE id=:id"),
            {"id": second_job},
        )
        await session.commit()
    before = await _snapshot()

    assert await run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=tmp_path / "drift.json",
    ) == 3
    assert await _snapshot() == before
    assert not (tmp_path / "drift.json").exists()

    async with SessionLocal() as session:
        states = (
            await session.execute(
                text(
                    "SELECT id,status FROM homework_jobs WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": [first_job, second_job]},
            )
        ).all()
        phase_states = (
            await session.execute(
                text(
                    "SELECT id,status FROM phase_outputs WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": [first_phase[0], second_phase[0]]},
            )
        ).all()
    assert {row.status for row in states} == {"done"}
    assert {row.status for row in phase_states} == {"done"}


async def test_expected_state_predicates_roll_back_earlier_job_updates():
    """A stale direct plan cannot leave its earlier jobs half-quarantined."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import SessionLocal
    from scripts.quarantine_solver_mismatches import (
        ApplyStateDriftError,
        apply_plan,
    )

    await _seed_job()
    stale_job_id, _toc, _phases = await _seed_job(
        completed_at=NOW - timedelta(days=1)
    )
    stale_plan = await _load_plan()
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE homework_jobs SET notion_skip_reason='drifted' WHERE id=:id"),
            {"id": stale_job_id},
        )
        await session.commit()
    before = await _snapshot()

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        with pytest.raises(ApplyStateDriftError):
            async with engine.begin() as conn:
                await apply_plan(conn, stale_plan)
    finally:
        await engine.dispose()

    assert await _snapshot() == before


async def test_invalid_manifest_destination_causes_zero_database_writes(tmp_path):
    from scripts.quarantine_solver_mismatches import plan_hash, run

    await _seed_job()
    reviewed_hash = plan_hash(await _load_plan())
    before = await _snapshot()
    invalid = tmp_path / "missing-parent" / "manifest.json"

    assert await run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=invalid,
    ) == 2
    assert await _snapshot() == before
    assert not invalid.exists()


async def test_manifest_staging_failure_rolls_back_database(monkeypatch, tmp_path):
    from scripts import quarantine_solver_mismatches as script

    await _seed_job()
    reviewed_hash = script.plan_hash(await _load_plan())
    before = await _snapshot()

    def _fail_stage(*args, **kwargs):
        raise OSError("disk full while staging")

    monkeypatch.setattr(script, "stage_manifest_durable", _fail_stage)
    assert await script.run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=tmp_path / "manifest.json",
    ) == 3
    assert await _snapshot() == before
    assert not (tmp_path / "manifest.json").exists()


async def test_post_commit_publish_failure_keeps_and_reports_durable_temp(
    monkeypatch, tmp_path, capsys
):
    from sqlalchemy import text

    from app.db import SessionLocal
    from scripts import quarantine_solver_mismatches as script

    job_id, _toc, _phases = await _seed_job()
    reviewed_hash = script.plan_hash(await _load_plan())
    final_path = tmp_path / "manifest.json"
    seen_temp = None

    async def _fail_publish(staged):
        nonlocal seen_temp
        seen_temp = staged.temporary
        assert staged.temporary.exists()
        assert not staged.target.exists()
        # A separate session sees `failed`, proving publication starts only
        # after the write transaction committed.
        async with SessionLocal() as session:
            status = await session.scalar(
                text("SELECT status FROM homework_jobs WHERE id=:id"),
                {"id": job_id},
            )
        assert status == "failed"
        raise OSError("simulated post-commit publish failure")

    monkeypatch.setattr(script, "publish_staged_manifest", _fail_publish)
    assert await script.run(
        database_url=os.environ["DATABASE_URL"],
        apply=True,
        expect_plan_hash=reviewed_hash,
        manifest_out=final_path,
    ) == 4

    assert seen_temp is not None and seen_temp.exists()
    assert not final_path.exists()
    staged_payload = json.loads(seen_temp.read_text(encoding="utf-8"))
    assert staged_payload["plan_hash"] == reviewed_hash
    error = capsys.readouterr().err
    assert "DATABASE COMMITTED" in error
    assert str(seen_temp) in error
