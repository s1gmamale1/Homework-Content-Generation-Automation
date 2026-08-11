"""Token-based auth.

Two acceptance modes per request:
  1. `Authorization: Bearer <token>` header — used by REST calls
  2. `?token=<token>` query parameter — used by SSE streams (EventSource
     can't set custom headers in the browser, so the token rides on the URL)

Tokens are validated against `settings.auth_token` (comma-separated). Empty
auth is accepted only when explicit insecure local-development mode is on.

In production, the upstream service either injects the header (REST) or
sets a cookie / appends the query param (SSE). The frontend's manual login
flow pastes the token into sessionStorage and attaches it to every call.
"""

from typing import Annotated, Optional

from fastapi import Header, HTTPException, Query, status

from app.config import settings, valid_auth_tokens, valid_dashboard_tokens
from app.services.operator_auth import constant_time_token_match


def _presented_value(value: str | None) -> str | None:
    if not value or any(character.isspace() for character in value):
        return None
    return value


def _bearer_value(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not value:
        return None
    return _presented_value(value)


def _query_value(token: str | None) -> str | None:
    return _presented_value(token)


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, include_in_schema=False),
) -> dict:
    valid = valid_auth_tokens()
    if not valid:
        if (
            settings.allow_insecure_local_auth
            and settings.auth_token == ""
        ):
            return {"user_id": "anonymous", "auth": "disabled"}
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="operator auth is unavailable",
        )

    # Fall through to the query token when the header is absent OR carries no
    # parseable Bearer credential. SSE/download clients can only present
    # `?token=`, and an upstream proxy may stamp a blank or non-Bearer
    # `Authorization` onto those browser-originated requests; such a header is
    # not a presented credential and must not suppress the query token.
    # A well-formed but WRONG Bearer is truthy here, so it short-circuits and
    # still 401s — the fallback never becomes credential shopping.
    provided = _bearer_value(authorization) or _query_value(token)

    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    if not constant_time_token_match(provided, sorted(valid)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    return {"user_id": "authenticated", "auth": "token"}


async def get_current_user_strict(
    authorization: Annotated[Optional[str], Header()] = None,
    token: Annotated[
        Optional[str], Query(include_in_schema=False)
    ] = None,
) -> dict:
    """Header-only auth for every service-account-key vault route."""
    valid = valid_auth_tokens()
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SA-key vault auth is unavailable",
        )
    if token is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="query auth is not accepted",
        )
    provided = _bearer_value(authorization)
    if not provided or not constant_time_token_match(provided, sorted(valid)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    return {"user_id": "authenticated", "auth": "token"}


async def get_viewer_user(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Header-only auth for the read-only dashboard-viewer port.

    Deliberately separate from operator auth: validates ONLY against
    `valid_dashboard_tokens()` (DASHBOARD_TOKEN), never `valid_auth_tokens()`
    (AUTH_TOKEN) — a valid operator token must be rejected here, and a valid
    viewer token must be rejected by the operator app. Like
    `get_current_user_strict`, this rejects the `?token=` query param (the
    viewer has no SSE/download need, and query tokens leak into logs) and
    refuses entirely (401) when DASHBOARD_TOKEN is unconfigured — a viewer
    port must never be served wide-open.
    """
    valid = valid_dashboard_tokens()
    provided: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(None, 1)[1].strip()
    if not valid or not provided or provided not in valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid dashboard token",
            headers={"WWW-Authenticate": 'Bearer realm="dashboard"'},
        )
    return {"user_id": "viewer", "auth": "token"}
