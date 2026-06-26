import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.repositories import jobs as jobs_repo


def test_set_notion_skip_reason_sets_field():
    job = SimpleNamespace(notion_skip_reason=None)
    session = SimpleNamespace(get=AsyncMock(return_value=job))
    asyncio.run(jobs_repo.set_notion_skip_reason(session, uuid4(), "no Notion page for x|5"))
    assert job.notion_skip_reason == "no Notion page for x|5"


def test_set_notion_archived_clears_skip_reason():
    from datetime import datetime, timezone
    job = SimpleNamespace(notion_archived_at=None, notion_skip_reason="stale")
    session = SimpleNamespace(get=AsyncMock(return_value=job))
    asyncio.run(jobs_repo.set_notion_archived(session, uuid4(), datetime.now(timezone.utc)))
    assert job.notion_archived_at is not None
    assert job.notion_skip_reason is None   # cleared on success


import app.services.notion_archive as na


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        return None


def test_archive_marks_skip_on_no_mapping():
    jid = uuid4()
    job = SimpleNamespace(id=jid, notion_archived_at=None, subject="math-algebra",
                          book_id=uuid4(), toc_entry_id=uuid4())
    book = SimpleNamespace(grade="5", original_filename="x.pdf")
    section = SimpleNamespace(id=uuid4(), section_number="1", section_title="T")
    set_skip = AsyncMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na.settings, "notion_subject_pages", {}), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(jid))
    set_skip.assert_awaited_once()
    assert "math-algebra|5" in set_skip.await_args.args[2]


def test_archive_no_skip_mark_when_disabled():
    set_skip = AsyncMock()
    with patch.object(na.settings, "notion_enabled", False), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(uuid4()))
    set_skip.assert_not_awaited()


def test_archive_marks_skip_on_push_exception():
    """A push that fails every attempt is retried _PUSH_MAX_ATTEMPTS times, then
    records notion_skip_reason='push error: <Type>' instead of vanishing."""
    jid = uuid4()
    job = SimpleNamespace(id=jid, notion_archived_at=None, subject="math-algebra",
                          book_id=uuid4(), toc_entry_id=uuid4())
    book = SimpleNamespace(grade="5", original_filename="x.pdf")
    section = SimpleNamespace(id=uuid4(), section_number="1", section_title="T")
    done_phase = SimpleNamespace(phase_name="case-based-preview", output_md="# x", status="done")
    push = MagicMock(side_effect=RuntimeError("boom"))
    set_skip = AsyncMock()
    sleeps = AsyncMock()
    with patch.object(na.settings, "notion_enabled", True), \
         patch.object(na.settings, "notion_api_key", "k"), \
         patch.object(na.settings, "notion_subject_pages", {"math-algebra|5": "subj"}), \
         patch.object(na, "SessionLocal", lambda: _FakeSession()), \
         patch.object(na, "NotionClientWrapper", MagicMock()), \
         patch.object(na, "_push_to_notion", push), \
         patch.object(na.asyncio, "sleep", sleeps), \
         patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[done_phase])), \
         patch.object(na.jobs_repo, "set_notion_skip_reason", set_skip):
        asyncio.run(na.archive_job(jid))
    assert push.call_count == na._PUSH_MAX_ATTEMPTS          # retried, not one-shot
    set_skip.assert_awaited()
    assert "push error" in set_skip.await_args.args[2]
    assert "RuntimeError" in set_skip.await_args.args[2]
