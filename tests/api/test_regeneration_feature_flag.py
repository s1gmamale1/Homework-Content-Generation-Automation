"""The two flags, and the order the gates run in.

Design §15.4 requires the flag-off state to hide the UI **and** reject every
mutation endpoint, so a stale UI cannot mutate a hidden feature. Two properties
have to hold together:

* with ``REGENERATION_ENABLED=false`` every route — read and write — is a 404,
  not a 403 and not a 500;
* an anonymous request fails AUTHENTICATION first, whatever the flag says, so
  the flag never becomes a way to probe which routes exist.

``REGENERATION_PUBLISHER_ENABLED`` is the second, independent flag: only the
two routes that hand work to the publication loop require it, and they refuse
with a structured 409 that says automatic publication is unavailable — not a
404, because the campaign genuinely exists and the operator may still generate,
reject, cancel and abandon.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.auth as auth_module
from app.api.v1 import regeneration as regen_api
from app.auth import get_current_user
from app.config import settings
from app.db import get_session
from main import app

client = TestClient(app)

BASE = "/api/v1/regeneration"
SUBJECT = "math-algebra"
#: Shaped like a real Notion integration token; never used against Notion.
_KEY = "secret_pytest_not_a_real_notion_token"

READ_ROUTES = (
    ("get", f"{BASE}/eligible", None),
    ("get", f"{BASE}/campaigns", None),
    ("get", f"{BASE}/campaigns/{uuid4()}", None),
)
WRITE_ROUTES = (
    ("post", f"{BASE}/phase-plan",
     {"subject": SUBJECT, "selected_phases": ["reflection"]}),
    ("post", f"{BASE}/estimate", {}),
    ("post", f"{BASE}/campaigns", {}),
    ("post", f"{BASE}/campaigns/{uuid4()}/canary", {}),
    ("post", f"{BASE}/campaigns/{uuid4()}/approve", {}),
    ("post", f"{BASE}/campaigns/{uuid4()}/reject", {"reason": "no"}),
    ("post", f"{BASE}/campaigns/{uuid4()}/cancel", {"reason": "no"}),
    ("post", f"{BASE}/targets/{uuid4()}/retry-generation", {}),
    ("post", f"{BASE}/targets/{uuid4()}/retry-publication", {}),
    ("post", f"{BASE}/targets/{uuid4()}/abandon", {"reason": "no"}),
)
ALL_ROUTES = READ_ROUTES + WRITE_ROUTES


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    async def _fake_session():
        session = MagicMock()
        session.commit = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_session] = _fake_session
    monkeypatch.setattr(regen_api, "_reconcile", AsyncMock(return_value=0))
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)
    regen_api.reset_rollup_debounce()


def _call(method, url, body):
    if body is None:
        return getattr(client, method)(url)
    return getattr(client, method)(url, json=body)


def _fake_service():
    service = SimpleNamespace()
    for name in (
        "create_campaign", "launch_canary", "approve_canary", "reject_canary",
        "cancel", "retry_generation", "retry_publication", "abandon", "roll_up",
    ):
        setattr(service, name, AsyncMock())
    return service


def _notion(monkeypatch, *, enabled: bool, key: str = _KEY) -> None:
    """Set this head's Notion prerequisite explicitly. Every test that touches a
    delivery route says which state it is in, because the ambient state is not a
    constant: `config.load_dotenv` means these settings come from whatever `.env`
    the host has, so a developer machine or the real head reads *configured*
    while a clean checkout reads *unconfigured*. A test that leaned on either
    would pass for the wrong reason on the other."""
    monkeypatch.setattr(settings, "notion_enabled", enabled)
    monkeypatch.setattr(settings, "notion_api_key", key)


async def _teapot(*_args, **_kwargs):
    """Stands in for a route body. 418 is a status no gate in this router
    produces, so seeing it means the request got past every gate."""
    raise HTTPException(418, "route body reached")


# ═════════════════════════ the master flag ═══════════════════════════════


@pytest.mark.parametrize("method,url,body", ALL_ROUTES)
def test_every_route_is_absent_when_the_feature_is_off(monkeypatch, method, url, body):
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    response = _call(method, url, body)

    assert response.status_code == 404


def test_the_flag_off_404_beats_request_validation(monkeypatch):
    """A malformed body must not reveal that the route parses one."""
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    response = client.post(f"{BASE}/phase-plan", json={"nonsense": True})

    assert response.status_code == 404


def test_the_flag_off_404_beats_the_publisher_gate(monkeypatch):
    """Both flags off must read as "no such feature", not "no publisher"."""
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    for url in (
        f"{BASE}/campaigns/{uuid4()}/approve",
        f"{BASE}/targets/{uuid4()}/retry-publication",
    ):
        assert client.post(url, json={}).status_code == 404


@pytest.mark.parametrize("method,url,body", ALL_ROUTES)
def test_the_feature_flag_never_precedes_authentication(
    monkeypatch, method, url, body
):
    """Flag OFF and anonymous → 401, not 404.

    FastAPI runs an ``include_router`` dependency before the sub-router's own,
    so the auth dependency mounted in ``app/api/v1/__init__.py`` decides first.
    An anonymous caller therefore cannot use the status code to discover
    whether the feature is deployed.
    """
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})
    app.dependency_overrides.pop(get_current_user, None)

    assert _call(method, url, body).status_code == 401


@pytest.mark.parametrize("method,url,body", ALL_ROUTES)
def test_an_invalid_token_is_refused_before_the_flag_too(
    monkeypatch, method, url, body
):
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})
    app.dependency_overrides.pop(get_current_user, None)

    response = _call(method, url, body) if body is None else getattr(client, method)(
        url, json=body, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_a_valid_token_with_the_flag_on_reaches_the_state_gate(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(regen_api, "_service", _fake_service)
    monkeypatch.setattr(regen_api, "_list_campaigns", AsyncMock(return_value=([], {}, 0)))

    response = client.get(
        f"{BASE}/campaigns", headers={"Authorization": "Bearer s3cret-token"}
    )

    assert response.status_code == 200
    assert response.json()["campaigns"] == []


# ═════════════════════════ the publisher flag ════════════════════════════


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/campaigns/{uuid4()}/approve",
        f"{BASE}/targets/{uuid4()}/retry-publication",
    ],
)
def test_delivery_routes_refuse_with_409_when_the_publisher_is_off(monkeypatch, url):
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    _notion(monkeypatch, enabled=True)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    service = _fake_service()
    monkeypatch.setattr(regen_api, "_service", lambda: service)

    response = client.post(url, json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "publisher_disabled"
    assert "REGENERATION_PUBLISHER_ENABLED" in detail["message"]
    assert service.approve_canary.await_count == 0
    assert service.retry_publication.await_count == 0


# ═════════════════════ the Notion prerequisite ═══════════════════════════
#
# The publisher flag says an operator wants delivery; it does not say the head
# can deliver. Approving with no usable Notion destination releases every target
# to a loop that claims them and reserves their `Homework V{n}` numbers — spent
# forever — before failing to build a client. So the answer is given here,
# before approval, and it is a 409 rather than a 404 for the same reason the
# publisher flag's is: the campaign exists and the operator may still generate,
# reject, cancel and abandon.


@pytest.mark.parametrize(
    "notion_enabled, key",
    [(False, _KEY), (True, ""), (True, "   "), (True, "not-a-notion-key")],
    ids=["disabled", "no-key", "blank-key", "wrong-shape"],
)
@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/campaigns/{uuid4()}/approve",
        f"{BASE}/targets/{uuid4()}/retry-publication",
    ],
)
def test_delivery_routes_refuse_with_409_when_notion_is_unavailable(
    monkeypatch, url, notion_enabled, key
):
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    _notion(monkeypatch, enabled=notion_enabled, key=key)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    service = _fake_service()
    monkeypatch.setattr(regen_api, "_service", lambda: service)

    response = client.post(url, json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "notion_unavailable"
    assert "NOTION_" in detail["message"]
    assert service.approve_canary.await_count == 0
    assert service.retry_publication.await_count == 0


def test_the_publisher_flag_is_answered_before_the_notion_prerequisite(monkeypatch):
    """Both wrong is one answer, not two. The flag is the switch the operator
    turns first (§3b), so it is the one named."""
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    _notion(monkeypatch, enabled=False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    monkeypatch.setattr(regen_api, "_service", _fake_service)

    response = client.post(f"{BASE}/campaigns/{uuid4()}/approve", json={})

    assert response.json()["detail"]["error"] == "publisher_disabled"


def test_the_notion_gate_never_precedes_the_feature_404(monkeypatch):
    """A hidden feature stays hidden: an unconfigured head must not answer 409
    and reveal that regeneration exists."""
    monkeypatch.setattr(settings, "regeneration_enabled", False)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    _notion(monkeypatch, enabled=False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    assert client.post(f"{BASE}/campaigns/{uuid4()}/approve", json={}).status_code == 404


@pytest.mark.parametrize(
    "method,url,body",
    [
        ("get", f"{BASE}/eligible", None),
        ("get", f"{BASE}/campaigns", None),
        ("post", f"{BASE}/phase-plan",
         {"subject": SUBJECT, "selected_phases": ["reflection"]}),
    ],
)
def test_reads_and_generation_routes_ignore_the_publisher_flag(
    monkeypatch, method, url, body
):
    """Drafting, pricing and eligibility stay open with delivery dark AND with
    Notion unconfigured — that combination is a supported way to run."""
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    _notion(monkeypatch, enabled=False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    monkeypatch.setattr(regen_api, "_service", _fake_service)
    monkeypatch.setattr(regen_api, "_list_campaigns", AsyncMock(return_value=([], {}, 0)))
    monkeypatch.setattr(
        regen_api.discovery, "list_source_candidates", AsyncMock(return_value=[])
    )

    assert _call(method, url, body).status_code == 200


@pytest.mark.parametrize(
    "url, seam",
    [
        (f"{BASE}/campaigns/{uuid4()}/canary", "_campaign_action"),
        (f"{BASE}/targets/{uuid4()}/retry-generation", "_target_action"),
    ],
    ids=["canary", "retry-generation"],
)
def test_generation_routes_still_run_with_notion_unavailable(monkeypatch, url, seam):
    """Canary generation and generation retry spend model budget but write no
    Notion page and reserve no version, so an unconfigured head must still serve
    them. The route body is replaced by a teapot: reaching it at all is the
    assertion, and 418 is a status neither gate can produce."""
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    _notion(monkeypatch, enabled=False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    monkeypatch.setattr(regen_api, "_service", _fake_service)
    monkeypatch.setattr(regen_api, seam, _teapot)

    assert client.post(url, json={}).status_code == 418


@pytest.mark.parametrize("url", [f"{BASE}/estimate", f"{BASE}/campaigns"])
def test_pricing_and_creation_are_not_gated_on_notion(monkeypatch, url):
    """Estimating prices a draft and creation freezes one; neither writes a
    Notion page or reserves a version, so both stay open on an unconfigured
    head. A **422** on this empty body is the proof, and a precise one: a route
    dependency raises before the body is validated (that is exactly why the
    feature 404 beats request validation above), so reaching validation at all
    means no gate refused the request."""
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    _notion(monkeypatch, enabled=False)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}
    monkeypatch.setattr(regen_api, "_service", _fake_service)

    assert client.post(url, json={}).status_code == 422


def test_both_flags_default_to_off():
    """The feature ships dark: nothing here is reachable on a fleet that has
    not opted in, and no test in this file may change that default."""
    from app.config import Settings

    assert Settings.model_fields["regeneration_enabled"].default is False
    assert Settings.model_fields["regeneration_publisher_enabled"].default is False
