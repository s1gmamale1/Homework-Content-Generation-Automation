"""Bounded acceptance for the persistent solver-mismatch fail-closed path.

This is deliberately *not* a normal generation command.  It writes only to an
explicitly named scratch/test PostgreSQL database, reads one source extract in
a read-only transaction through ``SOURCE_DB_URL``, stubs content generation and
the judge, and permits exactly two real ``gemini-3.1-pro-preview`` transport
calls.  The paid path is impossible to enter unless the operator supplies the
separately-approved, exact gesture ``--max-cost-usd 0.20``.

No production database receives jobs, phases, events, or usage rows.  The
source connection executes ``SET TRANSACTION READ ONLY`` before its only
SELECT.  Notion is replaced with an in-process recorder.

Run only after the separate spend approval::

    DATABASE_URL=postgresql+asyncpg://.../edu_scratch_solver_smoke \
    SOURCE_DB_URL=postgresql://readonly:.../.../edu_copy \
      uv run python scripts/smoke_solver_fail_closed.py --max-cost-usd 0.20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from unittest.mock import patch


APPROVED_CAP = Decimal("0.20")
MAX_PAID_CALLS = 2
MAX_PROVIDER_PROMPT_BYTES = 25_000
MAX_PROVIDER_OUTPUT_TOKENS = 2_048
SOLVER_MODEL = "gemini-3.1-pro-preview"
WORKER_ID = "solver-fail-closed-smoke"
SOURCE_JOB_PREFIX = "8f734563"

INITIAL_WRONG_OUTPUT = """# Memory check — acceptance plant

## Card 1 — multiple choice

**Compute:** `1/2 + 1/3`

A. `5/6`
B. `2/5`
C. `1/5`

**Answer key:** B — `1/2 + 1/3 = 2/5`.
"""

FINAL_WRONG_OUTPUT = """# Memory check — acceptance plant (repair attempt)

## Card 1 — multiple choice

**Compute:** `1/2 + 1/3`

A. `5/6`
B. `2/5`
C. `1/5`

**Answer key:** B — `1/2 + 1/3 = 2/5`.
"""

CORRECT_CONTROL_OUTPUT = """# Memory check — acceptance control

## Card 1 — multiple choice

**Compute:** `1/2 + 1/3`

A. `5/6`
B. `2/5`
C. `1/5`

