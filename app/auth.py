"""Token-based auth.

Two acceptance modes per request:
  1. `Authorization: Bearer <token>` header — used by REST calls
  2. `?token=<token>` query parameter — used by SSE streams (EventSource
     can't set custom headers in the browser, so the token rides on the URL)

Tokens are validated against `settings.auth_token` (comma-separated). When
the setting is empty, auth is disabled (dev/local mode) — any request is
accepted and `user_id="anonymous"` is returned.

In production, the upstream service either injects the header (REST) or
sets a cookie / appends the query param (SSE). The frontend's manual login
flow pastes the token into sessionStorage and attaches it to every call.
"""

from typing import Optional

from fastapi import Header, HTTPException, Query, status

from app.config import valid_auth_tokens


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, include_in_schema=False),
) -> dict:
    valid = valid_auth_tokens()
    if not valid:
        # Auth disabled (no AUTH_TOKEN configured). Local/dev convenience.
        return {"user_id": "anonymous", "auth": "disabled"}

    provided: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(None, 1)[1].strip()
    elif token:
        provided = token.strip()

    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    if provided not in valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    return {"user_id": "authenticated", "auth": "token"}
