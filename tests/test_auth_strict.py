import pytest
from fastapi import HTTPException

import app.auth as auth


STRONG_A = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"
STRONG_B = "mD8vQ2kL7xN4pR1sT6wY9cA3fH5jU0zE-BgC"


@pytest.mark.asyncio
async def test_general_auth_empty_requires_explicit_local_mode(monkeypatch):
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: set())
    monkeypatch.setattr(auth.settings, "auth_token", "")
    monkeypatch.setattr(auth.settings, "allow_insecure_local_auth", False)
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user(authorization=None, token=None)
    assert caught.value.status_code == 503

    monkeypatch.setattr(auth.settings, "allow_insecure_local_auth", True)
    user = await auth.get_current_user(authorization=None, token=None)
    assert user == {"user_id": "anonymous", "auth": "disabled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [" ", ",", "  ,  "])
async def test_local_mode_never_opens_a_malformed_empty_token_list(
    monkeypatch, raw
):
    monkeypatch.setattr(auth.settings, "auth_token", raw)
    monkeypatch.setattr(auth.settings, "allow_insecure_local_auth", True)
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: set())

    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user(authorization=None, token=None)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_strict_requires_header_and_refuses_when_open(monkeypatch):
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: set())
    monkeypatch.setattr(auth.settings, "allow_insecure_local_auth", True)
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user_strict(authorization="Bearer anything")
    assert caught.value.status_code == 503

    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"secret"})
    assert (
        await auth.get_current_user_strict(authorization="Bearer secret")
    )["auth"] == "token"
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user_strict(authorization=None)
    assert caught.value.status_code == 401
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user_strict(authorization="Bearer nope")
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_general_auth_checks_every_candidate_in_constant_time(
    monkeypatch,
):
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {STRONG_B, STRONG_A})
    real = auth.constant_time_token_match
    calls = []

    def tracked(provided, candidates):
        calls.append((provided, tuple(candidates)))
        return real(provided, candidates)

    monkeypatch.setattr(auth, "constant_time_token_match", tracked)
    result = await auth.get_current_user(
        authorization=f"Bearer {STRONG_A}", token=None
    )
    assert result["auth"] == "token"
    assert calls == [(STRONG_A, tuple(sorted((STRONG_A, STRONG_B))))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "token"),
    [
        (f"Bearer  {STRONG_A}", None),
        (f"Bearer {STRONG_A} ", None),
        (None, f" {STRONG_A}"),
        (None, f"{STRONG_A} "),
        ("Bearer токен", None),
    ],
)
async def test_presented_header_and_query_values_are_exact_safe_misses(
    monkeypatch, authorization, token
):
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {STRONG_A})
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user(authorization=authorization, token=token)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_diagnostics_never_disclose_token_values(monkeypatch):
    configured = STRONG_A
    presented = "WRONG_" + STRONG_B
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {configured})
    with pytest.raises(HTTPException) as caught:
        await auth.get_current_user(
            authorization=f"Bearer {presented}", token=None
        )
    detail = str(caught.value.detail)
    assert configured not in detail
    assert presented not in detail
