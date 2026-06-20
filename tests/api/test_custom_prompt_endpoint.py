from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

_HDR = {"Authorization": "Bearer 123"}
_BOOK_ID = "00000000-0000-0000-0000-000000000001"
_SECTION_ID = "00000000-0000-0000-0000-000000000002"


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _ready_book_patch(monkeypatch):
    from app.api.v1 import jobs as jobs_mod

    class _Book:
        status = "toc_ready"
        subject = "math-algebra"

        def __init__(self, book_id):
            self.id = book_id

    class _Section:
        def __init__(self, book_id, sid):
            self.id = sid
            self.book_id = book_id

    async def _fake_book(session, book_id):
        return _Book(book_id)

    async def _fake_toc(session, toc_entry_id):
        return _Section(UUID(_BOOK_ID), toc_entry_id)

    monkeypatch.setattr(jobs_mod.books_repo, "get", _fake_book)
    monkeypatch.setattr(jobs_mod.toc_repo, "get", _fake_toc)


def _post(c, body):
    return c.post(f"/api/v1/books/{_BOOK_ID}/sections/{_SECTION_ID}/generate",
                  headers=_HDR, json={"provider": "claude", **body})


@pytest.mark.asyncio
async def test_unknown_phase_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"selected_phases": ["not-a-phase"]})
    assert r.status_code == 400, r.text
    assert "phase" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_empty_phases_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"selected_phases": []})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_oversize_custom_prompt_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"custom_prompts": {"flashcards": "x" * 20_001}})
    assert r.status_code == 400, r.text
    assert "flashcards" in r.json()["detail"]


@pytest.mark.asyncio
async def test_custom_prompt_for_extract_rejected(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"custom_prompts": {"extract": "no"}})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_picked_phase_without_prompt_rejected(monkeypatch):
    # Pick-phases requires an uploaded md for every picked phase.
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {"selected_phases": ["flashcards"]})
    assert r.status_code == 400, r.text
    assert "missing a custom prompt" in r.json()["detail"]
    assert "flashcards" in r.json()["detail"]


@pytest.mark.asyncio
async def test_partial_prompts_rejects_only_the_uncovered_phase(monkeypatch):
    _ready_book_patch(monkeypatch)
    async with _client() as c:
        r = await _post(c, {
            "selected_phases": ["flashcards", "boss-arena"],
            "custom_prompts": {"flashcards": "md"},
        })
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "boss-arena" in detail and "flashcards" not in detail
