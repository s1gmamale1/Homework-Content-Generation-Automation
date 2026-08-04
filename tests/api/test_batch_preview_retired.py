"""Batch preview must report a saved section whose latest failed/cancelled job
is pinned to a retired model (gemini-2.5, retired 2026-08-03) as a DISJOINT
`retired` count — never folded into `resumable` (that would imply the launch
could safely reuse its saved phases, which it can't: resuming reuses the
job's pinned provider/model verbatim and would call a dead model).

The real (non-preview) launch's relaunch-RESUME path must refuse — 409, never
silently recreate or silently resume — when it would otherwise adopt a
retired-stamped saved section. `relaunch_mode="discard"` is the sanctioned
escape hatch (not exercised for the happy path here; that's the existing
create-fresh behavior, untouched).

Follows the mocking convention in tests/api/test_batch_class_filter.py:
ASGITransport + monkeypatched repo calls (books_repo.lock_book_shared is
already no-op'd fleet-wide by tests/api/conftest.py's autouse fixture).
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

BOOK_ID = uuid.uuid4()
_LESSON1_ID = uuid.uuid4()  # will resolve to a LIVE-model saved failed job
_LESSON2_ID = uuid.uuid4()  # will resolve to a RETIRED-model saved failed job
_LESSON3_ID = uuid.uuid4()  # brand new (no prior job at all)

_ROWS = [
    SimpleNamespace(id=_LESSON1_ID, section_number="1.1", section_title="Kasrlar",
                    page_start=1, page_end=10, order_index=0),
    SimpleNamespace(id=_LESSON2_ID, section_number="1.2", section_title="Darajalar",
                    page_start=11, page_end=20, order_index=1),
    SimpleNamespace(id=_LESSON3_ID, section_number="1.3", section_title="Uchburchaklar",
                    page_start=21, page_end=30, order_index=2),
]


def _fake_book():
    return SimpleNamespace(
        id=BOOK_ID, status="toc_ready", subject="math-geometry", grade="8",
        source_language="uz", original_filename="g.pdf",
    )


def _fake_launch_defaults():
    return SimpleNamespace(
        judge_provider="claude", judge_model="claude-sonnet-4-6", judge_transport="inherit",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite", extract_transport="api",
        solver_provider="claude", solver_model="claude-haiku-4-5-20251001", solver_transport="inherit",
        output_language="uz",
    )


def _live_failed_job(jid):
    return SimpleNamespace(
        id=jid, status="failed",
        provider="claude", model="claude-sonnet-4-6",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        batch_id=None,
    )


def _retired_failed_job(jid):
    return SimpleNamespace(
        id=jid, status="failed",
        provider="gemini", model="gemini-2.5-flash",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        batch_id=None,
    )


def _apply_common_monkeypatches(monkeypatch, batch_mod):
    async def _fake_get_book(session, book_id):
        return _fake_book()

    async def _fake_list_for_book(session, book_id):
        return list(_ROWS)

    async def _fake_get_launch_defaults(session):
        return _fake_launch_defaults()

    async def _fake_find_active_for_section(session, book_id, toc_entry_id, *, transport=None,
                                             output_language):
        return None  # nothing pending/running/done — every target is "remaining"

    async def _fake_latest_for_section(session, book_id, toc_entry_id, *, transport=None,
                                        output_language):
        if toc_entry_id == _LESSON1_ID:
            return _live_failed_job(uuid.uuid4())
        if toc_entry_id == _LESSON2_ID:
            return _retired_failed_job(uuid.uuid4())
        return None  # _LESSON3_ID — brand new

    async def _fake_done_phase_count_for_job(session, job_id):
        # Only consulted for the (non-retired) live job's saved-phase count.
        return 2

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_get_book)
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _fake_list_for_book)
    monkeypatch.setattr(batch_mod.launch_defaults_repo, "get", _fake_get_launch_defaults)
    monkeypatch.setattr(batch_mod.jobs_repo, "find_active_for_section",
                        _fake_find_active_for_section)
    monkeypatch.setattr(batch_mod.jobs_repo, "latest_for_section", _fake_latest_for_section)
    monkeypatch.setattr(batch_mod.jobs_repo, "done_phase_count_for_job",
                        _fake_done_phase_count_for_job)


async def _post(payload):
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(
            "/api/v1/jobs/batch",
            headers={"Authorization": "Bearer 123"},
            json=payload,
        )


@pytest.mark.asyncio
async def test_preview_counts_retired_disjoint_from_resumable(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post({
        "book_id": str(BOOK_ID), "preview": True,
        "toc_entry_ids": [str(_LESSON1_ID), str(_LESSON2_ID), str(_LESSON3_ID)],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["resumable"] == 1     # only the live-model saved job
    assert data["retired"] == 1       # the gemini-2.5-stamped saved job — NOT resumable
    assert data["new"] == 1           # the brand-new lesson
    assert data["empty"] == 0


@pytest.mark.asyncio
async def test_preview_all_retired_none_resumable(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post({
        "book_id": str(BOOK_ID), "preview": True,
        "toc_entry_ids": [str(_LESSON2_ID)],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["retired"] == 1
    assert data["resumable"] == 0


@pytest.mark.asyncio
async def test_relaunch_resume_of_retired_section_returns_409(monkeypatch):
    """A real (non-preview) launch whose relaunch_mode defaults to "resume"
    must refuse — 409 — rather than silently reset_for_retry a retired-stamped
    saved section (which would call a dead model) or silently fall through to
    a fresh create (which would mask the operator's need to explicitly discard)."""
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    fake_batch = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(batch_mod.batches_repo, "get_or_create_for_book",
                        AsyncMock(return_value=fake_batch))
    monkeypatch.setattr(batch_mod.jobs_repo, "lock_section_for_generate", AsyncMock())
    reset_mock = AsyncMock()
    create_mock = AsyncMock()
    monkeypatch.setattr(batch_mod.jobs_repo, "reset_for_retry", reset_mock)
    monkeypatch.setattr(batch_mod.jobs_repo, "create", create_mock)

    resp = await _post({
        "book_id": str(BOOK_ID),
        "toc_entry_ids": [str(_LESSON2_ID)],
    })

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "retired_model"
    reset_mock.assert_not_awaited()
    create_mock.assert_not_awaited()
