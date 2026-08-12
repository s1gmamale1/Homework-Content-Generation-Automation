"""Task 9: fence the AUTOMATIC (pipeline) Notion archive on the winning
claim_token, layered ON TOP OF the worklog-0129 guards (idempotency +
created_at direction guard), never replacing them. Token-less callers
(operator force-archive, retry_archive_job, batch re-archive) are unaffected.

Mocking pattern mirrors tests/services/test_notion_archive_stamp.py."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na


def _job(*, status="done", claim_token=None, archived=False, created_at=None):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(),
        subject="geometriya-g7-11", output_language="uz",
        created_at=created_at or datetime(2026, 6, 1, tzinfo=timezone.utc),
        notion_archived_at=(datetime.now(timezone.utc) if archived else None),
        status=status, claim_token=claim_token,
    )


def _section(job, *, page_id=None, archived_job_id=None):
    return SimpleNamespace(
        id=job.toc_entry_id, section_number="1", section_title="L",
        notion_homework_page_id=page_id, notion_archived_job_id=archived_job_id,
        notion_lesson_page_id=None,
    )


def _wire(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"geometriya-g7-11|8": "subj"})
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    book = SimpleNamespace(grade="8", original_filename="g8.pdf")
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")
    return book, phase


@pytest.mark.asyncio
async def test_obsolete_token_refused_no_publish_no_pointer(monkeypatch):
    """A job whose current claim_token differs from the one presented (the
    reclaimed-job case: an obsolete worker still holds an old token) must not
    publish and must not touch the section pointer."""
    winning_token = uuid4()
    obsolete_token = uuid4()
    job = _job(status="done", claim_token=winning_token)
    section = _section(job)
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw"))) as push:
        await na.archive_job(job.id, claim_token=obsolete_token)
    push.assert_not_awaited()
    set_ptr.assert_not_awaited()
    stamp.assert_not_awaited()
    set_arch.assert_not_awaited()


@pytest.mark.asyncio
async def test_obsolete_token_refused_when_job_no_longer_done(monkeypatch):
    """Even a MATCHING token must not archive once the job has moved off
    `done` (defensive: status is part of the fence, not just the token)."""
    token = uuid4()
    job = _job(status="running", claim_token=token)  # reclaimed and re-run
    section = _section(job)
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw"))) as push:
        await na.archive_job(job.id, claim_token=token)
    push.assert_not_awaited()
    set_ptr.assert_not_awaited()


@pytest.mark.asyncio
async def test_winning_token_archives_once(monkeypatch):
    """The job's own current claim_token, presented back, publishes exactly
    once and writes the pointer — the 0129 guards (first_archive here) still
    decide the replace flag."""
    job = _job(status="done", claim_token=uuid4())
    section = _section(job, page_id=None, archived_job_id=None)  # never filed
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw"))) as push:
        await na.archive_job(job.id, claim_token=job.claim_token)
    push.assert_awaited_once()
    set_ptr.assert_awaited_once()
    stamp.assert_awaited_once()
    set_arch.assert_awaited_once()


@pytest.mark.asyncio
async def test_winning_token_still_respects_0129_idempotency_guard(monkeypatch):
    """A winning token does not bypass the 0129 already-archived idempotency
    short-circuit — the token check is an EXTRA precondition, not a
    replacement."""
    token = uuid4()
    job = _job(status="done", claim_token=token, archived=True)  # already archived
    section = _section(job)
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na, "_push_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id, claim_token=token)  # no force
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_toctou_pointer_update_rechecked(monkeypatch):
    """The Notion push runs with NO DB session held; if the job is reclaimed
    (new claim_token minted) during that window, the SECOND session (the
    pointer-update) must re-check and refuse — a check only in the first
    session would leave a stale pointer write."""
    original_token = uuid4()
    rotated_token = uuid4()
    job = _job(status="done", claim_token=original_token)
    reclaimed_job = _job(status="done", claim_token=rotated_token)
    reclaimed_job.id = job.id  # same job row, token rotated mid-flight
    section = _section(job)
    book, phase = _wire(monkeypatch)

    calls = {"n": 0}

    async def get_side_effect(session, jid):
        calls["n"] += 1
        return job if calls["n"] == 1 else reclaimed_job

    with patch.object(na.jobs_repo, "get", AsyncMock(side_effect=get_side_effect)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.toc_repo, "set_notion_lesson_page_id", AsyncMock()) as set_lesson, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=("lesson-xyz", "hw"))) as push:
        await na.archive_job(job.id, claim_token=original_token)
    # The push (Notion I/O) already ran on the winning check in session 1 —
    # it is the pointer write that must be refused once the re-check (in the
    # pointer-update session) finds the token no longer current. The push mock
    # deliberately returns a non-None lesson_id here so a regression that moved
    # the lesson-stamp write OUTSIDE the fence (or dropped the fence re-check
    # for it) would show up as an unwanted await, not hide behind lesson_id
    # always being None.
    push.assert_awaited_once()
    set_ptr.assert_not_awaited()
    stamp.assert_not_awaited()
    set_lesson.assert_not_awaited()
    set_arch.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_less_call_archives_as_today(monkeypatch):
    """The three existing operator/batch callers pass no claim_token — the
    fence is skipped entirely and the 0129 guards alone decide, unchanged."""
    job = _job(status="done", claim_token=uuid4())  # status/token irrelevant
    section = _section(job, page_id=None, archived_job_id=None)
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw"))) as push:
        await na.archive_job(job.id)  # no claim_token — token-less path
    push.assert_awaited_once()
    set_ptr.assert_awaited_once()
    stamp.assert_awaited_once()
    set_arch.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_rearchive_token_less_bypasses_idempotency_guard(monkeypatch):
    """Task 10 gap: the operator re-archive path
    (`POST /jobs/{id}/retry-archive?force=true` -> `_force_rearchive_one` ->
    `archive_job(job_id, force=True)`, always token-less — app/api/v1/jobs.py)
    must keep functioning alongside fencing. Two things must both hold:
      * `force=True` bypasses the 0129 already-archived idempotency
        short-circuit (contrast `test_winning_token_still_respects_0129_idempotency_guard`,
        same archived=True setup but WITHOUT force — proves NO bypass there).
      * the token-less path is fence-exempt (`_claim_token_ok(job, None)` is
        always True), so this fires even though the job carries some
        claim_token nobody currently owns (a long-since-completed job)."""
    job = _job(status="done", claim_token=uuid4(), archived=True)  # already archived
    section = _section(job)  # first_archive=True (no pointer filed yet)
    book, phase = _wire(monkeypatch)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()) as set_ptr, \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value=(None, "hw"))) as push:
        await na.archive_job(job.id, force=True)  # token-less, force
    push.assert_awaited_once()
    set_ptr.assert_awaited_once()
    stamp.assert_awaited_once()
    set_arch.assert_awaited_once()


def test_claim_token_ok_helper():
    """Direct unit coverage of the fence predicate."""
    token = uuid4()
    done_matching = SimpleNamespace(status="done", claim_token=token)
    done_mismatch = SimpleNamespace(status="done", claim_token=uuid4())
    not_done = SimpleNamespace(status="running", claim_token=token)

    assert na._claim_token_ok(done_matching, None) is True   # token-less: always ok
    assert na._claim_token_ok(done_mismatch, None) is True   # token-less: always ok
    assert na._claim_token_ok(done_matching, token) is True
    assert na._claim_token_ok(done_mismatch, token) is False
    assert na._claim_token_ok(not_done, token) is False
