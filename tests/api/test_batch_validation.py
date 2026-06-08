import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_batch_rejects_non_toc_ready(monkeypatch):
    from main import app
    from app.api.v1 import batch as batch_mod

    class _Book:
        status = "toc_extracting"
        error_message = None
        subject = "math-algebra"
        grade = None

    async def _fake_get(session, book_id):
        return _Book()

    monkeypatch.setattr(batch_mod.books_repo, "get", _fake_get)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/jobs/batch",
            headers={"Authorization": "Bearer 123"},
            json={"book_id": "00000000-0000-0000-0000-000000000001"},
        )
    assert resp.status_code == 409
    assert "extract" in resp.json()["detail"].lower()
