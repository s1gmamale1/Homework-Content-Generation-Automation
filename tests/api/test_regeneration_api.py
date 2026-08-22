"""Route behaviour for the regeneration API, over service fakes.

No database and no model call: the campaign service, the discovery/estimator
reads and the report gather are faked, so what is under test is exactly the
router's job — authentication, the feature gate, request validation, the
exception→status mapping, idempotency, and the shape of a partial release.

The report content itself is proven against a real Postgres in
``tests/api/test_regeneration_reports.py``.
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

import app.auth as auth_module
from app.api.v1 import regeneration as regen_api
from app.auth import get_current_user
from app.config import settings
from app.db import get_session
from app.schemas import regeneration as schemas
from app.services import code_version
from app.services import regeneration_campaign as campaign_service
from app.services import regeneration_discovery as discovery
from app.services.regeneration_planner import build_phase_plan
from main import app

client = TestClient(app)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
SUBJECT = "math-algebra"
BASE = "/api/v1/regeneration"
#: What the process under test reports as its own code revision. Pinned by the
#: autouse fixture so nothing here depends on the checkout the suite runs from
#: — the real `code_version.GIT_SHA` is whatever HEAD happens to be, and in a
#: build without `.git` it is None.
SERVER_SHA = "0ddba11"


# ────────────────────────────── fixtures ─────────────────────────────────


@pytest.fixture(autouse=True)
def _feature_on(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_enabled", True)
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", True)
    # A head CONFIGURED to publish. `approve` and `retry-publication` refuse
    # with a 409 without a usable Notion destination, and the ambient value is
    # whatever the host's `.env` says — so it is pinned here rather than
    # inherited. The refusal itself is covered in
    # `test_regeneration_feature_flag.py`, which owns both gates.
    monkeypatch.setattr(settings, "notion_enabled", True)
    monkeypatch.setattr(settings, "notion_api_key", "secret_pytest_not_a_real_token")
    monkeypatch.setattr(code_version, "GIT_SHA", SERVER_SHA)
    # The deployed image bakes its commit into APP_GIT_REVISION, and that
    # source outranks `code_version.GIT_SHA`. Clear it unconditionally so the
    # suite reports the same revision on a developer's shell, in CI, and
    # inside the container it is testing; a test that wants the baked source
    # sets it explicitly.
    monkeypatch.delenv("APP_GIT_REVISION", raising=False)
    monkeypatch.setattr(
        regen_api,
        "_check_active_workers",
        AsyncMock(return_value=SimpleNamespace(
            ok=True,
            workers_online=1,
            compatible_worker_ids=("worker-1",),
            required_api_providers=("gemini",),
            fleet_api_paused=False,
            reason=None,
        )),
    )
    monkeypatch.setattr(
        regen_api, "_publication_version_conflicts", AsyncMock(return_value=[])
    )
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "test"}

    async def _fake_session():
        session = MagicMock()
        session.commit = AsyncMock()
        # Plain scalar reads (`get_campaign`, `_load_target`) resolve to
        # "missing" unless a test says otherwise.
        session.scalar = AsyncMock(return_value=None)
        # `launch_defaults_repo.get` is a real read on the estimate path; give
        # it the singleton row shape so contract resolution stays under test.
        session.get = AsyncMock(return_value=SimpleNamespace(
            extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
            extract_transport="api",
            judge_provider="gemini", judge_model="gemini-3.6-flash",
            judge_transport="api",
            solver_provider="gemini", solver_model="gemini-3.6-flash",
            solver_transport="api",
        ))
        yield session

    app.dependency_overrides[get_session] = _fake_session
    # The report gather is DB-shaped; every route test that isn't about the
    # report itself gets a canned one.
    reconcile = AsyncMock(return_value=0)
    monkeypatch.setattr(regen_api, "_reconcile", reconcile)

    async def _closed_reconcile():
        return await reconcile(MagicMock())

    monkeypatch.setattr(
        regen_api,
        "_reconcile_closed",
        AsyncMock(side_effect=_closed_reconcile),
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)
    regen_api.reset_rollup_debounce()


def _campaign(**overrides):
    base = dict(
        id=uuid4(),
        status="draft",
        selection_spec={},
        requested_phases=["flashcards"],
        excluded_phases=[],
        launch_contract={"provider": "gemini", "model": "gemini-3.6-flash"},
        refresh_extraction=False,
        exclusion_acknowledged=False,
        canary_size=1,
        estimated_cost_low_usd=None,
        estimated_cost_high_usd=None,
        app_git_revision=None,
        canary_launched_at=None,
        approved_at=None,
        rejected_at=None,
        cancel_requested_at=None,
        completed_at=None,
        rejected_reason=None,
        cancel_requested_reason=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _target(campaign_id=None, **overrides):
    base = dict(
        id=uuid4(),
        campaign_id=campaign_id or uuid4(),
        toc_entry_id=uuid4(),
        output_language="uz",
        is_canary=False,
        source_job_id=uuid4(),
        status="generating",
        phase_plan=build_phase_plan(
            subject=SUBJECT, selected_phases=["flashcards"]
        ).to_json(),
        publication_released_at=None,
        publication_version=None,
        notion_page_id=None,
        publication_attempts=0,
        publication_next_attempt_at=None,
        publication_last_error=None,
        terminal_at=None,
        terminal_reason=None,
        abandon_requested_at=None,
        abandon_requested_reason=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _detail(campaign=None, targets=()):
    campaign = campaign or _campaign()
    return schemas.CampaignDetailOut.build(campaign, list(targets), now=NOW)


def _fake_service(**methods):
    service = SimpleNamespace()
    for name in (
        "create_campaign", "launch_canary", "approve_canary", "reject_canary",
        "cancel", "retry_generation", "retry_publication", "abandon", "roll_up",
        "load_destination_sources",
    ):
        setattr(service, name, AsyncMock(return_value=methods.get(name)))
    for name, value in methods.items():
        if isinstance(value, AsyncMock):
            setattr(service, name, value)
    return service


def _install_service(monkeypatch, service):
    monkeypatch.setattr(regen_api, "_service", lambda: service)
    return service


def _install_detail(monkeypatch, detail=None):
    detail = detail if detail is not None else _detail()
    mock = AsyncMock(return_value=detail)
    monkeypatch.setattr(regen_api, "_campaign_detail", mock)
    return mock


def _install_target_report(monkeypatch, target=None):
    out = schemas.TargetReportOut.from_row(target or _target(), now=NOW)
    mock = AsyncMock(return_value=out)
    monkeypatch.setattr(regen_api, "_target_report", mock)
    return mock


def _create_body(**overrides):
    body = {
        "selection": {"toc_entry_ids": [str(uuid4())]},
        "contract": {
            "provider": "gemini",
            "model": "gemini-3.6-flash",
            "transport": "api",
        },
        "selected_phases": ["flashcards"],
        "publication_version": 3,
        "approved_destination_digest": "a" * 64,
        "actor": "operator",
    }
    body.update(overrides)
    return body


def _estimate_body(**overrides):
    body = _create_body()
    body.pop("actor")
    body.pop("approved_destination_digest")
    body.update(overrides)
    return body


def _source(**overrides):
    base = dict(
        source_job_id=uuid4(),
        toc_entry_id=uuid4(),
        book_id=uuid4(),
        subject=SUBJECT,
        grade="5",
        output_language="uz",
        source_publication_version=1,
        next_expected_version=2,
        source_is_revision=False,
        book_filename="algebra.pdf",
        section_number="1",
        section_title="Lesson 1",
        chapter_title="Chapter 1",
        page_start=3,
        notion_lesson_page_id="page-1",
        order_index=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _candidate(source=None, reasons=()):
    source = source if source is not None else _source()
    return discovery.SourceCandidate(
        toc_entry_id=source.toc_entry_id if source else uuid4(),
        output_language=source.output_language if source else "uz",
        source=source,
        reasons=tuple(reasons),
    )


# ═══════════════════════════ authentication ══════════════════════════════


_READ_ROUTES = (
    ("get", f"{BASE}/eligible", None),
    ("get", f"{BASE}/campaigns", None),
    ("get", f"{BASE}/campaigns/{uuid4()}", None),
)
_WRITE_ROUTES = (
    ("post", f"{BASE}/phase-plan", {"subject": SUBJECT, "selected_phases": ["reflection"]}),
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


@pytest.mark.parametrize("method,url,body", _READ_ROUTES + _WRITE_ROUTES)
def test_anonymous_requests_are_refused_on_every_route(monkeypatch, method, url, body):
    """Router-level auth, not route location and not the feature flag."""
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})

    response = getattr(client, method)(url, json=body) if body is not None \
        else getattr(client, method)(url)

    assert response.status_code == 401
    assert "token" in response.json()["detail"].lower()


def test_a_valid_operator_token_reaches_the_state_gate(monkeypatch):
    """Operator auth (header OR the SSE query form), not SA-key-strict auth."""
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})
    _install_service(monkeypatch, _fake_service())
    monkeypatch.setattr(
        regen_api, "_load_campaign",
        AsyncMock(side_effect=campaign_service.CampaignNotFound("nope")),
    )

    response = client.get(
        f"{BASE}/campaigns/{uuid4()}",
        headers={"Authorization": "Bearer s3cret-token"},
    )
    assert response.status_code == 404


def test_the_router_uses_general_operator_auth_not_the_sa_key_dependency():
    from app.api.v1 import __init__ as api_init  # noqa: F401
    from app.api.v1 import api_v1_router
    from app.auth import get_current_user_strict

    regen_routes = [
        route for route in api_v1_router.routes
        if getattr(route, "path", "").startswith("/api/v1/regeneration")
    ]
    assert regen_routes, "the regeneration router is not mounted"
    for route in regen_routes:
        callables = [
            dep.call for dep in route.dependant.dependencies if dep.call is not None
        ]
        assert get_current_user in callables
        assert get_current_user_strict not in callables


# ═══════════════════════════ discovery / preview ═════════════════════════


def test_eligible_lists_sources_and_says_why_the_rest_were_left_out(monkeypatch):
    source = _source()
    left_out = discovery.SourceCandidate(
        toc_entry_id=uuid4(), output_language="ru", source=None,
        reasons=(discovery.NO_COMPLETED_SOURCE_REASON,),
    )
    listed = AsyncMock(return_value=[_candidate(source), left_out])
    monkeypatch.setattr(discovery, "list_source_candidates", listed)

    response = client.get(
        f"{BASE}/eligible",
        params={"book_id": str(source.book_id), "output_language": ["uz", "ru"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible_count"] == 1
    assert body["sources"][0]["source_publication_version"] == 1
    assert body["sources"][0]["next_expected_version"] == 2
    assert body["ineligible"][0]["reasons"] == [discovery.NO_COMPLETED_SOURCE_REASON]
    kwargs = listed.await_args.kwargs
    assert kwargs["book_ids"] == [source.book_id]
    assert kwargs["output_languages"] == ["uz", "ru"]


# ─── selection scope (one rule, three routes) ────────────────────────────
#
# `book_ids`/`toc_entry_ids` are the only axes that bound a selection — this
# API has no subject or grade selector — so a request carrying only
# `output_languages` asks for every regenerable lesson in every book. The two
# routes that commit to a selection refuse it; `/eligible` is a browse and
# stays broad, but is still capped at what one campaign may hold.


def test_eligible_still_browses_without_a_book_or_lesson_filter(monkeypatch):
    """The picker populates from here. Browsing broadly is the point of the
    route — only the RESULT is bounded."""
    monkeypatch.setattr(
        discovery, "list_source_candidates", AsyncMock(return_value=[_candidate()])
    )
    assert client.get(f"{BASE}/eligible").status_code == 200


def test_eligible_browse_is_not_limited_to_one_campaign(monkeypatch):
    over = settings.regeneration_max_campaign_targets + 1
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=[_candidate() for _ in range(over)]),
    )
    response = client.get(f"{BASE}/eligible", params={"book_id": str(uuid4())})
    assert response.status_code == 200
    assert response.json()["eligible_count"] == over


def test_eligible_maps_the_discovery_workload_bound(monkeypatch):
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(side_effect=discovery.DiscoverySelectionTooLarge(1001, 1000)),
    )

    response = client.get(f"{BASE}/eligible", params={"book_id": str(uuid4())})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "selection_discovery_too_large",
        "message": str(discovery.DiscoverySelectionTooLarge(1001, 1000)),
        "count_at_least": 1001,
        "maximum": 1000,
    }


def test_estimate_refuses_an_unbounded_selection_without_scanning(monkeypatch):
    """Refused BEFORE discovery: the scan an unbounded selection would run is
    itself the thing being prevented, not just the campaign behind it."""
    listed = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "list_source_candidates", listed)

    response = client.post(
        f"{BASE}/estimate",
        json=_estimate_body(selection={"output_languages": ["uz"]}),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "unbounded_selection"
    assert "book_id" in detail["message"] and "toc_entry_id" in detail["message"]
    assert listed.await_count == 0


def test_estimate_cap_counts_only_eligible_targets(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_max_campaign_targets", 2)
    candidates = [_candidate()]
    candidates.extend(
        SimpleNamespace(
            toc_entry_id=uuid4(), output_language="uz", source=None,
            reasons=(discovery.NO_COMPLETED_SOURCE_REASON,), detail="",
        )
        for _ in range(5)
    )
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=candidates),
    )
    priced = AsyncMock(return_value=SimpleNamespace(
        low_usd=1.0, high_usd=2.0, line_items=(), target_count=1,
        regenerated_phase_count=10, copied_phase_count=1,
        regenerated_extract_count=0, copied_extract_count=1,
        window_start=NOW - timedelta(days=30), window_end=NOW,
        notes=(), is_estimate=True, has_unpriced_lines=False,
    ))
    monkeypatch.setattr(regen_api, "_estimate_regeneration", priced)
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[])
    )

    response = client.post(
        f"{BASE}/estimate", json=_estimate_body(selection={"book_ids": [str(uuid4())]})
    )

    assert response.status_code == 200
    assert response.json()["target_count"] == 1
    assert priced.await_count == 1


def test_estimate_refuses_more_eligible_targets_than_the_campaign_cap(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_max_campaign_targets", 2)
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=[_candidate() for _ in range(3)]),
    )
    priced = AsyncMock()
    monkeypatch.setattr(regen_api, "_estimate_regeneration", priced)

    response = client.post(
        f"{BASE}/estimate", json=_estimate_body(selection={"book_ids": [str(uuid4())]})
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "selection_too_large"
    assert priced.await_count == 0


def test_create_refuses_an_unbounded_selection_before_any_side_effect(monkeypatch):
    service = _install_service(monkeypatch, _fake_service())

    response = client.post(
        f"{BASE}/campaigns",
        json=_create_body(selection={"output_languages": ["uz"]}),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unbounded_selection"
    assert regen_api._reconcile.await_count == 0
    assert service.create_campaign.await_count == 0


@pytest.mark.parametrize(
    "error, code",
    [
        (campaign_service.UnboundedSelection(), "unbounded_selection"),
        (
            campaign_service.SelectionTooLarge(
                501,
                maximum=500,
                what="create a regeneration campaign",
            ),
            "selection_too_large",
        ),
    ],
)
def test_create_maps_a_scope_refusal_to_a_structured_422(monkeypatch, error, code):
    """The RULE is the service's — one definition — and the router's job is to
    render its refusal as something an operator can act on."""
    _install_service(
        monkeypatch,
        _fake_service(create_campaign=AsyncMock(side_effect=error)),
    )
    response = client.post(f"{BASE}/campaigns", json=_create_body())
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == code


def test_the_approval_gate_refusal_is_reported_as_its_own_conflict(monkeypatch):
    """`CanaryNotReviewable` is an `IllegalCampaignAction`, so it would map to
    the generic `illegal_campaign_state` by inheritance. It gets its own code
    because the operator's next move is specific: retry or abandon the blocked
    canaries, then approve."""
    _install_service(
        monkeypatch,
        _fake_service(approve_canary=AsyncMock(
            side_effect=campaign_service.CanaryNotReviewable(
                "1 of 2 canary target(s) are ['generation_failed']",
                blockers=["generation_failed"], total=2,
                reason_code="blocked",
                remedy="Retry or abandon the failed canary.",
            )
        )),
    )
    response = client.post(
        f"{BASE}/campaigns/{uuid4()}/approve", json={"actor": "operator"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "canary_not_reviewable"
    assert detail["blockers"] == ["generation_failed"]
    assert detail["canary_count"] == 2
    assert detail["reason_code"] == "blocked"
    assert detail["remedy"] == "Retry or abandon the failed canary."


def test_phase_plan_previews_broken_edges_without_refusing_the_preview():
    response = client.post(
        f"{BASE}/phase-plan",
        json={
            "subject": SUBJECT,
            "selected_phases": ["flashcards"],
            "excluded_affected_phases": ["reflection"],
            "exclusion_acknowledged": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["acknowledgement_required"] is True
    assert body["broken_dependency_edges"] == [
        {"upstream": "boss-arena", "downstream": "reflection"}
    ]
    assert body["acknowledgement_message"]
    assert len(body["auto_included_phases"]) >= 8


def test_phase_plan_refuses_an_unknown_subject_or_phase():
    assert client.post(
        f"{BASE}/phase-plan",
        json={"subject": "not-a-subject", "selected_phases": ["reflection"]},
    ).status_code == 422
    assert client.post(
        f"{BASE}/phase-plan",
        json={"subject": SUBJECT, "selected_phases": ["not-a-phase"]},
    ).status_code == 422


def test_estimate_is_read_only_and_carries_preflight_and_incompleteness(monkeypatch):
    source = _source()
    monkeypatch.setattr(
        discovery, "list_source_candidates", AsyncMock(return_value=[_candidate(source)])
    )
    failure = discovery.NotionPreflightFailure(
        source_job_id=source.source_job_id, toc_entry_id=source.toc_entry_id,
        subject=SUBJECT, grade="5", output_language="uz",
        lesson_title="Lesson 1", reason=discovery.NO_SUBJECT_PAGE_REASON,
        detail="NOTION_SUBJECT_PAGES has no page for math-algebra|5",
    )
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[failure])
    )
    estimate = SimpleNamespace(
        low_usd=1.0, high_usd=2.0, line_items=(), target_count=1,
        regenerated_phase_count=10, copied_phase_count=1,
        regenerated_extract_count=0, copied_extract_count=1,
        window_start=NOW - timedelta(days=30), window_end=NOW,
        notes=("note",), is_estimate=True, has_unpriced_lines=True,
    )
    priced = AsyncMock(return_value=estimate)
    monkeypatch.setattr(regen_api, "_estimate_regeneration", priced)

    response = client.post(f"{BASE}/estimate", json=_estimate_body())

    assert response.status_code == 200
    body = response.json()
    assert body["target_count"] == 1
    assert body["estimate"]["is_complete"] is False
    assert body["estimate"]["incomplete_reason"]
    assert body["preflight"]["ok"] is False
    assert body["preflight"]["failures"][0]["reason"] == discovery.NO_SUBJECT_PAGE_REASON
    assert body["phase_plans"][0]["subject"] == SUBJECT
    assert priced.await_count == 1


def test_estimate_returns_exact_version_and_worker_readiness_without_notion_io(
    monkeypatch,
):
    source = _source()
    monkeypatch.setattr(
        discovery, "list_source_candidates", AsyncMock(return_value=[_candidate(source)])
    )
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        regen_api,
        "_estimate_regeneration",
        AsyncMock(return_value=SimpleNamespace(
            low_usd=1.0, high_usd=2.0, line_items=(), target_count=1,
            regenerated_phase_count=10, copied_phase_count=1,
            regenerated_extract_count=0, copied_extract_count=1,
            window_start=NOW - timedelta(days=30), window_end=NOW,
            notes=(), is_estimate=True, has_unpriced_lines=False,
        )),
    )
    remote = AsyncMock()
    monkeypatch.setattr(regen_api, "_resolve_destinations", remote)

    response = client.post(f"{BASE}/estimate", json=_estimate_body())

    assert response.status_code == 200
    body = response.json()
    assert body["publication_version"] == 3
    assert body["worker_executability"] == {
        "ok": True,
        "workers_online": 1,
        "compatible_worker_ids": ["worker-1"],
        "required_api_providers": ["gemini"],
        "fleet_api_paused": False,
        "reason": None,
    }
    assert "destination_digest" not in body
    assert remote.await_count == 0


def test_estimate_warns_when_refresh_pdf_is_missing_on_the_head(
    monkeypatch,
    tmp_path,
):
    source = _source(book_filename="missing-algebra.pdf")
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(
        discovery,
        "list_source_candidates",
        AsyncMock(return_value=[_candidate(source)]),
    )
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        regen_api,
        "_estimate_regeneration",
        AsyncMock(return_value=SimpleNamespace(
            low_usd=1.0, high_usd=2.0, line_items=(), target_count=1,
            regenerated_phase_count=11, copied_phase_count=0,
            regenerated_extract_count=1, copied_extract_count=0,
            window_start=NOW - timedelta(days=30), window_end=NOW,
            notes=(), is_estimate=True, has_unpriced_lines=False,
        )),
    )

    response = client.post(
        f"{BASE}/estimate",
        json=_estimate_body(refresh_extraction=True),
    )

    assert response.status_code == 200
    (warning,) = response.json()["source_availability_warnings"]
    assert "missing-algebra.pdf" in warning
    assert str(source.book_id) in warning
    assert "worker" in warning.lower()


def test_destination_check_returns_every_reviewed_target(monkeypatch):
    source = campaign_service.DestinationSource(
        toc_entry_id=uuid4(), output_language="uz", source_job_id=uuid4(),
        subject=SUBJECT, grade="5", book_filename="algebra.pdf",
        section_number="1", section_title="Lesson one", chapter_title="",
        page_start=7, notion_lesson_page_id="lesson-1",
        lesson_title="1 Lesson one",
    )
    service = _install_service(monkeypatch, _fake_service())
    service.load_destination_sources.return_value = (source,)
    resolution = campaign_service.DestinationResolution(
        toc_entry_id=source.toc_entry_id, output_language="uz",
        lesson_title=source.lesson_title, status="reuse",
        container_policy="reuse", container_page_id="container-1",
        lesson_policy="reuse", lesson_page_id="lesson-1",
        candidates=(), reason=None,
    )
    resolved = AsyncMock(return_value=campaign_service.DestinationPreflight(
        ok=True, resolutions=(resolution,), digest="b" * 64,
        checked_target_count=1,
    ))
    monkeypatch.setattr(regen_api, "_resolve_destinations", resolved)

    response = client.post(f"{BASE}/destinations", json={
        "selection": {"toc_entry_ids": [str(source.toc_entry_id)]},
        "publication_version": 3,
        "destination_overrides": [],
    })

    assert response.status_code == 200
    body = response.json()
    assert body["target_count"] == body["checked_target_count"] == 1
    assert body["destination_digest"] == "b" * 64
    assert body["destinations"][0]["status"] == "reuse"
    assert body["destinations"][0]["notion_page_url"].endswith("lesson1")
    assert service.load_destination_sources.await_count == 1
    assert resolved.await_count == 1


def test_estimate_refuses_a_retired_model_the_same_way_creation_does(monkeypatch):
    """A stale `launch_defaults` row is the ONE way a retired model reaches a
    campaign: the draft body is manifest-validated by pydantic, but that row is
    not, and gemini-2.5 was retired on 2026-08-03.

    Without this check the estimate prices a dead model and answers 200 (or
    422-ing on an "unknown (provider, model)" message that never says the word
    retired). The operator only finds out one request later, at create. The
    check runs on the RAW pins before contract resolution — the same order
    `_stored_contract` uses, and for the same reason: a retired model is no
    longer in MODEL_MANIFEST, so resolution refuses it with a message that
    tells an operator nothing about retirement.
    """
    source = _source()
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=[_candidate(source)]),
    )
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[])
    )
    priced = AsyncMock()
    monkeypatch.setattr(regen_api, "_estimate_regeneration", priced)

    async def _retired_defaults_session():
        session = MagicMock()
        session.commit = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.get = AsyncMock(return_value=SimpleNamespace(
            extract_provider="gemini", extract_model="gemini-2.5-flash",
            extract_transport="api",
            judge_provider="gemini", judge_model="gemini-3.6-flash",
            judge_transport="api",
            solver_provider="gemini", solver_model="gemini-3.6-flash",
            solver_transport="api",
        ))
        yield session

    app.dependency_overrides[get_session] = _retired_defaults_session

    response = client.post(f"{BASE}/estimate", json=_estimate_body())

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "retired_model"
    assert detail["retired"] == [
        {"role": "extract", "provider": "gemini", "model": "gemini-2.5-flash"}
    ]
    assert "retired" in detail["message"]
    assert priced.await_count == 0, "a retired campaign must not be priced"


def test_estimate_with_no_eligible_lineage_reports_it_without_pricing(monkeypatch):
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=[
            discovery.SourceCandidate(
                toc_entry_id=uuid4(), output_language="uz", source=None,
                reasons=(discovery.NO_COMPLETED_SOURCE_REASON,),
            )
        ]),
    )
    priced = AsyncMock()
    monkeypatch.setattr(regen_api, "_estimate_regeneration", priced)

    response = client.post(f"{BASE}/estimate", json=_estimate_body())

    assert response.status_code == 200
    body = response.json()
    assert body["target_count"] == 0
    assert body["estimate"] is None
    assert body["ineligible"]
    assert priced.await_count == 0


# ═══════════════════════════ campaign creation ═══════════════════════════


def test_create_campaign_returns_the_new_campaign_report(monkeypatch):
    campaign = _campaign()
    service = _install_service(
        monkeypatch, _fake_service(create_campaign=AsyncMock(return_value=campaign))
    )
    detail = _install_detail(monkeypatch, _detail(campaign))

    response = client.post(f"{BASE}/campaigns", json=_create_body(canary_size=2))

    assert response.status_code == 201
    assert response.json()["id"] == str(campaign.id)
    spec = service.create_campaign.await_args.args[0]
    assert spec.canary_size == 2
    assert spec.publication_version == 3
    assert spec.approved_destination_digest == "a" * 64
    assert spec.selected_phases == ("flashcards",)
    assert spec.contract.transport == "api"
    assert detail.await_count == 1


def test_create_maps_a_changed_destination_review_without_a_report_read(monkeypatch):
    service = _install_service(
        monkeypatch,
        _fake_service(create_campaign=AsyncMock(side_effect=
            campaign_service.DestinationReviewChanged("review changed"))),
    )
    detail = _install_detail(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "destination_review_changed"
    assert service.create_campaign.await_count == 1
    assert detail.await_count == 0


@pytest.mark.parametrize(
    "error,expected_status,marker",
    [
        (
            campaign_service.ActiveLineageConflict([(uuid4(), "uz")]),
            409,
            "active_lineage_conflict",
        ),
        (
            campaign_service.NoEligibleTargets([
                discovery.SourceCandidate(
                    toc_entry_id=uuid4(), output_language="uz", source=None,
                    reasons=("no completed homework job",),
                )
            ]),
            409,
            "no_eligible_targets",
        ),
        (
            campaign_service.NonApiTransport([("transport", "cli")]),
            422,
            "non_api_transport",
        ),
        (
            campaign_service.RetiredModelRefusal(
                [("judge", "gemini", "gemini-2.5-flash")], what="create a campaign"
            ),
            409,
            "retired_model",
        ),
    ],
)
def test_create_campaign_maps_each_service_refusal(
    monkeypatch, error, expected_status, marker
):
    _install_service(
        monkeypatch, _fake_service(create_campaign=AsyncMock(side_effect=error))
    )
    _install_detail(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == expected_status
    assert response.json()["detail"]["error"] == marker
    assert response.json()["detail"]["message"]


def test_active_lineage_conflict_names_the_campaign_the_operator_must_open(monkeypatch):
    owner_id = uuid4()
    _install_service(
        monkeypatch,
        _fake_service(
            create_campaign=AsyncMock(
                side_effect=campaign_service.ActiveLineageConflict(
                    [(uuid4(), "uz")], campaign_ids=[owner_id]
                )
            )
        ),
    )
    _install_detail(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 409
    assert response.json()["detail"]["campaign_ids"] == [str(owner_id)]


def test_create_campaign_maps_an_unacknowledged_exclusion_to_422(monkeypatch):
    from app.services.regeneration_planner import ExclusionAcknowledgementRequired

    _install_service(
        monkeypatch,
        _fake_service(create_campaign=AsyncMock(
            side_effect=ExclusionAcknowledgementRequired(
                "excluded phases will be left stale by regenerated upstreams"
            )
        )),
    )
    response = client.post(
        f"{BASE}/campaigns",
        json=_create_body(
            excluded_affected_phases=["reflection"], exclusion_acknowledged=False
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "exclusion_acknowledgement_required"


def test_create_campaign_refuses_a_prompt_set_selector():
    response = client.post(f"{BASE}/campaigns", json=_create_body(prompt_set="old"))
    assert response.status_code == 422


# ═══════════════════════ campaign code provenance ════════════════════════
#
# §6 of the approved design: every campaign records the application revision it
# was created under, because "which code produced this packet?" is the first
# question asked of a regenerated lesson. The SPA posts `app_git_revision:
# null` deliberately, so the head serving the request is the normal source of
# the value — and a container built without `.git` has none, which is the case
# these tests exist for.


def _echoing_service(monkeypatch):
    """A create-service that stamps whatever revision the route resolved onto
    the campaign it returns, plus a report gather that renders THAT campaign.

    So a test reads the stamped value off the response body — the thing an
    operator and the audit column actually see — and not only off the spec.
    """
    created: list = []

    async def _create(spec):
        campaign = _campaign(app_git_revision=spec.app_git_revision)
        created.append(campaign)
        return campaign

    async def _report(session, campaign_id, *, now):
        return _detail(created[-1])

    service = _install_service(
        monkeypatch, _fake_service(create_campaign=AsyncMock(side_effect=_create))
    )
    monkeypatch.setattr(regen_api, "_campaign_detail", AsyncMock(side_effect=_report))
    return service


def test_create_campaign_keeps_an_explicit_revision_over_the_servers_own(monkeypatch):
    """The field is exposed on purpose: whoever deployed the code may know the
    revision better than the process does. An explicit value is normalized and
    preserved — never silently replaced by the head's own SHA."""
    service = _echoing_service(monkeypatch)

    response = client.post(
        f"{BASE}/campaigns", json=_create_body(app_git_revision="  deadbee  ")
    )

    assert response.status_code == 201
    assert service.create_campaign.await_args.args[0].app_git_revision == "deadbee"
    assert response.json()["app_git_revision"] == "deadbee"
    # ... and the fallback was genuinely not taken.
    assert response.json()["app_git_revision"] != SERVER_SHA


