"""Exercise pipeline.run with persisted rows and mocked external boundaries."""
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.repositories import launch_defaults
from app.services import pipeline
from app.services.lesson_errata import apply_lesson_errata
from app.services.lease import JobLease

HISTORY_ID = "768820b7-54ea-45d2-bbb4-d95275ef95e6"


def harness(monkeypatch, *, corrected=False, unrelated=False, reset_signal=None,
            reset_signal_after=0):
    section_id = str(uuid4()) if unrelated else HISTORY_ID
    original = (Path(__file__).parents[1] / "fixtures" / "lesson_errata" /
                "history-original.md").read_text(encoding="utf-8")
    md = apply_lesson_errata(original, section_id=section_id, subject="history") if corrected else original
    job_id, book_id = uuid4(), uuid4()
    lease = JobLease(job_id, uuid4(), "test-worker")
    rows = [NS(id=uuid4(), phase_name=name, status="done", output_md=text,
               phase_order=order, prompt_hash="old", model_name="test-model",
               tokens_input=3, tokens_output=4, validation_warnings=None,
               content_json={"old": True} if name != "extract" else None)
            for order, (name, text) in enumerate([
                ("extract", md), ("flashcards", "Old exercise: Sian Xuanxe daryosi bo‘yida.")])]
    resets, commits, consumed, archives, statuses = [], [], [], [], []

    class Session:
        async def commit(self):
            commits.append(deepcopy(rows))

    @asynccontextmanager
    async def session():
        snapshot = deepcopy(rows)
        try:
            yield Session()
        except BaseException:
            rows[:] = snapshot  # model transaction rollback on an aborted reset
            raise

    monkeypatch.setattr(pipeline, "SessionLocal", session)
    monkeypatch.setattr(pipeline.jobs_repo, "get", AsyncMock(return_value=NS(
        book_id=book_id, toc_entry_id=UUID(section_id), provider="gemini", model="test",
        batch_id=None, selected_phases=["flashcards"], output_language="ru")))
    monkeypatch.setattr(pipeline.books_repo, "get", AsyncMock(return_value=NS(
        id=book_id, subject="history", grade="5", file_size_bytes=0)))
    monkeypatch.setattr(pipeline.toc_repo, "get", AsyncMock(return_value=NS(
        id=UUID(section_id), section_title="Trade routes", section_number="18",
        page_start=1, page_end=2, chapter_title="", order_index=1)))
    monkeypatch.setattr(pipeline.toc_repo, "get_next_in_book", AsyncMock(return_value=None))
    monkeypatch.setattr(launch_defaults, "get", AsyncMock(return_value=NS(
        judge_provider="gemini", judge_model="test", solver_provider="gemini",
        solver_model="test", solver_boss_arena_enabled=False,
        extract_provider="gemini", extract_model="test")))
    monkeypatch.setattr(pipeline.book_fetch, "ensure_book_pdf", AsyncMock(return_value=Path("unused.pdf")))
    monkeypatch.setattr(pipeline.phase_repo, "list_for_job", AsyncMock(side_effect=lambda *a: rows))

    async def job_status(session, job_id, status, **kw):
        statuses.append(status)
        assert kw["claim_token"] == lease.claim_token
        return True

    async def reset(session, **kw):
        assert kw["lease"] == lease
        resets.append(kw["phase_name"])
        if reset_signal and len(resets) > reset_signal_after:
            return reset_signal
        row = next(r for r in rows if r.phase_name == kw["phase_name"])
        row.status, row.output_md, row.content_json = "pending", None, None
        row.claim_token = kw["lease"].claim_token
        return row

    async def phase_status(session, row_id, status, **kw):
        row = next(r for r in rows if r.id == row_id)
        assert kw["claim_token"] == lease.claim_token
        row.status = status
        row.output_md = kw.get("output_md", row.output_md)
        return True

    async def content(**kw):
        consumed.append(kw)
        # Model boundary substitutes a corrected exercise only when pending.
        if "flashcards" not in kw["prior_outputs"]:
            row = rows[1]
            assert row.status == "pending" and row.output_md is None and row.content_json is None
            row.output_md, row.status = "Sian shahridan", "done"

    async def archive(*a, **kw):
        archives.append(deepcopy(rows))

    monkeypatch.setattr(pipeline.jobs_repo, "set_status", job_status)
    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", phase_status)
    monkeypatch.setattr(pipeline, "_execute_one_phase", AsyncMock(side_effect=AssertionError("resume must not extract again")))
    monkeypatch.setattr(pipeline, "_run_content_phases_parallel", content)
    monkeypatch.setattr(pipeline, "_coverage_warnings_for_job", lambda rows: [])
    monkeypatch.setattr(pipeline.notion_archive, "archive_job", archive)
    monkeypatch.setattr(pipeline, "_log_token_summary", AsyncMock())
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    monkeypatch.setattr(pipeline.events_bus, "close", AsyncMock())
    return NS(job_id=job_id, lease=lease, rows=rows, resets=resets, commits=commits,
              consumed=consumed, archives=archives, statuses=statuses, original=md)


@pytest.mark.asyncio
async def test_changed_resumed_extract_invalidates_completed_dependencies_atomically(monkeypatch):
    h = harness(monkeypatch)
    await pipeline.run(h.job_id, lease=h.lease)
    assert set(h.resets) == {"extract", "flashcards"}
    assert h.consumed[0]["prior_outputs"] == {}
    assert "Xuanxe" not in h.consumed[0]["lesson_context"]
    assert h.consumed[0]["output_language"] == "ru"
    corrected_commits = [rows for rows in h.commits if rows[0].output_md != h.original]
    assert corrected_commits[0][0].status == "done"
    assert corrected_commits[0][1].status == "pending"
    assert corrected_commits[0][1].output_md is None
    assert all("Xuanxe" not in r.output_md for r in h.archives[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected, unrelated", [(True, False), (False, True)])
async def test_unchanged_resume_keeps_completed_phases(monkeypatch, corrected, unrelated):
    h = harness(monkeypatch, corrected=corrected, unrelated=unrelated)
    await pipeline.run(h.job_id, lease=h.lease)
    assert h.resets == []
    assert h.consumed[0]["prior_outputs"]["flashcards"].startswith("Old exercise:")
    assert h.archives[0][0].output_md == h.original


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel, signal", [
    (pipeline.LeaseLost, pipeline.LeaseLostSignal),
    (pipeline.CancelRequested, pipeline.CancelWonSignal),
])
async def test_resume_errata_reset_control_signal_cannot_archive_or_fail_job(monkeypatch, sentinel, signal):
    h = harness(monkeypatch, reset_signal=sentinel)
    with pytest.raises(signal):
        await pipeline.run(h.job_id, lease=h.lease)
    assert h.consumed == [] and h.archives == []
    assert "failed" not in h.statuses and "done" not in h.statuses


@pytest.mark.asyncio
async def test_interrupted_reset_rolls_back_primer_and_dependents_together(monkeypatch):
    h = harness(monkeypatch, reset_signal=pipeline.LeaseLost, reset_signal_after=1)
    before = deepcopy(h.rows)
    with pytest.raises(pipeline.LeaseLostSignal):
        await pipeline.run(h.job_id, lease=h.lease)
    assert h.rows == before
    assert all(rows[0].output_md == h.original for rows in h.commits)
    assert h.archives == []
