"""GET /api/v1/books/{id}/source.pdf — the head serves raw PDF bytes so a
remote worker can pull-on-demand (R13). File-presence only; no DB lookup."""
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.services import storage

_HDR = {"Authorization": "Bearer 123"}


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_serves_pdf_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "auth_token", "123")  # hermetic: don't lean on .env
    bid = uuid4()
    p = storage.book_pdf_path(bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 hello")
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{bid}/source.pdf", headers=_HDR)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 hello"
    assert r.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_404_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "auth_token", "123")  # hermetic: don't lean on .env
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{uuid4()}/source.pdf", headers=_HDR)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_401_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "auth_token", "123")  # auth ENABLED -> missing header is 401
    async with _client() as c:
        r = await c.get(f"/api/v1/books/{uuid4()}/source.pdf")
    assert r.status_code == 401