**Answer key:** A — `1/2 + 1/3 = 5/6`.
"""


class PreflightError(RuntimeError):
    """The paid smoke was not armed or its DB boundaries are unsafe."""


class BudgetExceeded(RuntimeError):
    """A provider call would exceed the approved call/prompt envelope."""


class AcceptanceFailure(AssertionError):
    """The real-chain result did not satisfy the acceptance contract."""


@dataclass(frozen=True)
class Preflight:
    database_url: str
    source_db_url: str
    max_cost_usd: Decimal
    max_paid_calls: int = MAX_PAID_CALLS


@dataclass(frozen=True)
class UsageRecord:
    operation: str
    model_name: str | None
    prompt_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class AcceptanceSnapshot:
    blocked_phase_status: str
    blocked_solver_status: str | None
    blocked_output_md: str | None
    blocked_error_message: str | None
    blocked_job_status: str
    blocked_job_attempts: int
    blocked_job_claimed_by: str | None
    blocked_job_claim_token: str | None
    expected_claim_token: str
    blocked_job_completed: bool
    blocked_phase_completed_events: int
    blocked_job_completed_events: int
    blocked_archive_calls: int
    usage_rows: tuple[UsageRecord, ...]
    mismatch_detections: int
    control_phase_status: str
    control_solver_status: str | None
    control_job_status: str
    control_generation_calls: int
    control_archive_calls: int


@dataclass(frozen=True)
class AcceptanceReport:
    paid_calls: int
    total_cost_usd: Decimal


@dataclass(frozen=True)
class SourceFixture:
    subject: str
    grade: str
    extract_md: str


@dataclass(frozen=True)
class SeededJob:
    job_id: uuid.UUID
    book_id: uuid.UUID


class PaidSolverGate:
    """The only route to the real transport during this acceptance run."""

    def __init__(self, approved_cap: Decimal):
        if approved_cap != APPROVED_CAP:
            raise PreflightError("paid smoke requires --max-cost-usd 0.20 exactly")
        self.calls = 0

    async def call(self, provider_call: Callable[..., Awaitable[Any]], **kwargs):
        if self.calls >= MAX_PAID_CALLS:
            raise BudgetExceeded("approval permits exactly two paid solver calls")
        prompt = str(kwargs.get("prompt") or "")
        if len(prompt.encode("utf-8")) > MAX_PROVIDER_PROMPT_BYTES:
            raise BudgetExceeded(
                "solver prompt exceeds the 25,000-byte bounded smoke envelope"
            )
        self.calls += 1
        return await provider_call(**kwargs)


def _database_name(url: str) -> str:
    return urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1)).path.lstrip("/")


def _is_scratch_name(name: str) -> bool:
    lowered = name.lower()
    return bool(
        "scratch" in lowered
        or lowered.startswith("test_")
        or re.search(r"(?:^|_)test(?:$|_)", lowered)
    )


def _parse_preflight(argv: Sequence[str] | None, environ: Mapping[str, str]) -> Preflight:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-usd")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        cap = Decimal(args.max_cost_usd) if args.max_cost_usd is not None else None
    except Exception as exc:  # decimal.InvalidOperation has a broad hierarchy
        raise PreflightError("paid smoke requires --max-cost-usd 0.20 exactly") from exc
    if args.max_cost_usd != "0.20" or cap != APPROVED_CAP:
        raise PreflightError("paid smoke requires --max-cost-usd 0.20 exactly")

    database_url = environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise PreflightError("DATABASE_URL must be explicitly set to a scratch/test database")
    database_name = _database_name(database_url)
    if not _is_scratch_name(database_name):
        raise PreflightError(
            f"DATABASE_URL must name a scratch/test database, got {database_name!r}"
        )

    source_db_url = environ.get("SOURCE_DB_URL", "").strip()
    if not source_db_url:
        raise PreflightError("SOURCE_DB_URL must be explicit; no production default is allowed")
    source_name = _database_name(source_db_url)
    if not source_name or source_name == database_name:
        raise PreflightError("SOURCE_DB_URL must be a separate read-only source database")
    return Preflight(database_url, source_db_url, cap)


def assert_acceptance(
    snapshot: AcceptanceSnapshot, max_cost_usd: Decimal
) -> AcceptanceReport:
    """Pure verifier for the five acceptance criteria; raises on the first miss."""
    if not (
        snapshot.blocked_phase_status == "failed"
        and snapshot.blocked_solver_status == "mismatch_blocked"
        and snapshot.blocked_output_md == FINAL_WRONG_OUTPUT
        and (snapshot.blocked_error_message or "").strip()
    ):
        raise AcceptanceFailure(
            "blocked phase must be failed/mismatch_blocked with final wrong output and error"
        )

    if not (
        snapshot.blocked_job_status == "failed"
        and snapshot.blocked_job_attempts == 1
        and snapshot.blocked_job_claimed_by == WORKER_ID
        and snapshot.blocked_job_claim_token == snapshot.expected_claim_token
        and snapshot.blocked_job_completed
    ):
        raise AcceptanceFailure("blocked job attempts/lease fields are inconsistent")

    if any(
        (
            snapshot.blocked_phase_completed_events,
            snapshot.blocked_job_completed_events,
            snapshot.blocked_archive_calls,
        )
    ):
        raise AcceptanceFailure("blocked completion/archive side effects must all be zero")

    rows = snapshot.usage_rows
    total_cost = sum((row.cost_usd for row in rows), Decimal("0"))
    usage_ok = (
        len(rows) == MAX_PAID_CALLS
        and all(row.operation == "solve:memory-check" for row in rows)
        and all(row.model_name == SOLVER_MODEL for row in rows)
        and all(row.prompt_tokens > 0 and row.output_tokens > 0 for row in rows)
        and Decimal("0") < total_cost <= max_cost_usd
    )
    if not usage_ok:
        raise AcceptanceFailure(
            "usage rows must be exactly two token-bearing, correctly priced solver calls"
        )
    if snapshot.mismatch_detections != MAX_PAID_CALLS:
        raise AcceptanceFailure("both solver calls must detect the planted high mismatch")

    if not (
        snapshot.control_phase_status == "done"
        and snapshot.control_solver_status == "ok"
        and snapshot.control_job_status == "done"
        and snapshot.control_generation_calls == 1
        and snapshot.control_archive_calls == 1
    ):
        raise AcceptanceFailure(
            "control fixture must finish done/ok without regeneration and archive once"
        )
    return AcceptanceReport(len(rows), total_cost)


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _load_source_fixture(source_db_url: str) -> SourceFixture:
    """Read one known rational-expression extract under a DB-enforced RO txn."""
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(source_db_url))
    transaction = conn.transaction(readonly=True)
    try:
        await transaction.start()
        row = await conn.fetchrow(
            """
            SELECT b.subject, b.grade, po.output_md
              FROM homework_jobs j
              JOIN books b ON b.id = j.book_id
              JOIN phase_outputs po ON po.job_id = j.id
             WHERE left(j.id::text, 8) = $1
               AND po.phase_name = 'extract'
               AND po.status = 'done'
             LIMIT 1
            """,
            SOURCE_JOB_PREFIX,
        )
        await transaction.commit()
    except BaseException:
        await transaction.rollback()
        raise
    finally:
        await conn.close()
    if row is None or not (row["output_md"] or "").strip():
        raise AcceptanceFailure(
            f"read-only source fixture {SOURCE_JOB_PREFIX}/extract is unavailable"
        )
    return SourceFixture(
        subject=row["subject"], grade=str(row["grade"] or "8"), extract_md=row["output_md"]
    )


async def _seed_job(source: SourceFixture, *, correct_control: bool) -> SeededJob:
    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry
    from app.repositories import books as books_repo
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as session:
        book = await books_repo.create(
            session,
            subject=source.subject,
            original_filename=f"solver-smoke-{uuid.uuid4()}.pdf",
            content_sha256=uuid.uuid4().hex.ljust(64, "0"),
            file_size_bytes=31,
            status="toc_ready",
            grade=source.grade,
        )
        toc = TOCEntry(
            book_id=book.id,
            section_title="Bounded solver acceptance",
            section_number="smoke",
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
            subject=source.subject,
            output_language="uz",
            provider="gemini",
            model="gemini-3.6-flash",
            transport="api",
            extract_transport="api",
            judge_transport="api",
            solver_transport="api",
            extract_provider="gemini",
            extract_model="gemini-3.5-flash-lite",
            judge_provider="gemini",
            judge_model="gemini-3.5-flash",
            solver_provider="gemini",
            solver_model=SOLVER_MODEL,
            selected_phases=["memory-check"],
        )
        job.priority = 2_000_000_000 if not correct_control else 1_999_999_999
        session.add(
            PhaseOutput(
                job_id=job.id,
                phase_name="extract",
                phase_order=0,
                prompt_hash="builtin:extract:v4",
                model_name="gemini-3.5-flash-lite",
                provider="gemini",
                output_md=source.extract_md,
                tokens_input=1,
                tokens_output=1,
                status="done",
                authoring_mode="markdown_builtin",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        return SeededJob(job.id, book.id)


async def _claim_and_run(job_id: uuid.UUID):
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.services.worker import Worker

    worker = Worker(concurrency=1, max_attempts=3)
    async with SessionLocal() as session:
        claimed = await jobs_repo.claim_next_job(
            session,
            worker_id=WORKER_ID,
            max_attempts=worker.max_attempts,
            capabilities={
                "can_gemini_api": True,
                "can_claude_api": False,
                "can_clodex_api": False,
            },
        )
        await session.commit()
    if claimed is None or claimed.job.id != job_id:
        actual = None if claimed is None else claimed.job.id
        raise AcceptanceFailure(f"scratch claim isolation failed: expected {job_id}, got {actual}")
    worker._leases[job_id] = claimed.lease
    await worker._execute_job(job_id)
    return claimed.lease


async def _collect_rows(job_id: uuid.UUID):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.services import pricing

    async with SessionLocal() as session:
        job = (
            await session.execute(select(HomeworkJob).where(HomeworkJob.id == job_id))
        ).scalar_one()
        phase = (
            await session.execute(
                select(PhaseOutput).where(
                    PhaseOutput.job_id == job_id,
                    PhaseOutput.phase_name == "memory-check",
                )
            )
        ).scalar_one()
        usage_rows = tuple(
            (
                await session.execute(
                    select(AgentUsage)
                    .where(AgentUsage.homework_job_id == job_id)
                    .where(AgentUsage.operation == "solve:memory-check")
                    .order_by(AgentUsage.created_at)
                )
            ).scalars()
        )
        usage = tuple(
            UsageRecord(
                operation=row.operation,
                model_name=row.model_name,
                prompt_tokens=row.prompt_tokens,
                output_tokens=row.output_tokens,
                cached_tokens=row.cached_tokens,
                cost_usd=Decimal(
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
                ),
            )
            for row in usage_rows
        )
        return {
            "job_status": job.status,
            "job_attempts": job.attempts,
            "job_claimed_by": job.claimed_by,
            "job_claim_token": str(job.claim_token) if job.claim_token else None,
            "job_completed": job.completed_at is not None,
            "phase_status": phase.status,
            "solver_status": phase.solver_status,
            "output_md": phase.output_md,
            "error_message": phase.error_message,
            "usage": usage,
        }


async def _run_paid_smoke(preflight: Preflight) -> None:
    # Importing app.db creates the configured engine, so every boundary check
    # above has already completed before any application module is imported.
    from app.config import settings
    from app.schemas.solver import SolveVerdict
    from app.services import agent, events_bus, notion_archive, phase_judge, pipeline
    from app.services.agent import PhaseResult
    source = await _load_source_fixture(preflight.source_db_url)
    blocked = await _seed_job(source, correct_control=False)
    control = await _seed_job(source, correct_control=True)
    paid_gate = PaidSolverGate(preflight.max_cost_usd)
    events: list[tuple[str, str, dict]] = []
    archive_calls: list[uuid.UUID] = []
    generation_counts = {blocked.job_id: 0, control.job_id: 0}
    mismatch_detections = 0
    real_run_phase = agent.run_phase
    real_spawn = agent._spawn

    async def bounded_spawn(**kwargs):
        return await paid_gate.call(real_spawn, **kwargs)

    async def routed_run_phase(**kwargs):
        nonlocal mismatch_detections
        phase_name = kwargs["phase_name"]
        job_id = kwargs.get("homework_job_id")
        usage = {"prompt_tokens": 11, "output_tokens": 7, "raw": {}}
        if phase_name == "__solver__":
            if job_id == control.job_id:
                parsed = SolveVerdict(agrees=True, discrepancies=[])
                return PhaseResult(text=parsed.model_dump_json(), parsed=parsed, usage=usage)
            result = await real_run_phase(**kwargs)
            verdict = result.parsed
            if isinstance(verdict, SolveVerdict) and any(
                discrepancy.confidence == "high"
                for discrepancy in verdict.discrepancies
            ):
                mismatch_detections += 1
            return result
        if phase_name == "__judge__":
            verdict = phase_judge.Verdict(passed=True, failures=[])
            return PhaseResult(text=verdict.model_dump_json(), parsed=verdict, usage=usage)
        if phase_name != "memory-check":
            raise AcceptanceFailure(f"unexpected generated phase {phase_name!r}")
        generation_counts[job_id] += 1
        if job_id == control.job_id:
            output = CORRECT_CONTROL_OUTPUT
        else:
            output = (
                INITIAL_WRONG_OUTPUT
                if generation_counts[job_id] == 1
                else FINAL_WRONG_OUTPUT
            )
        return PhaseResult(text=output, parsed=None, usage=usage)

    async def record_event(resource_id: str, event: str, data: dict):
        events.append((resource_id, event, data))

    async def record_archive(job_id: uuid.UUID, **_kwargs):
        archive_calls.append(job_id)

    async def no_close(_resource_id: str):
        return None

    with tempfile.TemporaryDirectory(prefix="solver-fail-closed-") as tmp:
        pdf = Path(tmp) / "source.pdf"
        pdf.write_bytes(b"%PDF-1.4 bounded solver smoke")
        with (
            patch.object(agent, "run_phase", routed_run_phase),
            patch.object(agent, "_spawn", bounded_spawn),
            patch.object(pipeline.book_fetch, "ensure_book_pdf_sync", lambda *_a, **_k: pdf),
            patch.object(events_bus, "publish", record_event),
            patch.object(events_bus, "close", no_close),
            patch.object(notion_archive, "archive_job", record_archive),
            patch.object(settings, "structured_output_enabled", False),
            patch.object(settings, "extract_coverage_check_enabled", False),
            patch.object(settings, "solver_enabled", True),
            patch.object(settings, "max_solve_regens", 1),
            patch.object(settings, "api_max_output_tokens", MAX_PROVIDER_OUTPUT_TOKENS),
        ):
            blocked_lease = await _claim_and_run(blocked.job_id)
            control_lease = await _claim_and_run(control.job_id)

    blocked_rows = await _collect_rows(blocked.job_id)
    control_rows = await _collect_rows(control.job_id)
    blocked_resource = f"job:{blocked.job_id}"
    snapshot = AcceptanceSnapshot(
        blocked_phase_status=blocked_rows["phase_status"],
        blocked_solver_status=blocked_rows["solver_status"],
        blocked_output_md=blocked_rows["output_md"],
        blocked_error_message=blocked_rows["error_message"],
        blocked_job_status=blocked_rows["job_status"],
        blocked_job_attempts=blocked_rows["job_attempts"],
        blocked_job_claimed_by=blocked_rows["job_claimed_by"],
        blocked_job_claim_token=blocked_rows["job_claim_token"],
        expected_claim_token=str(blocked_lease.claim_token),
        blocked_job_completed=blocked_rows["job_completed"],
        blocked_phase_completed_events=sum(
            resource == blocked_resource
            and event == "phase_completed"
            and data.get("phase_name") == "memory-check"
            for resource, event, data in events
        ),
        blocked_job_completed_events=sum(
            resource == blocked_resource and event == "job_completed"
            for resource, event, _data in events
        ),
        blocked_archive_calls=archive_calls.count(blocked.job_id),
        usage_rows=blocked_rows["usage"],
        mismatch_detections=mismatch_detections,
        control_phase_status=control_rows["phase_status"],
        control_solver_status=control_rows["solver_status"],
        control_job_status=control_rows["job_status"],
        control_generation_calls=generation_counts[control.job_id],
        control_archive_calls=archive_calls.count(control.job_id),
    )
    report = assert_acceptance(snapshot, preflight.max_cost_usd)
    print(
        "PASS: persistent mismatch blocked; control done/ok; "
        f"paid_calls={report.paid_calls}; cost=${report.total_cost_usd:.4f}; "
        f"blocked_job={blocked.job_id}; control_job={control.job_id}"
    )
    assert control_rows["job_claim_token"] == str(control_lease.claim_token)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[[Preflight], Awaitable[None]] | None = None,
) -> int:
    preflight = _parse_preflight(argv, os.environ if environ is None else environ)
    asyncio.run((runner or _run_paid_smoke)(preflight))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
