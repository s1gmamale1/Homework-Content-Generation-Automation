"""TDD tests for Task 6: batch launch resolves + stamps output_language.

Tests:
  (a) launch with output_language="en" → batch + jobs carry output_language="en"
  (b) launch omitting output_language with global default "ru" → rows carry "ru"
  (c) output_language="fr" → 400

Offline (no DB needed): mock all repo calls, assert on what gets passed + what
the 400 rejects early (before any DB write).
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
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
    source_language=None,  # Phase 1 column; None → global default wins
)

_FAKE_SECTION = SimpleNamespace(
    id=SECTION_ID,
    book_id=BOOK_ID,
    title="Lesson 1",
)

_HDR = {"Authorization": "Bearer 123"}


def _make_fake_batch(output_language="uz"):
    return SimpleNamespace(
        id=uuid4(),
        book_id=BOOK_ID,
        subject="math-algebra",
        grade="8",
        provider="claude",
        model=None,
        transport="cli",
        extract_transport="inherit",
        judge_transport="inherit",
        solver_transport="inherit",
        extract_provider=None,
        extract_model=None,
        judge_provider=None,
        judge_model=None,
        solver_provider=None,
        solver_model=None,
        output_language=output_language,
        created_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
        paused_at=None,
        paused_reason=None,
        session_limit_strategy="inherit",
    )


def _make_fake_job(output_language="uz"):
    return SimpleNamespace(
        id=uuid4(),
        book_id=BOOK_ID,
        toc_entry_id=SECTION_ID,
        subject="math-algebra",
        status="pending",
        current_phase=None,
        error_message=None,
        provider="claude",
        model=None,
        transport="cli",
        extract_transport="inherit",
        judge_transport="inherit",
        solver_transport="inherit",
        extract_provider=None,
        extract_model=None,
        judge_provider=None,
        judge_model=None,
        solver_provider=None,
        solver_model=None,
        output_language=output_language,
        phase_outputs=[],
        notion_skip_reason=None,
        custom_prompts=None,
        selected_phases=None,
    )


def _make_fake_ld(output_language="uz"):
    """Fake launch_defaults singleton."""
    return SimpleNamespace(
        judge_provider="gemini", judge_model="gemini-2.5-flash",
        judge_transport="inherit", extract_provider="gemini",
        extract_model="gemini-2.5-flash", extract_transport="inherit",
        solver_provider="gemini", solver_model="gemini-3.1-pro-preview",
        solver_transport="inherit",
        output_language=output_language,
    )


def _app():
    from main import app
    return app


def _client():
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t")


def _make_fake_session():
    s = MagicMock()
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.rollback = AsyncMock()
    s.close = AsyncMock()
    return s


def _session_override():
    from app.db import get_session
    async def _fake():
        yield _make_fake_session()
    return get_session, _fake


# ─── (c) invalid language → 400 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_invalid_output_language_rejected():
    """output_language='fr' (not in {uz, en, ru}) must yield HTTP 400 and
    mention 'output_language' in the detail.

    Removing the validate_output_language call makes this return 201 (or
    crash in a different way), causing this assertion to fail.
    """
    import app.api.v1.batch as batch_mod

    app_obj = _app()
    app_obj.dependency_overrides[_session_override()[0]] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book",
                         AsyncMock(return_value=[_FAKE_SECTION])),
        ):
            async with _client() as c:
                resp = await c.post(
                    "/api/v1/jobs/batch",
                    headers=_HDR,
                    json={
                        "book_id": str(BOOK_ID),
                        "output_language": "fr",
                    },
                )
    finally:
        app_obj.dependency_overrides.pop(_session_override()[0], None)

    assert resp.status_code == 400, resp.text
    assert "output_language" in resp.json()["detail"]


# ─── (a) explicit output_language="en" → batch + jobs carry "en" ─────────────

@pytest.mark.asyncio
async def test_batch_explicit_output_language_en_threaded():
    """Launch with output_language='en' → get_or_create_for_book and jobs.create
    must be called with output_language='en'.

    Removing the output_language kwarg from either repo call causes the call
    assertion to fail (wrong or missing kwarg).
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    fake_batch = _make_fake_batch(output_language="en")
    fake_job = _make_fake_job(output_language="en")
    fake_ld = _make_fake_ld(output_language="uz")  # global default is uz; explicit wins

    mock_get_or_create = AsyncMock(return_value=fake_batch)
    mock_jobs_create = AsyncMock(return_value=fake_job)
    mock_find_active = AsyncMock(return_value=None)
    mock_latest = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book",
                         AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book",
                         mock_get_or_create),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(batch_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(batch_mod.jobs_repo, "latest_for_section",
                         mock_latest),
            patch.object(batch_mod.jobs_repo, "create",
                         mock_jobs_create),
            patch.object(batch_mod.batches_repo, "rollup_for_batch",
                         AsyncMock(return_value={"pending": 1})),
            patch.object(batch_mod.batches_repo, "archive_rollup_for_batch",
                         AsyncMock(return_value={"archived": 0, "unarchived": 0})),
        ):
            async with _client() as c:
                resp = await c.post(
                    "/api/v1/jobs/batch",
                    headers=_HDR,
                    json={
                        "book_id": str(BOOK_ID),
                        "output_language": "en",
                    },
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

    # get_or_create_for_book must have received output_language="en"
    _, kwargs = mock_get_or_create.call_args
    assert kwargs.get("output_language") == "en", (
        f"get_or_create_for_book called with output_language={kwargs.get('output_language')!r},"
        " expected 'en'"
    )

    # jobs.create must have received output_language="en"
    _, kwargs = mock_jobs_create.call_args
    assert kwargs.get("output_language") == "en", (
        f"jobs.create called with output_language={kwargs.get('output_language')!r},"
        " expected 'en'"
    )

    # find_active_for_section must have received output_language="en"
    _, kwargs = mock_find_active.call_args
    assert kwargs.get("output_language") == "en", (
        f"find_active_for_section called with output_language={kwargs.get('output_language')!r},"
        " expected 'en'"
    )

    # latest_for_section must have received output_language="en"
    _, kwargs = mock_latest.call_args
    assert kwargs.get("output_language") == "en", (
        f"latest_for_section called with output_language={kwargs.get('output_language')!r},"
        " expected 'en'"
    )


# ─── (b) omitted output_language → inherits global default "ru" ──────────────

@pytest.mark.asyncio
async def test_batch_inherits_global_default_output_language():
    """Launch omitting output_language with global default='ru' → batch + jobs
    carry output_language='ru'.

    If resolve_output_language is not called (or the result is not threaded in),
    the repos would receive None or 'uz' instead of 'ru', failing the assertions.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    fake_batch = _make_fake_batch(output_language="ru")
    fake_job = _make_fake_job(output_language="ru")
    fake_ld = _make_fake_ld(output_language="ru")  # global default is "ru"

    mock_get_or_create = AsyncMock(return_value=fake_batch)
    mock_jobs_create = AsyncMock(return_value=fake_job)
    mock_find_active = AsyncMock(return_value=None)
    mock_latest = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book",
                         AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(batch_mod.batches_repo, "get_or_create_for_book",
                         mock_get_or_create),
            patch.object(batch_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(batch_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(batch_mod.jobs_repo, "latest_for_section",
                         mock_latest),
            patch.object(batch_mod.jobs_repo, "create",
                         mock_jobs_create),
            patch.object(batch_mod.batches_repo, "rollup_for_batch",
                         AsyncMock(return_value={"pending": 1})),
            patch.object(batch_mod.batches_repo, "archive_rollup_for_batch",
                         AsyncMock(return_value={"archived": 0, "unarchived": 0})),
        ):
            async with _client() as c:
                resp = await c.post(
                    "/api/v1/jobs/batch",
                    headers=_HDR,
                    json={
                        "book_id": str(BOOK_ID),
                        # output_language omitted → should inherit from ld.output_language
                    },
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

    # get_or_create_for_book must have received output_language="ru" (global default)
    _, kwargs = mock_get_or_create.call_args
    assert kwargs.get("output_language") == "ru", (
        f"get_or_create_for_book called with output_language={kwargs.get('output_language')!r},"
        " expected 'ru' (inherited from global default)"
    )

    # jobs.create must have received output_language="ru"
    _, kwargs = mock_jobs_create.call_args
    assert kwargs.get("output_language") == "ru", (
        f"jobs.create called with output_language={kwargs.get('output_language')!r},"
        " expected 'ru' (inherited from global default)"
    )


# ─── preview path: find_active_for_section receives output_language ───────────

@pytest.mark.asyncio
async def test_batch_preview_passes_output_language():
    """preview=True path must also pass output_language to find_active_for_section
    and latest_for_section — otherwise the language-scoped lookup is incorrect
    even for the preview.
    """
    import app.api.v1.batch as batch_mod
    from app.db import get_session

    fake_ld = _make_fake_ld(output_language="en")
    mock_find_active = AsyncMock(return_value=None)
    mock_latest = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(batch_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(batch_mod.toc_repo, "list_for_book",
                         AsyncMock(return_value=[_FAKE_SECTION])),
            patch.object(batch_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(batch_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(batch_mod.jobs_repo, "latest_for_section",
                         mock_latest),
            patch.object(batch_mod.jobs_repo, "done_phase_count_for_job",
                         AsyncMock(return_value=0)),
        ):
            async with _client() as c:
                resp = await c.post(
                    "/api/v1/jobs/batch",
                    headers=_HDR,
                    json={
                        "book_id": str(BOOK_ID),
                        "output_language": "en",
                        "preview": True,
                    },
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["preview"] is True

    # find_active_for_section must have received output_language="en"
    _, kwargs = mock_find_active.call_args
    assert kwargs.get("output_language") == "en", (
        f"find_active_for_section (preview) called with "
        f"output_language={kwargs.get('output_language')!r}, expected 'en'"
    )
