"""Real-Postgres proof for the persistent answer-key fail-closed chain.

The exercised path is intentionally the production one::

    claim_next_job -> Worker._execute_job -> pipeline.run -> wave scheduler
    -> phase generation/judge/solver policy -> job + phase repositories

Only boundaries that would leave the test process are replaced: the provider
response, PDF fetch, advisory event transport, and Notion network call.  The
fixture owns every row it changes, gives its job a priority higher than organic
work, and seeds an older decoy so a wrong claim fails loudly.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update


_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
_SKIP_REASON = "set RUN_DB_INTEGRATION=1 with a scratch DATABASE_URL to run"
_PRIORITY = 1_000_000
_INITIAL_WRONG = "# Memory check\n\nInitial wrong answer key: Q3 = B"
_STILL_WRONG_REGEN = "# Memory check\n\nRegenerated but still wrong answer key: Q3 = B"
_EXTRACT_MD = (
    "# Lesson extract\n\nThis lesson explains a source-grounded historical concept "
    "with enough detail for the generated practice phases."
)


@dataclass(frozen=True)
class _Seeded:
    book_id: uuid.UUID
    toc_id: uuid.UUID
    job_id: uuid.UUID
    decoy_book_id: uuid.UUID
    decoy_job_id: uuid.UUID


async def _seed(session) -> _Seeded:
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    book = await books_repo.create(
        session,
        subject="history",
        original_filename=f"solver-e2e-{uuid.uuid4()}.pdf",
        content_sha256=uuid.uuid4().hex.ljust(64, "0"),
        file_size_bytes=31,
        status="toc_ready",
        grade="9",
    )
    toc = TOCEntry(
        book_id=book.id,
        section_title="Solver fail-closed lesson",
        section_number="1",
        page_start=1,
        page_end=1,
        order_index=0,
    )
    session.add(toc)
    await session.flush()
    job = await jobs_repo.create(
        session,
        book_id=book.id,
        toc_entry_id=toc.id,
        subject="history",
        output_language="uz",
        provider="gemini",
        model="gemini-2.5-flash",
        transport="api",
        extract_transport="api",
        judge_transport="api",
        solver_transport="api",
        extract_provider="gemini",
        extract_model="gemini-2.5-flash",
        judge_provider="gemini",
        judge_model="gemini-2.5-pro",
        solver_provider="gemini",
        solver_model="gemini-2.5-pro",
        # Both phases are dependency-ready in this selected flow, so the wave
        # scheduler creates a genuine in-flight sibling before memory-check
        # reaches its terminal/transient outcome.
        selected_phases=["memory-check", "practice-rlc"],
    )
    job.priority = _PRIORITY
    session.add(
        PhaseOutput(
            job_id=job.id,
            phase_name="extract",
            phase_order=0,
            prompt_hash="builtin:extract:v4",
            model_name="gemini-2.5-flash",
            provider="gemini",
            output_md=_EXTRACT_MD,
            tokens_input=1,
            tokens_output=1,
            status="done",
            authoring_mode="markdown_builtin",
            completed_at=datetime.now(timezone.utc),
        )
    )

    # Row-owned claim isolation.  This older pending decoy would win the FIFO
    # tiebreak if the seeded job ever lost its explicit priority.
    decoy_book = await books_repo.create(
        session,
        subject="history",
        original_filename=f"solver-e2e-decoy-{uuid.uuid4()}.pdf",
        content_sha256=uuid.uuid4().hex.ljust(64, "0"),
        file_size_bytes=31,
        status="toc_ready",
        grade="9",
    )
    decoy_toc = TOCEntry(
        book_id=decoy_book.id,
        section_title="Older decoy lesson",
        section_number="1",
        page_start=1,
        page_end=1,
        order_index=0,
    )
    session.add(decoy_toc)
    await session.flush()
    decoy = await jobs_repo.create(
        session,
        book_id=decoy_book.id,
        toc_entry_id=decoy_toc.id,
        subject="history",
        output_language="uz",
        provider="gemini",
        model="gemini-2.5-flash",
        transport="api",
        extract_transport="api",
        judge_transport="api",
        solver_transport="api",
        extract_provider="gemini",
        extract_model="gemini-2.5-flash",
        judge_provider="gemini",
        judge_model="gemini-2.5-pro",
        solver_provider="gemini",
        solver_model="gemini-2.5-pro",
        selected_phases=["memory-check"],
    )
    decoy.scheduled_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await session.flush()
    return _Seeded(book.id, toc.id, job.id, decoy_book.id, decoy.id)


async def _cleanup(*book_ids: uuid.UUID) -> None:
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    ids = list(book_ids)
    if not ids:
        return
    async with SessionLocal() as session:
        job_ids = list(
            (
                await session.execute(
                    select(HomeworkJob.id).where(HomeworkJob.book_id.in_(ids))
                )
            ).scalars()
        )
        if job_ids:
            await session.execute(delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
            await session.execute(delete(HomeworkJob).where(HomeworkJob.id.in_(job_ids)))
        await session.execute(delete(TOCEntry).where(TOCEntry.book_id.in_(ids)))
        await session.execute(delete(Book).where(Book.id.in_(ids)))
        await session.commit()


async def _claim(seed: _Seeded, worker):
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as session:
        claimed = await jobs_repo.claim_next_job(
            session,
            worker_id="solver-fail-closed-e2e",
            max_attempts=worker.max_attempts,
            capabilities={
                "can_gemini_api": True,
                "can_claude_api": False,
                "can_clodex_api": False,
            },
        )
        await session.commit()
    assert claimed is not None
    assert claimed.job.id == seed.job_id, (
        f"claimed unrelated job {claimed.job.id} instead of owned job {seed.job_id}"
    )
    worker._leases[seed.job_id] = claimed.lease
    return claimed


class _ProviderScript:
    """Deterministic responses at the one model/network boundary."""

    def __init__(self, *, transient_repair: bool = False):
        self.transient_repair = transient_repair
        self.sibling_started = asyncio.Event()
        self.sibling_cancelled = asyncio.Event()

    async def __call__(self, **kwargs):
        from app.schemas.solver import Discrepancy, SolveVerdict
        from app.services.agent import PhaseResult
        from app.services.phase_judge import Verdict

        phase_name = kwargs["phase_name"]
        prompt = kwargs.get("phase_prompt") or ""
        usage = {"prompt_tokens": 11, "output_tokens": 7, "raw": {}}
        if phase_name == "__judge__":
            return PhaseResult(
                text='{"passed":true,"failures":[]}',
                parsed=Verdict(passed=True, failures=[]),
                usage=usage,
            )
        if phase_name == "__solver__":
            mismatch = SolveVerdict(
                agrees=False,
                discrepancies=[
                    Discrepancy(
                        item="Q3",
                        generated_key="B",
                        solver_answer="C",
                        explanation="the generated key contradicts the solved result",
                        confidence="high",
                    )
                ],
            )
            return PhaseResult(
                text=mismatch.model_dump_json(), parsed=mismatch, usage=usage
            )
        if phase_name == "practice-rlc":
            self.sibling_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.sibling_cancelled.set()
                raise
        assert phase_name == "memory-check"
        is_repair = "Fix these answer-key errors" in prompt
        if is_repair and self.transient_repair:
            raise ConnectionError("connection reset during solver repair")
        return PhaseResult(
            text=_STILL_WRONG_REGEN if is_repair else _INITIAL_WRONG,
            parsed=None,
            usage=usage,
        )


def _patch_boundaries(monkeypatch, tmp_path: Path, script: _ProviderScript):
    from app.config import settings
    from app.services import pipeline

    pdf = tmp_path / "owned.pdf"
    pdf.write_bytes(b"%PDF-1.4 solver fail-closed e2e")
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf_sync", lambda *_a, **_k: pdf
    )
    monkeypatch.setattr(pipeline.agent, "run_phase", script)
    publish = AsyncMock()
    close = AsyncMock()
    archive = AsyncMock()
    monkeypatch.setattr(pipeline.events_bus, "publish", publish)
    monkeypatch.setattr(pipeline.events_bus, "close", close)
    monkeypatch.setattr(pipeline.notion_archive, "archive_job", archive)
    monkeypatch.setattr(settings, "structured_output_enabled", False)
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", False)
    monkeypatch.setattr(settings, "solver_enabled", True)
    monkeypatch.setattr(settings, "max_solve_regens", 1)
    return publish, archive


async def _rows(job_id: uuid.UUID):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput

    async with SessionLocal() as session:
        job = (
            await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
        ).scalar_one()
        phases = {
            row.phase_name: row
            for row in (
                await session.execute(
                    select(PhaseOutput).where(PhaseOutput.job_id == job_id)
                )
            ).scalars()
        }
        # Detach the complete rows before the session closes so all assertions
        # below are plain reads and cannot trigger lazy database work.
        session.expunge(job)
        for row in phases.values():
            session.expunge(row)
        return job, phases


def _event_names(publish: AsyncMock, *, resource_id: str) -> list[str]:
    return [
        call.args[1]
        for call in publish.await_args_list
        if call.args and call.args[0] == resource_id
    ]


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_persistent_mismatch_blocks_job_sibling_completion_and_archive(
    monkeypatch, tmp_path
):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.services.worker import Worker

    seed = None
    try:
        async with SessionLocal() as session:
            seed = await _seed(session)
            await session.commit()
        script = _ProviderScript()
        publish, archive = _patch_boundaries(monkeypatch, tmp_path, script)
        worker = Worker(concurrency=1, max_attempts=3)
        claimed = await _claim(seed, worker)

        await worker._execute_job(seed.job_id)

        job, phases = await _rows(seed.job_id)
        assert job.status == "failed"
        assert "persistent answer-key mismatch" in (job.error_message or "")
        assert phases["extract"].status == "done"
        assert phases["memory-check"].status == "failed"
        assert phases["memory-check"].solver_status == "mismatch_blocked"
        assert phases["memory-check"].output_md == _STILL_WRONG_REGEN
        assert phases["practice-rlc"].status == "failed"
        assert script.sibling_started.is_set()
        assert script.sibling_cancelled.is_set()
        names = _event_names(publish, resource_id=f"job:{seed.job_id}")
        assert "job_completed" not in names
        assert not any(
            call.args[1] == "phase_completed"
            and call.args[2].get("phase_name") == "memory-check"
            for call in publish.await_args_list
        )
        archive.assert_not_awaited()
        assert claimed.job.attempts == 1

        async with SessionLocal() as session:
            assert (
                await session.execute(
                    select(HomeworkJob.status).where(HomeworkJob.id == seed.decoy_job_id)
                )
            ).scalar_one() == "pending"
    finally:
        if seed is not None:
            await _cleanup(seed.book_id, seed.decoy_book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_transient_repair_uses_bounded_queue_retry_and_reconciles_phases(
    monkeypatch, tmp_path
):
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.services.worker import Worker

    seed = None
    try:
        async with SessionLocal() as session:
            seed = await _seed(session)
            await session.commit()
        script = _ProviderScript(transient_repair=True)
        _publish, archive = _patch_boundaries(monkeypatch, tmp_path, script)

        first_worker = Worker(concurrency=1, max_attempts=3)
        await _claim(seed, first_worker)
        await first_worker._execute_job(seed.job_id)

        job, phases = await _rows(seed.job_id)
        assert job.status == "pending"
        assert job.attempts == 1
        assert "connection reset during solver repair" in (job.last_error or "")
        assert phases["extract"].status == "done"
        for phase_name in ("memory-check", "practice-rlc"):
            assert phases[phase_name].status == "pending"
            assert phases[phase_name].error_message is None
            assert phases[phase_name].claim_token is None
        archive.assert_not_awaited()

        async with SessionLocal() as session:
            await session.execute(
                update(HomeworkJob)
                .where(HomeworkJob.id == seed.job_id)
                .values(attempts=2, scheduled_at=datetime.now(timezone.utc))
            )
            await session.commit()

        final_worker = Worker(concurrency=1, max_attempts=3)
        final_claim = await _claim(seed, final_worker)
        assert final_claim.job.attempts == 3
        await final_worker._execute_job(seed.job_id)

        job, phases = await _rows(seed.job_id)
        assert job.status == "failed"
        assert job.attempts == 3
        assert "connection reset during solver repair" in (job.error_message or "")
        assert phases["extract"].status == "done"
        for phase_name in ("memory-check", "practice-rlc"):
            assert phases[phase_name].status == "failed"
            assert "connection reset during solver repair" in (
                phases[phase_name].error_message or ""
            )
        archive.assert_not_awaited()
    finally:
        if seed is not None:
            await _cleanup(seed.book_id, seed.decoy_book_id)


@pytest.mark.skipif(not _INTEGRATION, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_cancel_wins_when_requested_before_blocked_phase_write(
    monkeypatch, tmp_path
):
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.services import pipeline
    from app.services.worker import Worker

    seed = None
    try:
        async with SessionLocal() as session:
            seed = await _seed(session)
            await session.commit()
        script = _ProviderScript()
        publish, archive = _patch_boundaries(monkeypatch, tmp_path, script)
        entered = asyncio.Event()
        release = asyncio.Event()
        real_persist = pipeline._persist_solver_blocked_phase

        async def paused_persist(**kwargs):
            entered.set()
            await release.wait()
            return await real_persist(**kwargs)

        monkeypatch.setattr(pipeline, "_persist_solver_blocked_phase", paused_persist)
        worker = Worker(concurrency=1, max_attempts=3)
        await _claim(seed, worker)
        task = asyncio.create_task(worker._execute_job(seed.job_id))
        await asyncio.wait_for(entered.wait(), timeout=5)

        async with SessionLocal() as session:
            assert await jobs_repo.request_cancel(session, seed.job_id) is True
            await session.commit()
        release.set()
        await asyncio.wait_for(task, timeout=5)

        job, phases = await _rows(seed.job_id)
        assert job.status == "cancelled"
        assert phases["extract"].status == "done"
        assert phases["memory-check"].status == "failed"
        assert phases["practice-rlc"].status == "failed"
        names = _event_names(publish, resource_id=f"job:{seed.job_id}")
        assert "job_completed" not in names
        archive.assert_not_awaited()
    finally:
        if seed is not None:
            await _cleanup(seed.book_id, seed.decoy_book_id)
