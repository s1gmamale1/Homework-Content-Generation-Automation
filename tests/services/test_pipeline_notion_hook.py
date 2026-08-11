import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import app.services.notion_archive as notion_archive


def test_pipeline_calls_archive_job_after_done():
    src = Path("app/services/pipeline.py").read_text(encoding="utf-8")
    assert "notion_archive" in src, "pipeline must import/call notion_archive"
    assert "archive_job" in src
    tree = ast.parse(src)
    found_guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            call_names = {
                getattr(n.func, "attr", "")
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and hasattr(n, "func")
            }
            if "archive_job" in call_names:
                found_guarded = True
    assert found_guarded, "archive_job must be inside a try/except"


def test_failed_job_with_retained_markdown_is_not_archived(monkeypatch):
    """A blocked phase keeps its markdown for diagnosis, but the automatic
    archive hook must stop at the failed parent before reading/publishing it."""
    token = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        status="failed",
        claim_token=token,
        notion_archived_at=None,
    )
    retained = SimpleNamespace(
        phase_name="memory-check",
        status="failed",
        output_md="# Known-bad key retained for inspection",
    )
    monkeypatch.setattr(notion_archive.settings, "notion_enabled", True)
    monkeypatch.setattr(notion_archive.settings, "notion_api_key", "ntn_test")

    with patch.object(
        notion_archive.jobs_repo, "get", AsyncMock(return_value=job)
    ), patch.object(
        notion_archive.phase_repo, "list_for_job", AsyncMock(return_value=[retained])
    ) as list_phases, patch.object(
        notion_archive, "NotionClientWrapper", MagicMock()
    ) as client, patch.object(
        notion_archive, "_push_with_retry", AsyncMock()
    ) as push:
        asyncio.run(notion_archive.archive_job(job.id, claim_token=token))

    list_phases.assert_not_awaited()
    client.assert_not_called()
    push.assert_not_awaited()
