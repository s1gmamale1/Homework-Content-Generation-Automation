"""TDD tests for Task 7: single-job /generate resolves + stamps output_language.

Tests:
  (a) generate with output_language="en" → created job carries "en"
  (b) omitted output_language with global default "ru" → job carries "ru"
  (c) output_language="xx" → 400
  (d) idempotency lookup is language-scoped: an existing UZ active job is NOT
      adopted for an EN request → a new job is created

Offline (no DB needed): mock all repo calls.
"""
from __future__ import annotations

import pytest
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
    source_language="uz",  # Phase 2 column; uz book uses global default as tiebreak
)

_FAKE_SECTION = SimpleNamespace(
    id=SECTION_ID,
    book_id=BOOK_ID,
    title="Lesson 1",
)

_HDR = {"Authorization": "Bearer 123"}


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
async def test_generate_invalid_output_language_rejected():
    """output_language='xx' (not in {uz, en, ru}) must yield HTTP 400.

    Removing the validate_output_language call makes this return 201/500,
    causing this assertion to fail.
    """
    import app.api.v1.jobs as jobs_mod

    app_obj = _app()
    app_obj.dependency_overrides[_session_override()[0]] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={"output_language": "xx"},
                )
    finally:
        app_obj.dependency_overrides.pop(_session_override()[0], None)

    assert resp.status_code == 400, resp.text
    assert "output_language" in resp.json()["detail"]


# ─── (a) explicit output_language="en" → job carries "en" ────────────────────

