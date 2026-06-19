"""TDD tests for fleet-api-4: never-pay-twice prior-spend warning.

Tests verify that when a force re-launch targets a section that already has a
completed paid-api job, the response surfaces prior_api_cost_usd + would_rebill
per section.  A cli-only prior or never-generated section must NOT trigger the
warning.  A non-force launch must be unaffected.

Covers:
  1. batch force re-launch over a section WITH a prior done api job →
     rebill_warnings list carries {toc_entry_id, prior_api_cost_usd > 0,
     would_rebill: true} for that section.
  2. batch force re-launch over a never-generated section → would_rebill false,
     prior cost 0.
  3. batch force re-launch over a section whose only prior job was cli →
     would_rebill false, prior_api_cost_usd 0.0.
  4. force=false (normal launch) → no rebill_warnings in response (no cost
     check because the section is skipped via existing active job).
  5. /generate force=True over a section with a prior done api job →
     would_rebill true and prior_api_cost_usd > 0 in the response.
  6. /generate force=True over a never-generated section → no warning.

Each test is written so removing the corresponding branch causes it to fail.
"""

from __future__ import annotations

import pytest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

BOOK_ID = uuid4()
SECTION_ID = uuid4()

_FAKE_BOOK = SimpleNamespace(
    id=BOOK_ID,
    status="toc_ready",
    subject="math-algebra",
    grade="8",
    original_filename=None,
    error_message=None,
)

_FAKE_SECTION = SimpleNamespace(
    id=SECTION_ID,
    book_id=BOOK_ID,
    title="Lesson 1",
)

_FAKE_BATCH = SimpleNamespace(
    id=uuid4(),
    book_id=BOOK_ID,
    subject="math-algebra",
    grade="8",
    provider="claude",
    model="claude-sonnet-4-6",
    transport="api",
    extract_transport="inherit",
    judge_transport="inherit",
    extract_provider=None,
    extract_model=None,
    judge_provider=None,
    judge_model=None,
    created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
)

_FAKE_JOB = SimpleNamespace(
    id=uuid4(),
    book_id=BOOK_ID,
    toc_entry_id=SECTION_ID,
    subject="math-algebra",
    status="pending",
    current_phase=None,
    error_message=None,
    provider="claude",
    model="claude-sonnet-4-6",
    transport="api",
    extract_transport="inherit",
    judge_transport="inherit",
    extract_provider=None,
    extract_model=None,
    judge_provider=None,
    judge_model=None,
    phase_outputs=[],
    notion_skip_reason=None,
)

_HDR = {"Authorization": "Bearer 123"}
_BATCH_BODY = {
    "book_id": str(BOOK_ID),
    "toc_entry_ids": [str(SECTION_ID)],
    "provider": "claude",
    "model": "claude-sonnet-4-6",
    "transport": "api",
    "force": True,
}
_GEN_BODY = {
    "provider": "claude",
    "model": "claude-sonnet-4-6",
    "transport": "api",
    "force": True,
}


def _app():
    from main import app
    return app


def _client():
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t")


def _make_fake_session():
    """Build a MagicMock session where all awaitable methods are AsyncMocks."""
    s = MagicMock()
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.rollback = AsyncMock()
    s.close = AsyncMock()
    return s


def _session_override():
    """FastAPI dependency override: yield a fake async session, no DB."""
    from app.db import get_session
    async def _fake():
        yield _make_fake_session()
    return get_session, _fake


