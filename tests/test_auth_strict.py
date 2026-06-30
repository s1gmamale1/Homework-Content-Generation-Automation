import pytest
from fastapi import HTTPException
import app.auth as auth


@pytest.mark.asyncio
async def test_strict_requires_header_and_refuses_when_open(monkeypatch):
    # auth disabled -> refuse (a key vault must never be open)
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: set())
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization="Bearer anything")
    assert e.value.status_code == 503

    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"secret"})
    # valid header passes
    assert (await auth.get_current_user_strict(authorization="Bearer secret"))["auth"] == "token"
    # missing header -> 401
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization=None)
    assert e.value.status_code == 401
    # wrong token -> 401
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization="Bearer nope")
    assert e.value.status_code == 401
