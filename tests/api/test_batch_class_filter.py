import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

BOOK_ID = uuid.uuid4()

# Known-mix fixture: a page-containment header umbrella (rows 2-3 are its
# children), a keyword `other` (Javoblar), a keyword `test` (Nazorat ishi),
# and two plain lesson rows (row 3 is also page-contained by the header but
# that doesn't change the header's own classification, already met by 2
# children) plus one more.
_HEADER_ID = uuid.uuid4()
_LESSON1_ID = uuid.uuid4()
_LESSON2_ID = uuid.uuid4()
_OTHER_ID = uuid.uuid4()
_TEST_ID = uuid.uuid4()
_LESSON3_ID = uuid.uuid4()

_ROWS = [
    SimpleNamespace(id=_HEADER_ID, section_number="1", section_title="Bob 1",
                    page_start=1, page_end=40, order_index=0),
    SimpleNamespace(id=_LESSON1_ID, section_number="1.1", section_title="Kasrlar",
                    page_start=1, page_end=10, order_index=1),
    SimpleNamespace(id=_LESSON2_ID, section_number="1.2", section_title="Darajalar",
                    page_start=11, page_end=20, order_index=2),
    SimpleNamespace(id=_OTHER_ID, section_number="1.3", section_title="Javoblar",
                    page_start=41, page_end=41, order_index=3),
    SimpleNamespace(id=_TEST_ID, section_number="1.4", section_title="Nazorat ishi",
                    page_start=42, page_end=43, order_index=4),
    SimpleNamespace(id=_LESSON3_ID, section_number="1.5", section_title="Uchburchaklar",
                    page_start=21, page_end=30, order_index=5),
]
# classify_entries(_ROWS) == [header, lesson, lesson, other, test, lesson]


def _fake_book():
    return SimpleNamespace(
        id=BOOK_ID, status="toc_ready", subject="math-geometry", grade="8",
        source_language="uz", original_filename="g.pdf",
    )


def _fake_launch_defaults():
    return SimpleNamespace(
        judge_provider="claude", judge_model="claude-sonnet-4-6", judge_transport="inherit",
        extract_provider="gemini", extract_model="gemini-2.5-flash", extract_transport="inherit",
        solver_provider="claude", solver_model="claude-haiku-4-5-20251001", solver_transport="inherit",
        output_language="uz",
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
        return None

    async def _fake_latest_for_section(session, book_id, toc_entry_id, *, transport=None,
                                        output_language):
        return None

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_get_book)
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _fake_list_for_book)
    monkeypatch.setattr(batch_mod.launch_defaults_repo, "get", _fake_get_launch_defaults)
    monkeypatch.setattr(batch_mod.jobs_repo, "find_active_for_section",
                        _fake_find_active_for_section)
    monkeypatch.setattr(batch_mod.jobs_repo, "latest_for_section", _fake_latest_for_section)


async def _post_preview(payload):
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(
            "/api/v1/jobs/batch",
            headers={"Authorization": "Bearer 123"},
            json=payload,
        )


@pytest.mark.asyncio
async def test_default_preview_targets_lesson_only(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post_preview({"book_id": str(BOOK_ID), "preview": True})

    assert resp.status_code == 200
    data = resp.json()
    assert data["target_count"] == 3
    assert data["new"] == 3
    assert data["excluded_by_class"] == {"header": 1, "other": 1, "test": 1}


@pytest.mark.asyncio
async def test_include_classes_widens_targets(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post_preview({
        "book_id": str(BOOK_ID), "preview": True,
        "include_classes": ["lesson", "test"],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["target_count"] == 4
    assert data["new"] == 4
    assert data["excluded_by_class"] == {"header": 1, "other": 1}


@pytest.mark.asyncio
async def test_explicit_toc_entry_ids_bypasses_filter(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post_preview({
        "book_id": str(BOOK_ID), "preview": True,
        "toc_entry_ids": [str(_HEADER_ID)],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["target_count"] == 1
    assert data["new"] == 1
    assert data["excluded_by_class"] == {}


@pytest.mark.asyncio
async def test_unknown_include_class_is_422(monkeypatch):
    from app.api.v1 import batch as batch_mod
    _apply_common_monkeypatches(monkeypatch, batch_mod)

    resp = await _post_preview({
        "book_id": str(BOOK_ID), "preview": True,
        "include_classes": ["banana"],
    })

    assert resp.status_code == 422