@pytest.mark.asyncio
async def test_generate_explicit_output_language_en_threaded():
    """Launch with output_language='en' → jobs.create and find_active_for_section
    must be called with output_language='en'.

    Removing the output_language kwarg from either call causes the assertion
    to fail.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    fake_job = _make_fake_job(output_language="en")
    # global default is "uz"; explicit "en" must win
    fake_ld = _make_fake_ld(output_language="uz")

    mock_jobs_create = AsyncMock(return_value=fake_job)
    mock_find_active = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(jobs_mod.jobs_repo, "queue_depth",
                         AsyncMock(return_value=0)),
            patch.object(jobs_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(jobs_mod.jobs_repo, "create", mock_jobs_create),
            patch.object(jobs_mod.jobs_repo, "get_with_phases",
                         AsyncMock(return_value=fake_job)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={"output_language": "en"},
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

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


# ─── (b) omitted output_language → inherits global default "ru" ──────────────

@pytest.mark.asyncio
async def test_generate_inherits_global_default_output_language():
    """Launch omitting output_language with global default='ru' and book
    source_language=None → job carries 'ru' (global default as final fallback).

    If resolve_output_language_for_book is not called (or its result not threaded in),
    jobs.create receives None or the wrong default, causing this assertion to fail.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    # Book with no source_language so global default is the only fallback
    fake_book_no_src = SimpleNamespace(
        id=BOOK_ID,
        status="toc_ready",
        subject="math-algebra",
        grade="8",
        original_filename=None,
        error_message=None,
        source_language=None,
    )
    fake_job = _make_fake_job(output_language="ru")
    fake_ld = _make_fake_ld(output_language="ru")  # global default is "ru"

    mock_jobs_create = AsyncMock(return_value=fake_job)
    mock_find_active = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=fake_book_no_src)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(jobs_mod.jobs_repo, "queue_depth",
                         AsyncMock(return_value=0)),
            patch.object(jobs_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(jobs_mod.jobs_repo, "create", mock_jobs_create),
            patch.object(jobs_mod.jobs_repo, "get_with_phases",
                         AsyncMock(return_value=fake_job)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={},  # output_language omitted → inherit global default
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

    # jobs.create must have received output_language="ru" (global default)
    _, kwargs = mock_jobs_create.call_args
    assert kwargs.get("output_language") == "ru", (
        f"jobs.create called with output_language={kwargs.get('output_language')!r},"
        " expected 'ru' (inherited from global default)"
    )


# ─── (d) language-scoped idempotency: UZ job NOT adopted for EN request ───────

@pytest.mark.asyncio
async def test_generate_language_scoped_dedup_uz_not_adopted_for_en():
    """An existing UZ active job must NOT be adopted for an EN request.

    The find_active_for_section mock returns None (simulating no EN-language
    active job — the language-scoped query skips the UZ one), so the handler
    must fall through to jobs.create and return 201 with a new job.

    If find_active_for_section did NOT receive output_language='en', it might
    return the UZ job and adopt it (returning 200 with the wrong language),
    causing the 201 assertion or the create-call assertion to fail.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    fake_job_en = _make_fake_job(output_language="en")
    fake_ld = _make_fake_ld(output_language="uz")  # global default uz; explicit en wins

    mock_find_active = AsyncMock(return_value=None)  # EN-scoped: no active EN job
    mock_jobs_create = AsyncMock(return_value=fake_job_en)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=_FAKE_BOOK)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(jobs_mod.jobs_repo, "queue_depth",
                         AsyncMock(return_value=0)),
            patch.object(jobs_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(jobs_mod.jobs_repo, "create", mock_jobs_create),
            patch.object(jobs_mod.jobs_repo, "get_with_phases",
                         AsyncMock(return_value=fake_job_en)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={"output_language": "en"},  # EN request
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    # Must create a new job (201), NOT adopt the UZ one (200)
    assert resp.status_code == 201, resp.text

    # A new job must have been created
    mock_jobs_create.assert_called_once()

    # find_active_for_section must have been called with output_language="en"
    _, kwargs = mock_find_active.call_args
    assert kwargs.get("output_language") == "en", (
        f"find_active_for_section called with output_language={kwargs.get('output_language')!r},"
        " expected 'en' — the language-scoped lookup must use the resolved language"
    )


# ─── (e) book source language wins over global default when no explicit pick ─────

@pytest.mark.asyncio
async def test_generate_book_source_language_wins_over_global_default():
    """Single-section launch from a RU-source book with no explicit output_language
    → job must carry 'ru', NOT the global default ('uz').

    Bite-prove: replacing resolve_output_language_for_book with the old 2-arg
    resolve_output_language (ignoring book.source_language) makes jobs.create
    receive 'uz' instead of 'ru', causing this assertion to fail.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    # RU-source book; global default is uz
    fake_book_ru = SimpleNamespace(
        id=BOOK_ID,
        status="toc_ready",
        subject="math-algebra",
        grade="8",
        original_filename=None,
        error_message=None,
        source_language="ru",
    )
    fake_job = _make_fake_job(output_language="ru")
    fake_ld = _make_fake_ld(output_language="uz")  # global default is uz

    mock_jobs_create = AsyncMock(return_value=fake_job)
    mock_find_active = AsyncMock(return_value=None)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=fake_book_ru)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "find_active_for_section",
                         mock_find_active),
            patch.object(jobs_mod.jobs_repo, "queue_depth",
                         AsyncMock(return_value=0)),
            patch.object(jobs_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(jobs_mod.jobs_repo, "create", mock_jobs_create),
            patch.object(jobs_mod.jobs_repo, "get_with_phases",
                         AsyncMock(return_value=fake_job)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={},  # no explicit output_language
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

    # jobs.create must have received output_language="ru" (from book.source_language)
    _, kwargs = mock_jobs_create.call_args
    assert kwargs.get("output_language") == "ru", (
        f"jobs.create called with output_language={kwargs.get('output_language')!r},"
        " expected 'ru' (book.source_language='ru' must win over global default 'uz')"
    )

    # find_active_for_section dedup must also use "ru"
    _, kwargs = mock_find_active.call_args
    assert kwargs.get("output_language") == "ru", (
        f"find_active_for_section called with output_language={kwargs.get('output_language')!r},"
        " expected 'ru' — language-scoped dedup must follow book source language"
    )


@pytest.mark.asyncio
async def test_generate_explicit_wins_over_book_source_language():
    """Explicit output_language pick must win over book.source_language.

    Book is RU-source, but operator explicitly picks 'en' → job carries 'en'.
    """
    import app.api.v1.jobs as jobs_mod
    from app.db import get_session

    fake_book_ru = SimpleNamespace(
        id=BOOK_ID,
        status="toc_ready",
        subject="math-algebra",
        grade="8",
        original_filename=None,
        error_message=None,
        source_language="ru",
    )
    fake_job = _make_fake_job(output_language="en")
    fake_ld = _make_fake_ld(output_language="uz")

    mock_jobs_create = AsyncMock(return_value=fake_job)

    app_obj = _app()
    app_obj.dependency_overrides[get_session] = _session_override()[1]
    try:
        with (
            patch.object(jobs_mod.books_repo, "get",
                         AsyncMock(return_value=fake_book_ru)),
            patch.object(jobs_mod.toc_repo, "get",
                         AsyncMock(return_value=_FAKE_SECTION)),
            patch.object(jobs_mod.jobs_repo, "lock_section_for_generate",
                         AsyncMock()),
            patch.object(jobs_mod.jobs_repo, "find_active_for_section",
                         AsyncMock(return_value=None)),
            patch.object(jobs_mod.jobs_repo, "queue_depth",
                         AsyncMock(return_value=0)),
            patch.object(jobs_mod.launch_defaults_repo, "get",
                         AsyncMock(return_value=fake_ld)),
            patch.object(jobs_mod.jobs_repo, "create", mock_jobs_create),
            patch.object(jobs_mod.jobs_repo, "get_with_phases",
                         AsyncMock(return_value=fake_job)),
        ):
            async with _client() as c:
                resp = await c.post(
                    f"/api/v1/books/{BOOK_ID}/sections/{SECTION_ID}/generate",
                    headers=_HDR,
                    json={"output_language": "en"},  # explicit en overrides ru source
                )
    finally:
        app_obj.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 201, resp.text

    _, kwargs = mock_jobs_create.call_args
    assert kwargs.get("output_language") == "en", (
        f"jobs.create called with output_language={kwargs.get('output_language')!r},"
        " expected 'en' (explicit pick must win over book.source_language='ru')"
    )
