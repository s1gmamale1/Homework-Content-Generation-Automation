"""Real-DB: GET/PUT /settings/launch-defaults — returns singleton, validates manifest.

Covers:
  (a) GET returns seeded defaults (gemini/gemini-2.5-flash/inherit/cli).
  (b) PUT partial update persists, untouched fields unchanged.
  (c) PUT rejects off-manifest model → 422.
  (d) PUT rejects bad transport → 422.

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
async def test_get_returns_seeded_defaults():
    """(a) GET /settings/launch-defaults returns the seeded row."""
    async with _client() as c:
        r = await c.get("/api/v1/settings/launch-defaults", headers=_HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["judge_provider"] == "gemini"
    assert body["judge_model"] == "gemini-2.5-flash"
    assert body["judge_transport"] == "inherit"
    assert body["extract_provider"] == "gemini"
    assert body["extract_model"] == "gemini-2.5-flash"
    assert body["extract_transport"] == "inherit"
    assert body["toc_transport"] == "cli"


@pytest.mark.asyncio
async def test_put_partial_update():
    """(b) PUT partial update persists; untouched fields stay at seeded value."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"judge_provider": "claude", "judge_model": "claude-opus-4-7"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["judge_provider"] == "claude"
    assert body["judge_model"] == "claude-opus-4-7"
    # untouched fields remain at seeded default
    assert body["extract_provider"] == "gemini"
    assert body["extract_model"] == "gemini-2.5-flash"

    # Restore singleton so other tests aren't poisoned.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(
            s, {"judge_provider": "gemini", "judge_model": "gemini-2.5-flash"}
        )
        await s.commit()


@pytest.mark.asyncio
async def test_put_rejects_off_manifest():
    """(c) PUT with off-manifest model returns 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"judge_provider": "claude", "judge_model": "not-a-model"},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_bad_transport():
    """(d) PUT with an invalid toc_transport returns 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"toc_transport": "bogus"},
        )
    assert r.status_code == 422, r.text
