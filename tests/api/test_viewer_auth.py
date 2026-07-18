"""Tests for the read-only dashboard-viewer auth dependency (get_viewer_user).

Viewer auth is deliberately SEPARATE from operator auth (AUTH_TOKEN /
get_current_user*): a valid operator token must be rejected by the viewer
dependency, and vice versa. It is also header-only — unlike get_current_user,
there is no `?token=` query-param fallback (the viewer has no SSE/download
need, and query tokens leak into logs), so a valid viewer token passed only
via the query string must be rejected too.
"""

import pytest
from fastapi import HTTPException

import app.auth as auth


@pytest.mark.asyncio
async def test_viewer_token_accepted(monkeypatch):
    monkeypatch.setattr(auth, "valid_dashboard_tokens", lambda: {"viewer-secret"})
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    result = await auth.get_viewer_user(authorization="Bearer viewer-secret")

    assert result["auth"] == "token"


@pytest.mark.asyncio
async def test_operator_token_rejected_even_though_valid_on_operator_app(monkeypatch):
    monkeypatch.setattr(auth, "valid_dashboard_tokens", lambda: {"viewer-secret"})
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    with pytest.raises(HTTPException) as exc:
        await auth.get_viewer_user(authorization="Bearer operator-secret")

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_header_rejected(monkeypatch):
    monkeypatch.setattr(auth, "valid_dashboard_tokens", lambda: {"viewer-secret"})
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    with pytest.raises(HTTPException) as exc:
        await auth.get_viewer_user(authorization=None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_header_rejected(monkeypatch):
    monkeypatch.setattr(auth, "valid_dashboard_tokens", lambda: {"viewer-secret"})
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    with pytest.raises(HTTPException) as exc:
        await auth.get_viewer_user(authorization="viewer-secret")  # no "Bearer " prefix

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_multiple_comma_separated_dashboard_tokens_all_accepted(monkeypatch):
    monkeypatch.setattr(
        auth, "valid_dashboard_tokens", lambda: {"viewer-a", "viewer-b", "viewer-c"}
    )
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    for tok in ("viewer-a", "viewer-b", "viewer-c"):
        result = await auth.get_viewer_user(authorization=f"Bearer {tok}")
        assert result["auth"] == "token"


@pytest.mark.asyncio
async def test_query_param_token_without_header_rejected(monkeypatch):
    """Header-only proof: a valid viewer token riding on ?token= with no
    Authorization header must NOT authenticate — get_viewer_user takes no
    `token` query param at all, unlike get_current_user."""
    monkeypatch.setattr(auth, "valid_dashboard_tokens", lambda: {"viewer-secret"})
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"operator-secret"})

    assert "token" not in auth.get_viewer_user.__annotations__

    with pytest.raises(HTTPException) as exc:
        await auth.get_viewer_user(authorization=None)

    assert exc.value.status_code == 401
