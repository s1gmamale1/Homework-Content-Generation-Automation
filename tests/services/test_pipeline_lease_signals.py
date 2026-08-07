"""Pipeline-level propagation of the fenced-lease control signals (Task 7).

The e2e (tests/integration/test_reclaim_fencing_e2e.py) proves the REPO
primitives fence correctly; this module proves the PIPELINE never swallows the
resulting control signal. The catastrophic case: a reclaimed job must NOT be
marked failed / abandoned-failed by the obsolete worker — the DAG scheduler's
`except Exception` (which does `_abandon_inflight("failed")`) and `run()`'s
`except Exception` (which does `jobs_repo.set_status(..., "failed")`) must both
re-raise `LeaseLostSignal` / `CancelWonSignal` untouched.

RED-proof: delete EITHER `except (LeaseLostSignal, CancelWonSignal): raise`
guard (the DAG wave handler, or run()'s top-level) and the matching test below
fails — the signal gets laundered into a `_abandon_inflight("failed")` /
`set_status("failed")` and the assertions fire.

$0: every DB session, model call and repo write is stubbed.
"""
from __future__ import annotations

import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from app.services import pipeline
from app.services.errors import CancelWonSignal, LeaseLostSignal
from app.services.lease import CancelRequested, JobLease, LeaseLost


def _lease() -> JobLease:
    jid = uuid.uuid4()
    return JobLease(job_id=jid, claim_token=uuid.uuid4(), owner_id="obsolete:1@sha")


# ===========================================================================
# The DAG wave scheduler must re-raise the control signal, NOT abandon-fail.
# ===========================================================================


@pytest.mark.parametrize("signal_cls", [LeaseLostSignal, CancelWonSignal])
async def test_scheduler_reraises_control_signal_without_abandon_failed(
    monkeypatch, signal_cls
):
    # Every phase is immediately ready (no deps) so the one phase launches at once.
    monkeypatch.setattr(pipeline, "resolve_phase_deps", lambda name, phases: set())

    async def _boom(**kw):
        raise signal_cls()

    monkeypatch.setattr(pipeline, "_execute_one_phase", _boom)

    # Spy: _abandon_inflight is the "failed"/"pending" phase-row writer. A
    # control signal must never reach it (the job is no longer ours to mutate).
    abandon_calls: list = []

    async def _spy_abandon(job_id, phase_names, status, reason):
        abandon_calls.append((status, reason))

    monkeypatch.setattr(pipeline, "_abandon_inflight", _spy_abandon)

    with pytest.raises(signal_cls):
        await pipeline._run_content_phases_parallel(
            job_id=uuid.uuid4(),
            resource_id="job:x",
            log=logger,
            content_phases=["preview"],
            phase_order_offset=1,
            subject="english",
            provider="claude",
            model=None,
            pdf_path=Path("/fake/book.pdf"),
            file_phases=set(),
            section_data={"title": "t", "number": "1.1"},
            lesson_context="ctx",
            prior_outputs={},
            difficulty=None,
            lease=_lease(),
        )

    assert abandon_calls == [], (
        "a control signal must NOT trigger _abandon_inflight — that would write "
        f"a phase row for a job we no longer own (got {abandon_calls})"
    )


# ===========================================================================
# run() must propagate the signal and never mark the job failed.
# ===========================================================================


@pytest.fixture()
def run_head(monkeypatch):
    """Stub run()'s context-load head so the first fenced job write (the
    `running` status write) is reached with nothing else touching the DB."""
    ns = types.SimpleNamespace()

    job = types.SimpleNamespace(
        id=uuid.uuid4(), book_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
        provider="claude", model=None, transport="cli",
        extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit", custom_prompts=None, selected_phases=None,
        judge_provider=None, judge_model=None, solver_provider=None,
        solver_model=None, extract_provider=None, extract_model=None,
        batch_id=None, output_language="uz",
    )
    book = types.SimpleNamespace(
        id=job.book_id, subject="english", grade="5", file_size_bytes=123,
    )
    section = types.SimpleNamespace(
        id=job.toc_entry_id, section_title="Tenses", section_number="1.1",
        page_start=1, page_end=5, chapter_title="Ch1", order_index=0,
    )
    ld = types.SimpleNamespace(
        extract_provider="gemini", extract_model=None, judge_provider="claude",
        judge_model=None, solver_provider="claude", solver_model=None,
        solver_boss_arena_enabled=True,
    )
    ns.job, ns.book, ns.section = job, book, section

    monkeypatch.setattr(pipeline.jobs_repo, "get", AsyncMock(return_value=job))
    monkeypatch.setattr(pipeline.books_repo, "get", AsyncMock(return_value=book))
    monkeypatch.setattr(pipeline.toc_repo, "get", AsyncMock(return_value=section))
    monkeypatch.setattr(
        pipeline.toc_repo, "get_next_in_book", AsyncMock(return_value=None)
    )
    import app.repositories.launch_defaults as ld_repo
    monkeypatch.setattr(ld_repo, "get", AsyncMock(return_value=ld))
    monkeypatch.setattr(
        pipeline.book_fetch, "ensure_book_pdf_sync",
        lambda *a, **k: Path("/fake/book.pdf"),
    )

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    ns.publish = AsyncMock()
    ns.close = AsyncMock()
    monkeypatch.setattr(pipeline.events_bus, "publish", ns.publish)
    monkeypatch.setattr(pipeline.events_bus, "close", ns.close)

    # jobs_repo.set_status is the fenced write under test; each test wires its
    # own return via ns.set_status (AsyncMock).
    ns.set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", ns.set_status)
    return ns


@pytest.mark.parametrize(
    "sentinel, signal_cls",
    [(LeaseLost, LeaseLostSignal), (CancelRequested, CancelWonSignal)],
)
async def test_run_propagates_control_signal_and_never_marks_failed(
    run_head, sentinel, signal_cls
):
    # The first fenced job write (the `running` status write near the top of
    # run()) reports the lease is gone / a cancel already finalized.
    run_head.set_status.return_value = sentinel

    with pytest.raises(signal_cls):
        await pipeline.run(run_head.job.id, _lease())

    # The catastrophic guarantee: NO `failed` job write happened.
    failed_writes = [
        c for c in run_head.set_status.call_args_list
        if (len(c.args) > 2 and c.args[2] == "failed")
        or c.kwargs.get("status") == "failed"
    ]
    assert failed_writes == [], (
        "a reclaimed / cancel-won job must NEVER be marked failed by the "
        f"obsolete worker (got {failed_writes})"
    )
    # ...and no `error` SSE event was published for it either.
    error_events = [
        c for c in run_head.publish.call_args_list
        if len(c.args) > 1 and c.args[1] == "error"
    ]
    assert error_events == [], f"no error event on a control signal (got {error_events})"
    # The SSE bus is still closed on the way out (the `finally`).
    run_head.close.assert_awaited_once()
