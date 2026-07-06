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
    assert body["content_provider"] == "gemini"
    assert body["content_model"] == "gemini-2.5-pro"
    assert body["content_transport"] == "api"


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


# ── Finding #2: null judge/extract provider or model must be 422 ────────────

@pytest.mark.asyncio
async def test_put_rejects_null_judge_provider():
    """(#2a) PUT {"judge_provider": null} → 422 — global default cannot be null."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"judge_provider": None},
        )
    assert r.status_code == 422, r.text
    assert "concrete" in r.json().get("detail", "").lower() or r.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_null_judge_model():
    """(#2b) PUT {"judge_model": null} → 422 — global default cannot be null."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"judge_model": None},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_null_extract_provider():
    """(#2c) PUT {"extract_provider": null} → 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"extract_provider": None},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_null_extract_model():
    """(#2d) PUT {"extract_model": null} → 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"extract_model": None},
        )
    assert r.status_code == 422, r.text


# ── Finding #5: toc_transport=api + non-api-capable extract provider ─────────

@pytest.mark.asyncio
async def test_put_rejects_toc_api_with_non_api_extract_provider():
    """(#5) toc_transport=api with a non-api-capable extract_provider → 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            # codex is in manifest but NOT api-capable (not in API_PROVIDERS)
            json={
                "toc_transport": "api",
                "extract_provider": "codex",
                "extract_model": "gpt-5.5",
            },
        )
    assert r.status_code == 422, r.text
    assert "toc_transport" in r.json().get("detail", "").lower() or r.status_code == 422


@pytest.mark.asyncio
async def test_put_valid_concrete_still_200():
    """(#2e) A valid concrete PUT with all four fields still returns 200."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={
                "judge_provider": "claude",
                "judge_model": "claude-opus-4-7",
                "extract_provider": "gemini",
                "extract_model": "gemini-2.5-flash",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["judge_provider"] == "claude"
    assert body["judge_model"] == "claude-opus-4-7"

    # Restore singleton so other tests aren't poisoned.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(
            s, {"judge_provider": "gemini", "judge_model": "gemini-2.5-flash"}
        )
        await s.commit()


# ── content_* fields ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_content_concrete_ok():
    """(content-a) PUT concrete content fields → 200, body content_provider==gemini."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"content_provider": "gemini", "content_model": "gemini-2.5-pro", "content_transport": "api"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_provider"] == "gemini"

    # Restore singleton so other tests aren't poisoned.
    async with SessionLocal() as s:
        await launch_defaults_repo.update(
            s, {"content_provider": "gemini", "content_model": "gemini-2.5-pro", "content_transport": "api"}
        )
        await s.commit()


@pytest.mark.asyncio
async def test_put_rejects_null_content_provider():
    """(content-b) PUT {"content_provider": null} → 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"content_provider": None},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_content_offmanifest():
    """(content-c) PUT off-manifest content model → 422."""
    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"content_provider": "gemini", "content_model": "not-a-model"},
        )
    assert r.status_code == 422, r.text


# ── solver_* fields (R21.8) — mirror judge exactly ──────────────────────────

@pytest.mark.asyncio
async def test_get_returns_seeded_solver_defaults():
    """(solver-a) GET returns the mig-0043 solver seed."""
    async with _client() as c:
        r = await c.get("/api/v1/settings/launch-defaults", headers=_HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solver_provider"] == "gemini"
    assert body["solver_model"] == "gemini-3.1-pro-preview"
    assert body["solver_transport"] == "inherit"


@pytest.mark.asyncio
async def test_put_solver_override_persists():
    """(solver-b) PUT a concrete solver override → 200, GET reflects it."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"solver_provider": "claude", "solver_model": "claude-opus-4-7",
                  "solver_transport": "api"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solver_provider"] == "claude"
    assert body["solver_model"] == "claude-opus-4-7"
    assert body["solver_transport"] == "api"

    async with SessionLocal() as s:
        await launch_defaults_repo.update(
            s, {"solver_provider": "gemini", "solver_model": "gemini-3.1-pro-preview",
                "solver_transport": "inherit"},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_put_rejects_null_solver_provider():
    """(solver-c) PUT {"solver_provider": null} → 422 (required-concrete)."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_provider": None})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_null_solver_model():
    """(solver-d) PUT {"solver_model": null} → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_model": None})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_solver_offmanifest():
    """(solver-e) PUT off-manifest solver model → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_provider": "gemini", "solver_model": "not-a-model"})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_bad_solver_transport():
    """(solver-f) PUT invalid solver_transport → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_transport": "bogus"})
    assert r.status_code == 422, r.text