@pytest.mark.parametrize(
    "body",
    [
        _create_body(),
        _create_body(app_git_revision=None),
        _create_body(app_git_revision=""),
        _create_body(app_git_revision="   "),
    ],
    ids=["absent", "explicit-null", "empty", "whitespace"],
)
def test_create_campaign_stamps_the_running_processs_revision(monkeypatch, body):
    """No usable value in the request — including the null the SPA sends by
    design — means the head that serves the create is the authority."""
    service = _echoing_service(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=body)

    assert response.status_code == 201
    assert service.create_campaign.await_args.args[0].app_git_revision == SERVER_SHA
    assert response.json()["app_git_revision"] == SERVER_SHA


def test_an_explicit_revision_still_works_where_the_process_has_no_git(monkeypatch):
    """The escape hatch, stated: a `.git`-less build is auditable as long as
    whatever deployed it says which revision it deployed."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    _echoing_service(monkeypatch)

    response = client.post(
        f"{BASE}/campaigns", json=_create_body(app_git_revision="c8a4c18")
    )

    assert response.status_code == 201
    assert response.json()["app_git_revision"] == "c8a4c18"


@pytest.mark.parametrize("server_sha", [None, "", "   "], ids=["none", "empty", "blank"])
def test_create_campaign_refuses_when_no_revision_can_be_established(
    monkeypatch, server_sha
):
    """Neither source yields a value, so the campaign is NOT created.

    A NULL in the audit column would read as "unknown code" exactly when an
    operator most needs to know, and the row would be immutable once written —
    so this is a refusal, not a silent fallback.
    """
    monkeypatch.setattr(code_version, "GIT_SHA", server_sha)
    service = _install_service(monkeypatch, _fake_service())
    detail = _install_detail(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 409
    detail_body = response.json()["detail"]
    assert detail_body["error"] == "app_git_revision_unavailable"
    # Plain operator guidance: the field to send, and where a SHA comes from.
    assert "app_git_revision" in detail_body["message"]
    assert "git" in detail_body["message"].lower()
    # Refused before ANY side effect — no campaign, no report gather, and not
    # even the crash-repair sweep. Creation is the audit boundary.
    assert service.create_campaign.await_count == 0
    assert detail.await_count == 0
    assert regen_api._reconcile.await_count == 0


def test_the_provenance_gate_stays_behind_the_feature_flag_and_auth(monkeypatch):
    """The refusal describes THIS deployment, so it must never be the first
    thing a request meets: flag-off is still 404 and anonymous is still 401,
    even on a head that could not stamp a revision."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    service = _install_service(monkeypatch, _fake_service())

    monkeypatch.setattr(settings, "regeneration_enabled", False)
    assert client.post(f"{BASE}/campaigns", json=_create_body()).status_code == 404

    monkeypatch.setattr(settings, "regeneration_enabled", True)
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setattr(auth_module, "valid_auth_tokens", lambda: {"s3cret-token"})
    assert client.post(f"{BASE}/campaigns", json=_create_body()).status_code == 401

    assert service.create_campaign.await_count == 0


