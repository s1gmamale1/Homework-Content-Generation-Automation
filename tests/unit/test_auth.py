"""Tests for app/auth.py — get_current_user dependency."""
import pytest
from unittest.mock import patch

from fastapi import HTTPException

from app.auth import get_current_user


async def _call(authorization=None, token=None, valid_tokens=None):
    """Helper to call get_current_user directly, patching valid_auth_tokens."""
    tokens = valid_tokens if valid_tokens is not None else {"test-token"}
    with patch("app.auth.valid_auth_tokens", return_value=tokens):
        return await get_current_user(authorization=authorization, token=token)


# ─── auth disabled (no tokens configured) ────────────────────────────────────

class TestAuthDisabled:
    @pytest.mark.asyncio
    async def test_empty_token_set_allows_any_request(self):
        result = await _call(valid_tokens=set())
        assert result["auth"] == "disabled"
        assert result["user_id"] == "anonymous"

    @pytest.mark.asyncio
    async def test_disabled_does_not_require_header(self):
        result = await _call(authorization=None, token=None, valid_tokens=set())
        assert result["user_id"] == "anonymous"


# ─── bearer header ───────────────────────────────────────────────────────────

class TestBearerHeader:
    @pytest.mark.asyncio
    async def test_valid_bearer_token_accepted(self):
        result = await _call(authorization="Bearer test-token")
        assert result["user_id"] == "authenticated"
        assert result["auth"] == "token"

    @pytest.mark.asyncio
    async def test_invalid_bearer_token_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            await _call(authorization="Bearer wrong-token")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_prefix_case_insensitive(self):
        result = await _call(authorization="BEARER test-token")
        assert result["user_id"] == "authenticated"

    @pytest.mark.asyncio
    async def test_bearer_with_extra_whitespace(self):
        result = await _call(authorization="Bearer  test-token ")
        assert result["user_id"] == "authenticated"

    @pytest.mark.asyncio
    async def test_non_bearer_scheme_treated_as_missing(self):
        with pytest.raises(HTTPException) as exc:
            await _call(authorization="Basic dGVzdA==")
        assert exc.value.status_code == 401


# ─── query parameter ─────────────────────────────────────────────────────────

class TestQueryToken:
    @pytest.mark.asyncio
    async def test_valid_query_param_accepted(self):
        result = await _call(token="test-token")
        assert result["user_id"] == "authenticated"

    @pytest.mark.asyncio
    async def test_invalid_query_param_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            await _call(token="bad-token")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_query_param_with_whitespace_stripped(self):
        result = await _call(token="  test-token  ")
        assert result["user_id"] == "authenticated"


# ─── missing token ────────────────────────────────────────────────────────────

class TestMissingToken:
    @pytest.mark.asyncio
    async def test_no_header_no_param_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            await _call(authorization=None, token=None)
        assert exc.value.status_code == 401
        assert "missing" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_401_includes_www_authenticate_header(self):
        with pytest.raises(HTTPException) as exc:
            await _call()
        assert "WWW-Authenticate" in exc.value.headers


# ─── multiple valid tokens ────────────────────────────────────────────────────

class TestMultipleValidTokens:
    @pytest.mark.asyncio
    async def test_second_token_also_accepted(self):
        result = await _call(
            authorization="Bearer token-b",
            valid_tokens={"token-a", "token-b"},
        )
        assert result["user_id"] == "authenticated"

    @pytest.mark.asyncio
    async def test_unknown_token_rejected_when_multiple_configured(self):
        with pytest.raises(HTTPException):
            await _call(
                authorization="Bearer token-c",
                valid_tokens={"token-a", "token-b"},
            )
