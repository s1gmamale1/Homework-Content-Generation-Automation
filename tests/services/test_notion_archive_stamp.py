from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na


def _job(archived=False, created_at=None):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(),
        subject="geometriya-g7-11", output_language="uz",
        created_at=created_at or datetime(2026, 6, 1, tzinfo=timezone.utc),
        notion_archived_at=(datetime.now(timezone.utc) if archived else None),
    )


def _section(job, *, page_id=None, archived_job_id=None):
    return SimpleNamespace(
        id=job.toc_entry_id, section_number="1", section_title="L", page_start=7, order_index=0,
        notion_homework_page_id=page_id, notion_archived_job_id=archived_job_id,
    )


def _wire(monkeypatch, job, section):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"geometriya-g7-11|8": "subj"})
    book = SimpleNamespace(grade="8", original_filename="g8.pdf", id=job.book_id)
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    return book, phase


@pytest.mark.asyncio
async def test_first_archive_stamps_producing_job(monkeypatch):
    job = _job()
    section = _section(job, page_id=None, archived_job_id=None)   # never filed
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)
    assert push.await_args.kwargs["replace"] is False       # empty page → plain write
    stamp.assert_awaited_once()
    assert stamp.await_args.args[2] == job.id


@pytest.mark.asyncio
async def test_regen_auto_replaces_own_older_output_and_restamps(monkeypatch):
    job = _job(created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))  # the newer regen job
    prior = SimpleNamespace(id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    section = _section(job, page_id="hw", archived_job_id=prior.id)   # page is OUR older output
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get",
                      AsyncMock(side_effect=lambda s, jid: job if jid == job.id else prior)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)                          # NO force
    assert push.await_args.kwargs["replace"] is True          # auto-replace fired
    assert stamp.await_args.args[2] == job.id                 # re-stamped to the newer job


@pytest.mark.asyncio
async def test_older_job_does_not_clobber_newer_stamp(monkeypatch):
    """C1 (GK2): an OLDER job re-archiving after a newer regen already stamped
    the page (e.g. its original push failed, operator retries it) must NOT
    auto-replace — that would rewrite stale content over fresh. Skip preserved,
    stamp untouched (still points at the newer job)."""
    old_job = _job(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = SimpleNamespace(id=uuid4(), created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    section = _section(old_job, page_id="hw", archived_job_id=newer.id)
    book, phase = _wire(monkeypatch, old_job, section)
    with patch.object(na.jobs_repo, "get",
                      AsyncMock(side_effect=lambda s, jid: old_job if jid == old_job.id else newer)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(old_job.id)                      # NO force
    assert push.await_args.kwargs["replace"] is False         # skip preserved
    stamp.assert_not_awaited()                                # newer stamp untouched


@pytest.mark.asyncio
async def test_missing_stamped_job_row_keeps_skip(monkeypatch):
    """C1 edge: stamped job row gone (only possible via book deletion) — no
    direction evidence, keep the skip; Refresh-stale remediates."""
    job = _job()
    section = _section(job, page_id="hw", archived_job_id=uuid4())  # stamp → missing row
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get",
                      AsyncMock(side_effect=lambda s, jid: job if jid == job.id else None)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)
    assert push.await_args.kwargs["replace"] is False
    stamp.assert_not_awaited()


@pytest.mark.asyncio
async def test_husk_no_stamp_no_replace(monkeypatch):
    """Populated page with a NULL stamp (pre-feature husk / human-edited-ours):
    skip-default preserved — no replace, no (mis-)stamp."""
    job = _job()
    section = _section(job, page_id="hw", archived_job_id=None)  # populated but unstamped
    book, phase = _wire(monkeypatch, job, section)
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=[])), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()) as stamp, \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw")) as push:
        await na.archive_job(job.id)
    assert push.await_args.kwargs["replace"] is False
    stamp.assert_not_awaited()