def test_a_malformed_draft_is_still_422_on_a_head_with_no_revision(monkeypatch):
    """Request validation keeps precedence: a body the server cannot even
    parse must not be answered with a report on the deployment's git state."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    service = _install_service(monkeypatch, _fake_service())

    response = client.post(f"{BASE}/campaigns", json=_create_body(prompt_set="old"))

    assert response.status_code == 422
    assert service.create_campaign.await_count == 0


def test_estimate_prices_a_draft_on_a_head_that_has_no_revision(monkeypatch):
    """Estimate creates nothing and spends nothing, so it is not the audit
    boundary. Hoisting the gate onto the router — or onto this route — would
    deny an operator a free preview for a reason that only matters at create."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    monkeypatch.setattr(
        discovery, "list_source_candidates", AsyncMock(return_value=[])
    )

    response = client.post(f"{BASE}/estimate", json=_estimate_body())

    assert response.status_code == 200
    assert response.json()["target_count"] == 0


# ═════════════════ the baked build revision (APP_GIT_REVISION) ═══════════
#
# The head that serves a create in production is a container, and that
# container has no git: `.dockerignore` excludes `.git` and the runtime image
# installs no git binary, so `code_version.GIT_SHA` is None there. The SPA
# posts `app_git_revision: null` by design. Those two facts together make the
# git fallback answer nothing and every containerised create a 409 — the
# feature is unusable exactly where it ships.
#
# So the build states the commit it built, into `APP_GIT_REVISION`, and the
# resolver reads three sources in order: explicit request, baked environment,
# this process's own git. The environment sits in the middle because it is a
# deliberate statement by whatever produced the artifact, while a local
# checkout is only evidence that A checkout exists.


