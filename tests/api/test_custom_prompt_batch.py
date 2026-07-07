import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"Authorization": "Bearer 123"}
_BOOK_ID = "00000000-0000-0000-0000-000000000001"


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _ready_batch_patch(monkeypatch):
    from app.api.v1 import batch as batch_mod

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"
        grade = None
        error_message = None

    class _TOC:
        def __init__(self, i):
            self.id = i
            self.section_title = f"L{i}"
            self.order_index = i
            self.page_start = None
            self.page_end = None

    async def _fake_book(session, book_id):
        return _Book()

    async def _fake_list(session, book_id):
        return [_TOC(0), _TOC(1)]

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _fake_list)


@pytest.mark.asyncio
async def test_batch_unknown_phase_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                         json={"book_id": _BOOK_ID, "selected_phases": ["not-a-phase"]})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_batch_oversize_custom_rejected(monkeypatch):
    _ready_batch_patch(monkeypatch)
    async with _client() as c:
        r = await c.post("/api/v1/jobs/batch", headers=_HDR,
                         json={"book_id": _BOOK_ID,
                               "custom_prompts": {"flashcards": "x" * 20_001}})
    assert r.status_code == 400, r.text
    assert "flashcards" in r.json()["detail"]
