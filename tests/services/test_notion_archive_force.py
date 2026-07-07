from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.notion_archive as na


def _done_archived_job():
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), toc_entry_id=uuid4(), subject="geometriya-g7-11",
        notion_archived_at=datetime.now(timezone.utc), output_language="uz",
    )


@pytest.mark.asyncio
async def test_archive_job_without_force_short_circuits_already_archived(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    job = _done_archived_job()

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na, "_push_with_retry", AsyncMock()) as push:
        await na.archive_job(job.id)          # no force
    push.assert_not_awaited()                  # early-return: no push at all


@pytest.mark.asyncio
async def test_archive_job_force_pushes_with_replace_on_already_archived(monkeypatch):
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    job = _done_archived_job()
    book = SimpleNamespace(grade="8", original_filename="8-sinf.pdf", id=job.book_id)
    section = SimpleNamespace(
        id=job.toc_entry_id, section_number="1", section_title="L",
        notion_homework_page_id=None, notion_archived_job_id=None,
    )
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"geometriya-g7-11|8": "subj"})

    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()), \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()) as set_arch, \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_with_retry", AsyncMock(return_value="hw_page")) as push:
        await na.archive_job(job.id, force=True)

    push.assert_awaited_once()
    assert push.await_args.kwargs["replace"] is True
    # N3: the force-success path runs the success write, which clears
    # notion_skip_reason (set_notion_archived sets notion_skip_reason=None).
    set_arch.assert_awaited_once()