def test_the_baked_build_revision_answers_for_a_process_with_no_git(monkeypatch):
    """The production shape: a container knows its commit only because the
    build wrote it into the environment."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    monkeypatch.setenv("APP_GIT_REVISION", "1a2b3c4d5e6f")
    service = _echoing_service(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 201
    spec = service.create_campaign.await_args.args[0]
    assert spec.app_git_revision == "1a2b3c4d5e6f"
    assert response.json()["app_git_revision"] == "1a2b3c4d5e6f"


def test_the_baked_build_revision_beats_the_processs_own_git_sha(monkeypatch):
    """Both sources answer, and the BUILD wins.

    A checkout being present is not evidence of what was deployed — a mounted
    source tree drifts from the code the process already imported, and a base
    layer can carry someone else's `.git`. The build named its commit on
    purpose, so that statement outranks a local guess.
    """
    monkeypatch.setenv("APP_GIT_REVISION", "bui1dsha")
    service = _echoing_service(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 201
    assert service.create_campaign.await_args.args[0].app_git_revision == "bui1dsha"
    assert response.json()["app_git_revision"] == "bui1dsha"
    # ... and the git source was genuinely not consulted.
    assert response.json()["app_git_revision"] != SERVER_SHA


def test_an_explicit_request_revision_still_beats_the_baked_one(monkeypatch):
    """The request field stays the top of the chain, normalization included:
    an operator correcting a mis-baked image must not be overruled by it."""
    monkeypatch.setenv("APP_GIT_REVISION", "bui1dsha")
    service = _echoing_service(monkeypatch)

    response = client.post(
        f"{BASE}/campaigns", json=_create_body(app_git_revision="  0perator  ")
    )

    assert response.status_code == 201
    assert service.create_campaign.await_args.args[0].app_git_revision == "0perator"
    assert response.json()["app_git_revision"] == "0perator"


@pytest.mark.parametrize(
    "baked", ["", "   ", "\t\n "], ids=["empty", "spaces", "whitespace"]
)
def test_a_blank_baked_revision_falls_through_to_the_processs_git(monkeypatch, baked):
    """`ARG APP_GIT_REVISION=""` with no `--build-arg` exports the variable
    EMPTY, so a hand-rolled `docker build` — and any dev shell that exported
    it and moved on — reaches the resolver with the name present and
    meaningless. Present-but-blank reads as absent, or the middle source
    shadows a working git checkout with nothing."""
    monkeypatch.setenv("APP_GIT_REVISION", baked)
    service = _echoing_service(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 201
    assert service.create_campaign.await_args.args[0].app_git_revision == SERVER_SHA
    assert response.json()["app_git_revision"] == SERVER_SHA


def test_a_blank_baked_revision_on_a_gitless_process_is_still_refused(monkeypatch):
    """An image built without the arg, on a head with no git: no source can
    name a revision, so the campaign is not created. A blank string in an
    immutable audit column is the outcome this whole chain exists to avoid."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    monkeypatch.setenv("APP_GIT_REVISION", "   ")
    service = _install_service(monkeypatch, _fake_service())
    detail = _install_detail(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "app_git_revision_unavailable"
    # Still refused before any side effect — the audit boundary is unmoved.
    assert service.create_campaign.await_count == 0
    assert detail.await_count == 0
    assert regen_api._reconcile.await_count == 0


def test_an_over_long_baked_revision_cannot_overflow_the_audit_column(monkeypatch):
    """`regeneration_campaigns.app_git_revision` is `String(64)`.

    The request field is bounded by the schema, but nothing bounds a build
    arg — and an unbounded value would fail at INSERT, turning one mis-set
    variable into a 500 on a request that had already passed validation. 64 is
    also exactly a full SHA-256 git object name, so no real revision is lost.
    """
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    monkeypatch.setenv("APP_GIT_REVISION", "  " + "f" * 200 + "  ")
    service = _echoing_service(monkeypatch)

    response = client.post(f"{BASE}/campaigns", json=_create_body())

    assert response.status_code == 201
    stamped = service.create_campaign.await_args.args[0].app_git_revision
    assert stamped == "f" * 64
    assert response.json()["app_git_revision"] == "f" * 64


def test_the_refusal_names_the_build_arg_that_fixes_it(monkeypatch):
    """The operator meeting this 409 is almost always looking at a container,
    where "run the API from a git checkout" is not a fix they can apply. The
    message has to name the variable and the build arg that are."""
    monkeypatch.setattr(code_version, "GIT_SHA", None)
    _install_service(monkeypatch, _fake_service())
    _install_detail(monkeypatch)

    message = client.post(f"{BASE}/campaigns", json=_create_body()).json()[
        "detail"
    ]["message"]

    assert "APP_GIT_REVISION" in message
    assert "build-arg" in message
    # The two original escape hatches are still offered, not replaced.
    assert "app_git_revision" in message
    assert "git" in message.lower()


# ═══════════ the build side of the same provenance chain ═════════════════
#
# `APP_GIT_REVISION` is a real source only if something actually sets it, so
# the resolver above and these two files are one mechanism. They are pinned
# here, next to the behaviour they serve, because the failure they close is
# precisely the split kind: a resolver that is green over unit fakes while the
# image it ships in never declares the variable it reads.
#
# Every assertion below normalizes whitespace and parses structure rather than
# matching source lines, so reformatting the Dockerfile or re-indenting the
# workflow cannot turn a preserved contract into a red test — or a dropped one
# into a green one.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"


def _dockerfile_instructions() -> list[tuple[str, str]]:
    """`(INSTRUCTION, argument)` pairs: comments dropped, `\\`-continuations
    joined, inner whitespace collapsed."""
    logical: list[str] = []
    buffer = ""
    for raw in _DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        logical.append((buffer + line).strip())
        buffer = ""
    if buffer.strip():
        logical.append(buffer.strip())
    parsed = []
    for line in logical:
        head, _, rest = line.partition(" ")
        parsed.append((head.upper(), " ".join(rest.split())))
    return parsed


def _runtime_stage() -> list[tuple[str, str]]:
    """Only the instructions inside `FROM ... AS runtime`.

    The stage boundary IS the assertion: an `ARG` declared in a builder stage
    is scoped to that stage and never reaches the shipped image, so a
    correct-looking line in the wrong stage sets nothing at runtime.
    """
    instructions = _dockerfile_instructions()
    starts = [
        i
        for i, (op, arg) in enumerate(instructions)
        if op == "FROM" and arg.lower().endswith(" as runtime")
    ]
    assert len(starts) == 1, f"expected one `FROM ... AS runtime`, got {starts}"
    start = starts[0]
    end = next(
        (i for i in range(start + 1, len(instructions)) if instructions[i][0] == "FROM"),
        len(instructions),
    )
    return instructions[start + 1 : end]


def test_the_runtime_image_declares_the_revision_build_arg():
    """Without an `ARG` in the runtime stage there is nothing for CI's
    `--build-arg` to bind to: docker warns and drops the value silently."""
    args = [arg for op, arg in _runtime_stage() if op == "ARG"]
    names = [entry.split("=", 1)[0].strip() for arg in args for entry in arg.split()]
    assert "APP_GIT_REVISION" in names, f"runtime-stage ARGs: {args}"


def test_the_runtime_image_exports_the_build_arg_into_the_environment():
    """A build arg is invisible to the running process; only `ENV` survives
    into the container, and only when the `ARG` is already in scope — an `ENV`
    placed above its `ARG` expands to empty and looks correct in review."""
    instructions = _runtime_stage()
    exported = r"(^|\s)APP_GIT_REVISION\s*=\s*[\"']?\$\{?APP_GIT_REVISION\}?[\"']?(\s|$)"
    env_at = [
        i
        for i, (op, arg) in enumerate(instructions)
        if op == "ENV" and re.search(exported, arg)
    ]
    arg_at = [
        i
        for i, (op, arg) in enumerate(instructions)
        if op == "ARG" and "APP_GIT_REVISION" in arg
    ]
    assert env_at, f"runtime-stage ENVs: {[a for o, a in instructions if o == 'ENV']}"
    assert arg_at and min(arg_at) < min(env_at), (
        f"ARG at {arg_at} must precede ENV at {env_at}"
    )


def test_the_runtime_image_still_ships_without_git():
    """The premise the build arg exists for, pinned.

    If the image ever gained `.git` or a git binary this chain would start
    looking redundant — right up until the next slim rebuild silently removed
    it again and every containerised create became a 409.
    """
    instructions = _runtime_stage()
    copies = [arg for op, arg in instructions if op == "COPY"]
    assert not [c for c in copies if re.search(r"(^|\s|/)\.git(\s|/|$)", c)], copies
    runs = " ".join(arg for op, arg in instructions if op == "RUN")
    assert not re.search(r"\binstall\b[^&|;]*\bgit\b", runs), runs
    ignored = {
        line.strip()
        for line in (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    }
    assert ".git" in ignored, "the build context must keep excluding .git"


def _workflow_document() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _build_step() -> dict:
    steps = _workflow_document()["jobs"]["build-and-push"]["steps"]
    matches = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("docker/build-push-action")
    ]
    assert len(matches) == 1, f"expected one image build step, got {len(matches)}"
    return matches[0]


def _build_args(step: dict) -> dict[str, str]:
    """`build-args` is a newline-delimited `KEY=VALUE` block, so parse it —
    a substring match would pass on a commented-out or misspelled entry."""
    pairs: dict[str, str] = {}
    for line in str(step.get("with", {}).get("build-args") or "").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        key, sep, value = entry.partition("=")
        assert sep, f"malformed build-arg entry: {entry!r}"
        pairs[key.strip()] = value.strip()
    return pairs


def test_ci_bakes_the_built_commit_into_every_image():
    """The workflow is the only party that still knows the commit: the build
    context discards `.git`, so nothing downstream can recover it. One build
    step serves push, tag, PR and dispatch, so wiring it here covers them all.
    """
    args = _build_args(_build_step())
    assert "APP_GIT_REVISION" in args, args
    assert re.fullmatch(
        r"\$\{\{\s*github\.sha\s*\}\}", args["APP_GIT_REVISION"]
    ), args["APP_GIT_REVISION"]


def test_the_build_step_keeps_its_publishing_contract():
    """The build arg is an addition, not a rewrite. Both architectures, the
    tag set, the GHA cache and max-mode provenance are what make the pushed
    image usable and attestable, and each is a single line to lose while
    editing the `with:` block above it."""
    with_block = _build_step()["with"]
    assert with_block["context"] == "."
    assert with_block["push"] == "${{ github.event_name != 'pull_request' }}"
    assert [p.strip() for p in str(with_block["platforms"]).split(",")] == [
        "linux/amd64",
        "linux/arm64",
    ]
    assert with_block["tags"] == "${{ steps.meta.outputs.tags }}"
    assert with_block["labels"] == "${{ steps.meta.outputs.labels }}"
    assert with_block["cache-from"] == "type=gha"
    assert with_block["cache-to"] == "type=gha,mode=max"
    assert with_block["provenance"] == "mode=max"
    assert with_block["sbom"] is True


def test_the_workflow_still_fires_on_every_publishing_event():
    """Triggers and the tag set, pinned alongside the build arg they feed.

    (`on:` is YAML 1.1's boolean `on` under `safe_load`, hence the two-key
    lookup — a plain `document["on"]` would KeyError and read as a dropped
    trigger block.)
    """
    document = _workflow_document()
    triggers = document.get("on", document.get(True))
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["master"]
    assert triggers["push"]["tags"] == ["v*.*.*"]
    assert triggers["pull_request"]["branches"] == ["master"]

    steps = document["jobs"]["build-and-push"]["steps"]
    meta = next(step for step in steps if step.get("id") == "meta")
    declared = {line.strip() for line in str(meta["with"]["tags"]).splitlines()}
    assert {
        "type=ref,event=branch",
        "type=ref,event=pr",
        "type=sha,prefix=sha-",
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "type=raw,value=latest,enable={{is_default_branch}}",
    } <= declared, declared


# ═══════════════════════════ campaign reads ══════════════════════════════


def test_campaign_detail_reconciles_then_rolls_up(monkeypatch):
    campaign = _campaign()
    service = _install_service(monkeypatch, _fake_service())
    detail = _install_detail(monkeypatch, _detail(campaign))
    monkeypatch.setattr(regen_api, "_load_campaign", AsyncMock(return_value=campaign))

    response = client.get(f"{BASE}/campaigns/{campaign.id}")

    assert response.status_code == 200
    assert regen_api._reconcile.await_count == 1
    assert service.roll_up.await_count == 1
    assert detail.await_count == 1


def test_repeated_detail_polls_do_not_take_the_campaign_write_lock(monkeypatch):
    """MI-1: `roll_up` holds the campaign FOR UPDATE and makes the publisher's
    wait-free claim skip that campaign, so a poll must not run it every time."""
    campaign = _campaign()
    service = _install_service(monkeypatch, _fake_service())
    _install_detail(monkeypatch, _detail(campaign))
    monkeypatch.setattr(regen_api, "_load_campaign", AsyncMock(return_value=campaign))
    clock = {"t": 1000.0}
    monkeypatch.setattr(regen_api, "_clock", lambda: clock["t"])
    monkeypatch.setattr(settings, "regeneration_publisher_interval_seconds", 30)

    for _ in range(5):
        assert client.get(f"{BASE}/campaigns/{campaign.id}").status_code == 200
    assert service.roll_up.await_count == 1

    clock["t"] += 31
    assert client.get(f"{BASE}/campaigns/{campaign.id}").status_code == 200
    assert service.roll_up.await_count == 2
    # Reconciliation is NOT debounced: it is the crash-repair path and it takes
    # no campaign lock unless a target actually needs repair.
    assert regen_api._reconcile.await_count == 6


def test_the_rollup_debounce_state_stays_bounded(monkeypatch):
    monkeypatch.setattr(regen_api, "_clock", lambda: 5000.0)
    monkeypatch.setattr(settings, "regeneration_publisher_interval_seconds", 30)
    for _ in range(regen_api._DEBOUNCE_MAX_ENTRIES + 50):
        regen_api._claim_rollup_slot(uuid4())
    assert len(regen_api._ROLLUP_DEBOUNCE) <= regen_api._DEBOUNCE_MAX_ENTRIES


def test_the_rollup_debounce_prunes_expired_entries(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(regen_api, "_clock", lambda: clock["t"])
    monkeypatch.setattr(settings, "regeneration_publisher_interval_seconds", 30)
    stale = uuid4()
    assert regen_api._claim_rollup_slot(stale) is True
    clock["t"] += 3600
    assert regen_api._claim_rollup_slot(uuid4()) is True
    assert stale not in regen_api._ROLLUP_DEBOUNCE


def test_a_failing_rollup_does_not_500_the_report(monkeypatch):
    campaign = _campaign()
    _install_service(
        monkeypatch,
        _fake_service(roll_up=AsyncMock(
            side_effect=ValueError("publication state before campaign approval")
        )),
    )
    detail = _install_detail(monkeypatch, _detail(campaign))
    monkeypatch.setattr(regen_api, "_load_campaign", AsyncMock(return_value=campaign))

    response = client.get(f"{BASE}/campaigns/{campaign.id}")

    assert response.status_code == 200
    assert "publication state" in response.json()["rollup_error"]
    assert detail.await_count == 1


def test_campaign_detail_404s_for_an_unknown_campaign(monkeypatch):
    _install_service(monkeypatch, _fake_service())
    monkeypatch.setattr(
        regen_api, "_load_campaign",
        AsyncMock(side_effect=campaign_service.CampaignNotFound("gone")),
    )
    assert client.get(f"{BASE}/campaigns/{uuid4()}").status_code == 404


def test_campaign_list_paginates_and_filters(monkeypatch):
    campaign = _campaign(status="bulk_running")
    listed = AsyncMock(return_value=([campaign], {campaign.id: {"published": 2}}, 1))
    monkeypatch.setattr(regen_api, "_list_campaigns", listed)

    response = client.get(
        f"{BASE}/campaigns", params={"status": "bulk_running", "limit": 10}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["campaigns"][0]["bucket_counts"]["published"] == 2
    assert listed.await_args.kwargs["statuses"] == ["bulk_running"]
    assert listed.await_args.kwargs["limit"] == 10


# ═══════════════════════════ canary / approval ═══════════════════════════


def test_canary_launch_returns_the_refreshed_campaign(monkeypatch):
    campaign = _campaign(status="canary_running")
    service = _install_service(
        monkeypatch, _fake_service(launch_canary=AsyncMock(return_value=campaign))
    )
    _install_detail(monkeypatch, _detail(campaign))

    response = client.post(f"{BASE}/campaigns/{campaign.id}/canary")

    assert response.status_code == 200
    assert response.json()["status"] == "canary_running"
    assert service.launch_canary.await_args.args[0] == campaign.id
    assert regen_api._reconcile.await_count == 1


def test_canary_preflight_failures_are_one_409_listing_every_lesson(monkeypatch):
    failures = [
        discovery.NotionPreflightFailure(
            source_job_id=uuid4(), toc_entry_id=uuid4(), subject=SUBJECT,
            grade="5", output_language="uz", lesson_title=f"Lesson {i}",
            reason=discovery.NO_SUBJECT_PAGE_REASON, detail="fix NOTION_SUBJECT_PAGES",
        )
        for i in range(4)
    ]
    _install_service(
        monkeypatch,
        _fake_service(launch_canary=AsyncMock(
            side_effect=campaign_service.PreflightBlocked(failures)
        )),
    )

    response = client.post(f"{BASE}/campaigns/{uuid4()}/canary")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "preflight_blocked"
    assert detail["count"] == 4
    assert len(detail["failures"]) == 4
    assert {f["lesson_title"] for f in detail["failures"]} == {
        "Lesson 0", "Lesson 1", "Lesson 2", "Lesson 3"
    }


def test_a_partial_release_is_committed_success_not_a_failure(monkeypatch):
    """`PartialWaveRelease` is raised AFTER every healthy job is committed."""
    campaign = _campaign(status="bulk_running", approved_at=NOW)
    failed_target = _target(campaign_id=campaign.id, status="abandoned",
                            terminal_at=NOW)
    failure = campaign_service.WaveFailure(
        target_id=failed_target.id,
        source_job_id=failed_target.source_job_id,
        reason="snapshot no longer validates",
    )
    _install_service(
        monkeypatch,
        _fake_service(approve_canary=AsyncMock(
            side_effect=campaign_service.PartialWaveRelease([failure])
        )),
    )
    _install_detail(monkeypatch, _detail(campaign))
    monkeypatch.setattr(
        regen_api, "_current_target_statuses",
        AsyncMock(return_value={failed_target.id: "abandoned"}),
    )

    response = client.post(f"{BASE}/campaigns/{campaign.id}/approve", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "bulk_running"
    assert len(body["released_failures"]) == 1
    entry = body["released_failures"][0]
    assert entry["target_id"] == str(failed_target.id)
    assert entry["source_job_id"] == str(failed_target.source_job_id)
    assert entry["reason"] == "snapshot no longer validates"
    # NOT the exception message's "all are generation_failed" claim.
    assert entry["current_status"] == "abandoned"


def test_approval_is_idempotent_and_returns_the_current_resource(monkeypatch):
    campaign = _campaign(status="bulk_running", approved_at=NOW)
    service = _install_service(
        monkeypatch, _fake_service(approve_canary=AsyncMock(return_value=campaign))
    )
    _install_detail(monkeypatch, _detail(campaign))

    first = client.post(f"{BASE}/campaigns/{campaign.id}/approve", json={})
    second = client.post(f"{BASE}/campaigns/{campaign.id}/approve", json={})

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert service.approve_canary.await_count == 2


def test_approval_of_a_terminal_campaign_is_a_human_readable_409(monkeypatch):
    _install_service(
        monkeypatch,
        _fake_service(approve_canary=AsyncMock(
            side_effect=campaign_service.IllegalCampaignAction(
                "campaign is 'cancelled' — it can no longer be approved"
            )
        )),
    )
    response = client.post(f"{BASE}/campaigns/{uuid4()}/approve", json={})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "illegal_campaign_state"
    assert "can no longer be approved" in detail["message"]


def test_approve_requires_the_publisher_flag(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    service = _install_service(monkeypatch, _fake_service())

    response = client.post(f"{BASE}/campaigns/{uuid4()}/approve", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "publisher_disabled"
    assert "publication" in response.json()["detail"]["message"].lower()
    assert service.approve_canary.await_count == 0


def test_reject_and_cancel_return_the_refreshed_campaign(monkeypatch):
    campaign = _campaign(status="rejected", rejected_at=NOW)
    service = _install_service(
        monkeypatch,
        _fake_service(
            reject_canary=AsyncMock(return_value=campaign),
            cancel=AsyncMock(return_value=campaign),
        ),
    )
    _install_detail(monkeypatch, _detail(campaign))

    reject = client.post(
        f"{BASE}/campaigns/{campaign.id}/reject",
        json={"actor": "op", "reason": "content was wrong"},
    )
    cancel = client.post(
        f"{BASE}/campaigns/{campaign.id}/cancel",
        json={"actor": "op", "reason": "stop"},
    )

    assert reject.status_code == 200
    assert cancel.status_code == 200
    assert service.reject_canary.await_args.kwargs["reason"] == "content was wrong"
    assert service.cancel.await_args.kwargs["actor"] == "op"


def test_reject_and_cancel_require_a_reason():
    assert client.post(
        f"{BASE}/campaigns/{uuid4()}/reject", json={"actor": "op"}
    ).status_code == 422
    assert client.post(
        f"{BASE}/campaigns/{uuid4()}/cancel", json={"actor": "op", "reason": " "}
    ).status_code == 422


def test_canary_and_reject_do_not_require_the_publisher_flag(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    campaign = _campaign(status="canary_running")
    service = _install_service(
        monkeypatch,
        _fake_service(
            launch_canary=AsyncMock(return_value=campaign),
            reject_canary=AsyncMock(return_value=campaign),
        ),
    )
    _install_detail(monkeypatch, _detail(campaign))

    assert client.post(f"{BASE}/campaigns/{campaign.id}/canary").status_code == 200
    assert client.post(
        f"{BASE}/campaigns/{campaign.id}/reject",
        json={"actor": "op", "reason": "no"},
    ).status_code == 200
    assert service.launch_canary.await_count == 1


# ═══════════════════════════ target actions ══════════════════════════════


def test_retry_generation_returns_the_refreshed_target(monkeypatch):
    target = _target(status="generating")
    service = _install_service(
        monkeypatch, _fake_service(retry_generation=AsyncMock(return_value=target))
    )
    _install_target_report(monkeypatch, target)

    response = client.post(f"{BASE}/targets/{target.id}/retry-generation", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["target"]["id"] == str(target.id)
    assert body["campaign_id"] == str(target.campaign_id)
    assert service.retry_generation.await_args.args[0] == target.id
    assert regen_api._reconcile.await_count == 1


def test_retry_generation_partial_release_is_200_with_the_real_status(monkeypatch):
    target = _target(status="generation_failed")
    failure = campaign_service.WaveFailure(
        target_id=target.id, source_job_id=target.source_job_id,
        reason="source snapshot was purged",
    )
    _install_service(
        monkeypatch,
        _fake_service(retry_generation=AsyncMock(
            side_effect=campaign_service.PartialWaveRelease([failure])
        )),
    )
    _install_target_report(monkeypatch, target)
    monkeypatch.setattr(regen_api, "_load_target", AsyncMock(return_value=target))
    monkeypatch.setattr(
        regen_api, "_current_target_statuses",
        AsyncMock(return_value={target.id: "generation_failed"}),
    )

    response = client.post(f"{BASE}/targets/{target.id}/retry-generation", json={})

    assert response.status_code == 200
    assert response.json()["released_failures"][0]["current_status"] == "generation_failed"


def test_retry_generation_does_not_require_the_publisher_flag(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    target = _target()
    service = _install_service(
        monkeypatch, _fake_service(retry_generation=AsyncMock(return_value=target))
    )
    _install_target_report(monkeypatch, target)

    assert client.post(
        f"{BASE}/targets/{target.id}/retry-generation", json={}
    ).status_code == 200
    assert service.retry_generation.await_count == 1


def test_retry_generation_maps_a_retired_model_to_a_visible_409(monkeypatch):
    _install_service(
        monkeypatch,
        _fake_service(retry_generation=AsyncMock(
            side_effect=campaign_service.RetiredModelRefusal(
                [("content", "gemini", "gemini-2.5-flash")],
                what="retry this revision",
            )
        )),
    )
    response = client.post(f"{BASE}/targets/{uuid4()}/retry-generation", json={})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "retired_model"
    assert detail["retired"] == [
        {"role": "content", "provider": "gemini", "model": "gemini-2.5-flash"}
    ]


def test_retry_publication_captures_the_error_before_the_service_clears_it(monkeypatch):
    before = _target(
        status="publication_failed",
        publication_released_at=NOW,
        publication_version=2,
        publication_attempts=5,
        publication_next_attempt_at=None,
        publication_last_error="notion 502 bad gateway",
    )
    after = _target(
        id=before.id, campaign_id=before.campaign_id,
        toc_entry_id=before.toc_entry_id, source_job_id=before.source_job_id,
        status="publication_pending", publication_released_at=NOW,
        publication_version=2, publication_attempts=5,
        publication_next_attempt_at=None, publication_last_error=None,
    )
    service = _install_service(
        monkeypatch, _fake_service(retry_publication=AsyncMock(return_value=after))
    )
    monkeypatch.setattr(regen_api, "_load_target", AsyncMock(return_value=before))
    _install_target_report(monkeypatch, after)

    response = client.post(f"{BASE}/targets/{before.id}/retry-publication", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["previous_publication_error"] == "notion 502 bad gateway"
    assert body["previous_publication_attempts"] == 5
    assert body["target"]["publication_last_error"] is None
    assert body["target"]["status"] == "publication_pending"
    assert service.retry_publication.await_count == 1


def test_retry_publication_requires_the_publisher_flag(monkeypatch):
    monkeypatch.setattr(settings, "regeneration_publisher_enabled", False)
    service = _install_service(monkeypatch, _fake_service())

    response = client.post(f"{BASE}/targets/{uuid4()}/retry-publication", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "publisher_disabled"
    assert service.retry_publication.await_count == 0


def test_retry_publication_on_a_pending_target_is_idempotent(monkeypatch):
    target = _target(status="publication_pending", publication_released_at=NOW,
                     publication_version=2)
    _install_service(
        monkeypatch, _fake_service(retry_publication=AsyncMock(return_value=target))
    )
    monkeypatch.setattr(regen_api, "_load_target", AsyncMock(return_value=target))
    _install_target_report(monkeypatch, target)

    response = client.post(f"{BASE}/targets/{target.id}/retry-publication", json={})

    assert response.status_code == 200
    assert response.json()["target"]["status"] == "publication_pending"
    assert response.json()["previous_publication_error"] is None


def test_target_actions_map_illegal_state_to_409_and_missing_to_404(monkeypatch):
    _install_service(
        monkeypatch,
        _fake_service(
            abandon=AsyncMock(side_effect=campaign_service.IllegalTargetAction(
                "target is published — a delivered version cannot be abandoned"
            )),
            retry_publication=AsyncMock(
                side_effect=campaign_service.TargetNotFound("no such target")
            ),
        ),
    )
    monkeypatch.setattr(regen_api, "_load_target", AsyncMock(return_value=_target()))

    conflict = client.post(
        f"{BASE}/targets/{uuid4()}/abandon", json={"actor": "op", "reason": "x"}
    )
    missing = client.post(f"{BASE}/targets/{uuid4()}/retry-publication", json={})

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "illegal_target_state"
    assert "cannot be abandoned" in conflict.json()["detail"]["message"]
    assert missing.status_code == 404


def test_abandon_records_the_reason_and_returns_the_terminal_target(monkeypatch):
    target = _target(status="abandoned", terminal_at=NOW,
                     terminal_reason="abandoned by op: destination retired")
    service = _install_service(
        monkeypatch, _fake_service(abandon=AsyncMock(return_value=target))
    )
    _install_target_report(monkeypatch, target)

    response = client.post(
        f"{BASE}/targets/{target.id}/abandon",
        json={"actor": "op", "reason": "destination retired"},
    )

    assert response.status_code == 200
    assert response.json()["target"]["bucket"] == "abandoned"
    assert "destination retired" in response.json()["target"]["reason"]
    assert service.abandon.await_args.kwargs["reason"] == "destination retired"


# ═══════════════════════════ standing prohibitions ═══════════════════════


def test_the_router_never_touches_the_deadlocking_claim_primitive():
    """T9-1: `claim_target_publication` inverts the campaign→target lock order.
    Production claiming is `claim_next_publication`, inside the publisher.

    Checked over the parsed NAMES rather than the file text, so the module may
    still explain the prohibition in prose without tripping its own guard.
    """
    tree = ast.parse(inspect.getsource(regen_api))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "claim_target_publication" not in referenced
    assert "claim_next_publication" not in referenced


def test_the_router_exposes_no_prompt_set_or_publication_approval_route():
    paths = {route.path for route in regen_api.router.routes}
    assert not any("prompt" in path for path in paths)
    assert not any("publication-approval" in path or "approve-publication" in path
                   for path in paths)
    assert "/regeneration/targets/{target_id}/retry-publication" in paths


def test_retry_publication_maps_its_three_refusals_distinguishably(monkeypatch):
    """T9-2: the service raises three different refusals from this one route —
    wrong status, an abandon intent, and a cancelling campaign — and an
    operator must be able to tell them apart from the response alone."""
    target = _target(status="publication_failed", publication_released_at=NOW,
                     publication_version=2)
    monkeypatch.setattr(regen_api, "_load_target", AsyncMock(return_value=target))
    _install_target_report(monkeypatch, target)

    seen = []
    for error in (
        campaign_service.IllegalTargetAction(
            "target is 'published' — only a failed publication can be re-queued"
        ),
        campaign_service.IllegalTargetAction(
            "target is being abandoned — publication retry is refused"
        ),
        campaign_service.IllegalCampaignAction(
            "campaign is cancelling — publication retry is refused"
        ),
    ):
        _install_service(
            monkeypatch,
            _fake_service(retry_publication=AsyncMock(side_effect=error)),
        )
        response = client.post(f"{BASE}/targets/{target.id}/retry-publication",
                               json={})
        assert response.status_code == 409
        detail = response.json()["detail"]
        seen.append((detail["error"], detail["message"]))

    assert seen[0][0] == seen[1][0] == "illegal_target_state"
    assert seen[2][0] == "illegal_campaign_state"
    # Three refusals, three different sentences.
    assert len({message for _error, message in seen}) == 3


def test_a_mutation_never_consults_the_report_debounce(monkeypatch):
    """Binding 4: the debounce is a REPORT concern. A mutation's rollup is
    service-owned and must not be skipped, or an approval could return a
    campaign status that predates it."""
    campaign = _campaign(status="bulk_running", approved_at=NOW)
    service = _install_service(
        monkeypatch, _fake_service(approve_canary=AsyncMock(return_value=campaign))
    )
    _install_detail(monkeypatch, _detail(campaign))
    monkeypatch.setattr(regen_api, "_clock", lambda: 1000.0)

    for _ in range(3):
        assert client.post(
            f"{BASE}/campaigns/{campaign.id}/approve", json={}
        ).status_code == 200

    assert service.approve_canary.await_count == 3
    # The route itself took no rollup slot: only reports do.
    assert regen_api._ROLLUP_DEBOUNCE == {}
    assert service.roll_up.await_count == 0


def test_estimate_flags_acknowledgement_per_subject_not_campaign_wide(monkeypatch):
    """A campaign may span subjects, and the plan is built from each SOURCE
    JOB's flow, so the acknowledgement flag is per plan.

    Every deployed flow currently has the same shape, so this pins the
    RELATION — a plan claims an acknowledgement is needed exactly when it has
    broken edges — rather than a divergence that does not exist yet.
    """
    math = _source(subject="math-algebra")
    history = _source(subject="history")
    monkeypatch.setattr(
        discovery, "list_source_candidates",
        AsyncMock(return_value=[_candidate(math), _candidate(history)]),
    )
    monkeypatch.setattr(
        discovery, "preflight_notion_destinations", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        regen_api, "_estimate_regeneration",
        AsyncMock(return_value=SimpleNamespace(
            low_usd=1.0, high_usd=2.0, line_items=(), target_count=2,
            regenerated_phase_count=2, copied_phase_count=20,
            regenerated_extract_count=0, copied_extract_count=2,
            window_start=NOW - timedelta(days=30), window_end=NOW,
            notes=(), is_estimate=True, has_unpriced_lines=False,
        )),
    )

    response = client.post(
        f"{BASE}/estimate",
        json=_estimate_body(
            selected_phases=["boss-arena"],
            excluded_affected_phases=["reflection"],
            exclusion_acknowledged=False,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["acknowledgement_required"] is True
    by_subject = {plan["subject"]: plan for plan in body["phase_plans"]}
    assert set(by_subject) == {"math-algebra", "history"}
    for plan in by_subject.values():
        # Per-plan, and consistent with that plan's own edges.
        assert plan["acknowledgement_required"] == bool(
            plan["broken_dependency_edges"]
        )


def test_campaign_list_refuses_an_unknown_status_filter():
    response = client.get(f"{BASE}/campaigns", params={"status": "not-a-status"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_campaign_status"