# ─── batch tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_force_with_prior_api_job_warns():
    """Force re-launch over a section with a prior done api job must include
    would_rebill=True and prior_api_cost_usd > 0 for that section.

    Removing the section_prior_api_cost call / rebill_warnings assembly makes
    this fail because rebill_warnings would be absent or empty.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book", AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book", AsyncMock(return_value=_FAKE_BATCH)),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            # force=True: latest_for_section returns None → branch falls to create
            patch.object(batch_mod.jobs_repo, "latest_for_section", AsyncMock(return_value=None)),
            patch.object(batch_mod.jobs_repo, "create", AsyncMock(return_value=_FAKE_JOB)),
            patch.object(batch_mod.batches_repo, "rollup_for_batch", AsyncMock(return_value={"done": 1})),
            # section_prior_api_cost returns ($1.23, had_done=True) → should warn
            patch.object(batch_mod.cost_repo, "section_prior_api_cost",
                         AsyncMock(return_value=(1.23, True))),
        ):
            async with _client() as c:
                resp = await c.post("/api/v1/jobs/batch", headers=_HDR, json=_BATCH_BODY)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "rebill_warnings" in data, "rebill_warnings key must be present in response"
    warnings = data["rebill_warnings"]
    assert len(warnings) == 1, f"Expected 1 warning entry, got {len(warnings)}: {warnings}"
    w = warnings[0]
    assert w["toc_entry_id"] == str(SECTION_ID)
    assert w["would_rebill"] is True, "would_rebill must be True for a prior done api job"
    assert w["prior_api_cost_usd"] > 0, f"prior_api_cost_usd must be > 0, got {w['prior_api_cost_usd']}"
    assert w["prior_api_cost_usd"] == pytest.approx(1.23)


@pytest.mark.asyncio
async def test_batch_force_never_generated_no_warning():
    """Force re-launch over a never-generated section (no prior job) must
    produce would_rebill=False and prior_api_cost_usd=0.0.

    Removing the 'had_done_api_job' guard makes this fail: the prior check
    returns (0.0, False) so would_rebill must be False.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book", AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book", AsyncMock(return_value=_FAKE_BATCH)),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            patch.object(batch_mod.jobs_repo, "latest_for_section", AsyncMock(return_value=None)),
            patch.object(batch_mod.jobs_repo, "create", AsyncMock(return_value=_FAKE_JOB)),
            patch.object(batch_mod.batches_repo, "rollup_for_batch", AsyncMock(return_value={})),
            # No prior job at all: (0.0, False)
            patch.object(batch_mod.cost_repo, "section_prior_api_cost",
                         AsyncMock(return_value=(0.0, False))),
        ):
            async with _client() as c:
                resp = await c.post("/api/v1/jobs/batch", headers=_HDR, json=_BATCH_BODY)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "rebill_warnings" in data
    warnings = data["rebill_warnings"]
    assert len(warnings) == 1
    w = warnings[0]
    assert w["toc_entry_id"] == str(SECTION_ID)
    assert w["would_rebill"] is False
    assert w["prior_api_cost_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_batch_force_cli_only_prior_no_warning():
    """Force re-launch over a section whose only prior job was cli must
    produce would_rebill=False (cli is free).

    section_prior_api_cost returns (0.0, False) for a cli-only prior because
    had_done_api_job is False (no done api job).
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    _cli_body = {**_BATCH_BODY, "transport": "cli"}
    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book", AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book", AsyncMock(return_value=_FAKE_BATCH)),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            patch.object(batch_mod.jobs_repo, "latest_for_section", AsyncMock(return_value=None)),
            patch.object(batch_mod.jobs_repo, "create", AsyncMock(return_value=_FAKE_JOB)),
            patch.object(batch_mod.batches_repo, "rollup_for_batch", AsyncMock(return_value={})),
            # cli-only prior: done job exists but auth_mode=cli → cost 0, had_done=False
            patch.object(batch_mod.cost_repo, "section_prior_api_cost",
                         AsyncMock(return_value=(0.0, False))),
        ):
            async with _client() as c:
                resp = await c.post("/api/v1/jobs/batch", headers=_HDR, json=_cli_body)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "rebill_warnings" in data
    warnings = data["rebill_warnings"]
    assert len(warnings) == 1
    w = warnings[0]
    assert w["would_rebill"] is False
    assert w["prior_api_cost_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_batch_no_force_unaffected():
    """Non-force launch with an existing active job (skipped path) must NOT
    call section_prior_api_cost and must NOT include rebill_warnings entries.

    The force=False path short-circuits via the existing job check, so the
    cost check is never reached.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    _existing_job = SimpleNamespace(
        id=uuid4(), batch_id=_FAKE_BATCH.id, transport="api",
    )
    mock_prior = AsyncMock(return_value=(1.23, True))
    _noforce_body = {**_BATCH_BODY, "force": False}

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book", AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book", AsyncMock(return_value=_FAKE_BATCH)),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            # existing active job → section is skipped (never reaches force path)
            patch.object(batch_mod.jobs_repo, "find_active_for_section",
                         AsyncMock(return_value=_existing_job)),
            patch.object(batch_mod.batches_repo, "rollup_for_batch", AsyncMock(return_value={"done": 1})),
            patch.object(batch_mod.cost_repo, "section_prior_api_cost", mock_prior),
        ):
            async with _client() as c:
                resp = await c.post("/api/v1/jobs/batch", headers=_HDR, json=_noforce_body)
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    # Non-force path: section was skipped (existing job), no cost check needed
    mock_prior.assert_not_called()
    # rebill_warnings must be empty (no new jobs created on the force path)
    warnings = data.get("rebill_warnings", [])
    assert warnings == [], f"Expected empty rebill_warnings for force=False, got {warnings}"


# ─── /generate tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_force_with_prior_api_job_warns():
    """force=True /generate over a section with a prior done api job must
    return would_rebill=True and prior_api_cost_usd > 0 in the job response.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(jobs_mod.toc_repo, "get", AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            # Disable backpressure check (returns 0 queue depth)
            patch.object(jobs_mod.jobs_repo, "queue_depth", AsyncMock(return_value=0)),
            patch.object(jobs_mod.jobs_repo, "create", AsyncMock(return_value=_FAKE_JOB)),
            patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=_FAKE_JOB)),
            # Prior done api job
            patch.object(jobs_mod.cost_repo, "section_prior_api_cost",
                         AsyncMock(return_value=(2.50, True))),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json=_GEN_BODY,
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data.get("would_rebill") is True, "would_rebill must be True for a prior done api job"
    assert data.get("prior_api_cost_usd") == pytest.approx(2.50), (
        f"prior_api_cost_usd must be ~2.50, got {data.get('prior_api_cost_usd')}"
    )


@pytest.mark.asyncio
async def test_generate_force_never_generated_no_warning():
    """force=True /generate over a never-generated section must return
    would_rebill=False and prior_api_cost_usd=0.0.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get", AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(jobs_mod.toc_repo, "get", AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate", AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "queue_depth", AsyncMock(return_value=0)),
            patch.object(jobs_mod.jobs_repo, "create", AsyncMock(return_value=_FAKE_JOB)),
            patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=_FAKE_JOB)),
            # Never generated: no prior job
            patch.object(jobs_mod.cost_repo, "section_prior_api_cost",
                         AsyncMock(return_value=(0.0, False))),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json=_GEN_BODY,
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    # Either absent or explicitly False/0 — must not be would_rebill=True
    assert data.get("would_rebill") is not True, (
        "would_rebill must not be True for a never-generated section"
    )
    cost = data.get("prior_api_cost_usd", 0.0)
    assert cost == pytest.approx(0.0), f"prior_api_cost_usd must be 0.0, got {cost}"
