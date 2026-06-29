"""Real-DB: GET/PUT /settings/launch-defaults — output_language field.

Covers:
  (a) GET exposes output_language (seeded 'uz' by migration 0037/0038).
  (b) PUT {"output_language": "en"} round-trips; other fields stay unchanged.
  (c) PUT {"output_language": "fr"} → 422 (invalid language code).
  (d) PUT omitting output_language leaves it unchanged (merge semantics).

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_exposes_output_language():
    """(a) GET /settings/launch-defaults returns output_language field (seeded 'uz')."""
    async with _client() as c:
        r = await c.get("/api/v1/settings/launch-defaults", headers=_HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "output_language" in body, "output_language field missing from response"
    assert body["output_language"] == "uz"


@pytest.mark.asyncio
async def test_put_output_language_round_trips():
    """(b) PUT {"output_language": "en"} persists; other fields stay unchanged."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"output_language": "en"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_language"] == "en"
    # Merge semantics: other fields must remain at their seeded values.
    assert body["judge_provider"] == "gemini"
    assert body["judge_model"] == "gemini-2.5-flash"
    assert body["extract_provider"] == "gemini"
    assert body["extract_model"] == "gemini-2.5-flash"

    # Restore singleton so other tests aren't poisoned.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(s, {"output_language": "uz"})
        await s.commit()


@pytest.mark.asyncio
async def test_put_invalid_language_returns_422():
    """(c) PUT {"output_language": "fr"} → 422 (not in uz/en/ru)."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"output_language": "fr"},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_omitting_output_language_leaves_unchanged():
    """(d) PUT without output_language key leaves the field unchanged (merge semantics)."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    # First set it to 'ru' via direct repo call.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(s, {"output_language": "ru"})
        await s.commit()

    # PUT without output_language — merge semantics must leave it at 'ru'.
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"judge_provider": "gemini", "judge_model": "gemini-2.5-flash"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_language"] == "ru", (
        f"expected 'ru' (unchanged), got {body['output_language']!r}"
    )

    # Restore.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(s, {"output_language": "uz"})
        await s.commit()
