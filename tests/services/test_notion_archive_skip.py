import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
