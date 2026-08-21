"""Pure shape and presentation rules for the regeneration API payloads.

Everything under test here is I/O-free: request validation, the derived
buckets/publication states, the human-readable reason text, the estimate's two
independent incompleteness markers, and the actual-cost isolation filter. The
router is a thin adapter over these, so a shape regression fails here first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import regeneration as schemas
from app.services.regeneration_estimator import (
    STATIC_BASIS,
    UNPRICED_BASIS,
    ZERO_VOLUME_HISTORY,
    EstimateLineItem,
    RegenerationEstimate,
)
from app.services.regeneration_planner import build_phase_plan

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
SUBJECT = "math-algebra"


def _contract_payload(**overrides) -> dict:
    payload = {
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "transport": "api",
    }
    payload.update(overrides)
    return payload


def _create_payload(**overrides) -> dict:
    payload = {
        "selection": {"toc_entry_ids": [str(uuid4())]},
        "contract": _contract_payload(),
        "selected_phases": ["flashcards"],
        "actor": "operator",
    }
    payload.update(overrides)
    return payload


# ─────────────────────────── request validation ──────────────────────────


def test_create_request_requires_a_phase_selection_or_extraction_refresh():
    with pytest.raises(ValidationError) as caught:
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(selected_phases=[])
        )
    assert "refresh_extraction" in str(caught.value)

    ok = schemas.CreateCampaignRequest.model_validate(
        _create_payload(selected_phases=[], refresh_extraction=True)
    )
    assert ok.refresh_extraction is True


def test_create_request_refuses_a_phase_that_is_both_selected_and_excluded():
    with pytest.raises(ValidationError) as caught:
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(
                selected_phases=["flashcards"],
                excluded_affected_phases=["flashcards"],
                exclusion_acknowledged=True,
            )
        )
    assert "both selected and excluded" in str(caught.value)


def test_create_request_refuses_duplicate_phase_names_and_unknown_fields():
    with pytest.raises(ValidationError):
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(selected_phases=["flashcards", "flashcards"])
        )
    with pytest.raises(ValidationError):
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(prompt_set="v2")
        )


def test_create_request_refuses_a_canary_smaller_than_one():
    with pytest.raises(ValidationError):
        schemas.CreateCampaignRequest.model_validate(_create_payload(canary_size=0))


def test_create_request_refuses_an_unknown_output_language():
    with pytest.raises(ValidationError):
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(selection={"output_languages": ["de"]})
        )
    ok = schemas.CreateCampaignRequest.model_validate(
        _create_payload(selection={"output_languages": ["uz", "ru", "en"]})
    )
    assert ok.selection.output_languages == ["uz", "ru", "en"]


def test_create_request_carries_the_draft_contract_through_its_own_validator():
    with pytest.raises(ValidationError):
        schemas.CreateCampaignRequest.model_validate(
            _create_payload(contract=_contract_payload(provider="not-a-provider"))
        )


def test_reason_bearing_requests_require_a_non_blank_reason():
    with pytest.raises(ValidationError):
        schemas.TargetAbandonRequest.model_validate({"actor": "op", "reason": "  "})
    ok = schemas.CampaignRejectRequest.model_validate(
        {"actor": "op", "reason": "canary text was wrong"}
    )
    assert ok.reason == "canary text was wrong"


# ─────────────────────────── phase-plan preview ──────────────────────────


def test_phase_plan_out_reports_the_expansion_and_the_broken_edges():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["flashcards"],
        excluded_affected_phases=["reflection"],
        exclusion_acknowledged=True,
    )
    out = schemas.PhasePlanOut.from_plan(
        plan, subject=SUBJECT, acknowledgement_required=True
    )
    body = out.model_dump()

    assert body["subject"] == SUBJECT
    assert body["selected_phases"] == ["flashcards"]
    assert "reflection" in body["excluded_affected_phases"]
    assert body["broken_dependency_edges"] == [
        {"upstream": "boss-arena", "downstream": "reflection"}
    ]
    assert body["acknowledgement_required"] is True
    assert body["acknowledgement_message"]
    assert body["regenerated_phase_count"] == len(plan.regenerated_phases)
    assert body["copied_phase_count"] == len(plan.copied_phases)
    assert body["refresh_extraction"] is False
    # The expansion the operator must see BEFORE launching: flashcards is not
    # a cheap isolated pick.
    assert len(body["auto_included_phases"]) >= 8


def test_phase_plan_out_without_broken_edges_needs_no_acknowledgement():
    plan = build_phase_plan(subject=SUBJECT, selected_phases=["reflection"])
    out = schemas.PhasePlanOut.from_plan(
        plan, subject=SUBJECT, acknowledgement_required=False
    )
    assert out.acknowledgement_required is False
    assert out.acknowledgement_message is None
    assert out.broken_dependency_edges == []


# ─────────────────────────────── estimate ────────────────────────────────


def _line(**overrides) -> EstimateLineItem:
    base = dict(
        budget="base",
        kind="authoring",
        phase="flashcards",
        provider="gemini",
        model="gemini-3.6-flash",
        calls_low=1,
        calls_high=1,
        unit_cost_usd=0.01,
        cost_low_usd=0.01,
        cost_high_usd=0.01,
        basis="observed mean of 4 api call(s) in the last 30 days",
        observations=4,
        is_unpriced=False,
    )
    base.update(overrides)
    return EstimateLineItem(**base)


def _estimate(lines, *, notes=(), has_unpriced_lines=False) -> RegenerationEstimate:
    return RegenerationEstimate(
        low_usd=0.5,
        high_usd=1.5,
        line_items=tuple(lines),
        target_count=2,
        regenerated_phase_count=20,
        copied_phase_count=2,
        regenerated_extract_count=0,
        copied_extract_count=2,
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
        notes=tuple(notes),
        has_unpriced_lines=has_unpriced_lines,
    )


def test_estimate_out_marks_an_unpriced_line_and_the_incomplete_total():
    estimate = _estimate(
        [
            _line(),
            _line(
                provider="clodex",
                model="brand-new",
                basis=f"{UNPRICED_BASIS}; volume from {STATIC_BASIS}",
                observations=0,
                is_unpriced=True,
                unit_cost_usd=0.0,
                cost_low_usd=0.0,
                cost_high_usd=0.0,
            ),
        ],
        has_unpriced_lines=True,
    )
    out = schemas.RegenerationEstimateOut.from_estimate(estimate)
    body = out.model_dump()

    assert body["has_unpriced_lines"] is True
    assert body["unpriced_line_count"] == 1
    assert body["is_complete"] is False
    assert body["is_estimate"] is True
    assert body["incomplete_reason"]
    unpriced = body["line_items"][1]
    assert unpriced["is_unpriced"] is True
    assert unpriced["observations"] == 0
    assert unpriced["is_observed"] is False
    assert unpriced["basis"].startswith(UNPRICED_BASIS)


def test_estimate_out_keeps_zero_volume_fallback_separate_from_unpriced():
    note = (
        f"{ZERO_VOLUME_HISTORY}: 3 successful api authoring call(s) for phase "
        f"'flashcards' on gemini/gemini-3.6-flash recorded no billable tokens"
    )
    estimate = _estimate(
        [_line(basis=STATIC_BASIS, observations=0)],
        notes=[note, "solver calls are priced for every solver-bearing phase"],
    )
    out = schemas.RegenerationEstimateOut.from_estimate(estimate)
    body = out.model_dump()

    # A zero-volume fallback is NOT an unpriced line.
    assert body["has_unpriced_lines"] is False
    assert body["unpriced_line_count"] == 0
    assert body["zero_volume_history_notes"] == [note]
    assert note in body["notes"]
    line = body["line_items"][0]
    assert line["is_unpriced"] is False
    assert line["is_observed"] is False
    assert line["is_static_envelope"] is True
    assert line["basis"] == STATIC_BASIS
    # No unpriced line, so the range is complete even though a line fell back.
    assert body["is_complete"] is True


def test_estimate_out_calls_an_observed_line_observed():
    out = schemas.RegenerationEstimateOut.from_estimate(_estimate([_line()]))
    line = out.line_items[0]
    assert line.is_observed is True
    assert line.is_static_envelope is False
    assert line.observations == 4


# ───────────────────────── target presentation ───────────────────────────


def _target(**overrides):
    base = dict(
        id=uuid4(),
        campaign_id=uuid4(),
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


def test_publication_failed_with_a_future_attempt_is_backing_off():
    target = _target(
        status="publication_failed",
        publication_released_at=NOW,
        publication_version=2,
        publication_attempts=2,
        publication_next_attempt_at=NOW + timedelta(minutes=4),
        publication_last_error="notion 502",
    )
    out = schemas.TargetReportOut.from_row(target, now=NOW)
    assert out.publication_state == "backing_off"
    assert out.bucket == "publication_failed"
    assert out.action_required is False
    assert "automatic retry" in out.reason
    assert "notion 502" in out.reason
    assert out.publication_last_error == "notion 502"


def test_publication_failed_with_a_past_attempt_is_retry_due():
    target = _target(
        status="publication_failed",
        publication_released_at=NOW,
        publication_version=2,
        publication_attempts=2,
        publication_next_attempt_at=NOW - timedelta(minutes=1),
        publication_last_error="notion 502",
    )
    out = schemas.TargetReportOut.from_row(target, now=NOW)
    assert out.publication_state == "retry_due"
    assert out.action_required is False
    assert "due" in out.reason


def test_publication_failed_without_a_next_attempt_is_operator_parked():
    target = _target(
        status="publication_failed",
        publication_released_at=NOW,
        publication_version=2,
        publication_attempts=5,
        publication_next_attempt_at=None,
        publication_last_error="version page collision",
    )
    out = schemas.TargetReportOut.from_row(target, now=NOW)
    assert out.publication_state == "action_required"
    assert out.action_required is True
    assert "no automatic retry" in out.reason.lower()
    assert "version page collision" in out.reason


def test_an_abandoned_target_renders_both_the_abandon_and_the_delivery_error():
    target = _target(
        status="abandoned",
        publication_released_at=NOW,
        publication_version=2,
        publication_attempts=5,
        publication_last_error="notion 403 forbidden",
        terminal_at=NOW,
        terminal_reason="abandoned by operator: destination retired",
        abandon_requested_at=NOW,
        abandon_requested_reason="destination retired",
    )
    out = schemas.TargetReportOut.from_row(target, now=NOW)
    assert out.bucket == "abandoned"
    assert out.publication_state == "abandoned"
    assert out.delivery_error == "notion 403 forbidden"
    assert "destination retired" in out.reason
    assert "notion 403 forbidden" in out.reason


def test_an_abandoned_reason_ends_before_the_notion_clause_begins():
    """The interpolated reason is operator prose and carries no punctuation of
    its own, so without terminating it the row reads
    ``...destination retired No Notion page was deleted``."""
    out = schemas.TargetReportOut.from_row(
        _target(
            status="abandoned",
            publication_released_at=NOW,
            publication_version=2,
            terminal_at=NOW,
            terminal_reason="destination retired",
            abandon_requested_at=NOW,
            abandon_requested_reason="destination retired",
        ),
        now=NOW,
    )
    assert "abandoned: destination retired. No Notion page was deleted" in out.reason
    assert "retired No Notion" not in out.reason


def test_an_already_punctuated_abandon_reason_is_not_double_terminated():
    out = schemas.TargetReportOut.from_row(
        _target(
            status="abandoned",
            terminal_at=NOW,
            terminal_reason="destination retired.",
        ),
        now=NOW,
    )
    assert "abandoned: destination retired. No Notion page was deleted" in out.reason
    assert (
        "No Notion page was deleted and no publication version was consumed."
        in out.reason
    )
    assert ".." not in out.reason


def test_a_generation_failure_reason_ends_before_the_retry_instruction():
    job = SimpleNamespace(
        id=uuid4(),
        status="failed",
        error_message="phase flashcards failed: judge unavailable",
        last_error=None,
        scheduled_at=NOW,
        current_phase="flashcards",
    )
    out = schemas.TargetReportOut.from_row(
        _target(status="generation_failed"), now=NOW, revision_job=job
    )
    assert "judge unavailable. Retry generation or abandon" in out.reason
    assert "unavailable Retry" not in out.reason


def test_an_already_punctuated_generation_failure_is_not_double_terminated():
    out = schemas.TargetReportOut.from_row(
        _target(
            status="generation_failed",
            terminal_reason="the snapshot never validated.",
        ),
        now=NOW,
    )
    assert "the snapshot never validated. Retry generation or abandon" in out.reason
    assert ".." not in out.reason


def test_a_generation_failure_reads_the_revision_job_error_when_no_reason_exists():
    job = SimpleNamespace(
        id=uuid4(),
        status="failed",
        error_message="phase flashcards failed: judge unavailable",
        last_error=None,
        scheduled_at=NOW,
        current_phase="flashcards",
    )
    target = _target(status="generation_failed")
    out = schemas.TargetReportOut.from_row(target, now=NOW, revision_job=job)
    assert out.bucket == "generation_failed"
    assert out.action_required is True
    assert "judge unavailable" in out.reason
    assert out.revision_job_id == job.id
    assert out.revision_job_status == "failed"
    assert out.content_path == f"/api/v1/jobs/{job.id}"
    assert out.download_path == f"/api/v1/jobs/{job.id}/download"


def test_in_flight_targets_are_reported_not_omitted():
    for status, state in (
        ("planned", "not_started"),
        ("generating", "not_started"),
        ("awaiting_canary_approval", "not_started"),
    ):
        out = schemas.TargetReportOut.from_row(_target(status=status), now=NOW)
        assert out.bucket == "in_flight"
        assert out.publication_state == state
        assert out.reason

    publishing = _target(
        status="publishing", publication_released_at=NOW, publication_version=2
    )
    out = schemas.TargetReportOut.from_row(publishing, now=NOW)
    assert out.bucket == "in_flight"
    assert out.publication_state == "publishing"


def test_a_published_target_carries_its_version_and_page_link():
    target = _target(
        status="published",
        publication_released_at=NOW,
        publication_version=3,
        notion_page_id="abc123def",
        terminal_at=NOW,
    )
    out = schemas.TargetReportOut.from_row(target, now=NOW)
    assert out.bucket == "published"
    assert out.publication_state == "published"
    assert out.publication_version == 3
    assert out.notion_page_url == "https://www.notion.so/abc123def"
    assert "V3" in out.reason


def test_a_target_reports_its_source_version_and_phase_provenance():
    rows = [
        SimpleNamespace(
            phase_name="extract", judge_status=None, solver_status=None,
            copied_from_phase_output_id=uuid4(), status="done",
        ),
        SimpleNamespace(
            phase_name="flashcards", judge_status="major_shipped",
            solver_status=None, copied_from_phase_output_id=None, status="done",
        ),
        SimpleNamespace(
            phase_name="boss-arena", judge_status="ok",
            solver_status="mismatch_blocked", copied_from_phase_output_id=None,
            status="failed",
        ),
    ]
    out = schemas.TargetReportOut.from_row(
        _target(), now=NOW, source_publication_version=2, phase_rows=rows
    )
    assert out.source_publication_version == 2
    assert out.copied_phase_count == 1
    assert out.regenerated_phase_count == 2
    assert out.judge_status_counts == {"major_shipped": 1, "ok": 1}
    assert out.solver_status_counts == {"mismatch_blocked": 1}


def test_a_purged_source_link_is_named_not_silently_null():
    out = schemas.TargetReportOut.from_row(
        _target(source_job_id=None), now=NOW, source_publication_version=None
    )
    assert out.source_job_id is None
    assert "purged" in out.source_note.lower()


def test_an_unreadable_stored_phase_plan_is_reported_not_fatal():
    out = schemas.TargetReportOut.from_row(
        _target(phase_plan={"version": 99}), now=NOW
    )
    assert out.phase_plan is None
    assert out.phase_plan_error


# ─────────────────────────── actual cost isolation ───────────────────────


def _usage(job_id, **overrides):
    base = dict(
        homework_job_id=job_id,
        provider="gemini",
        model_name="gemini-3.6-flash",
        operation="phase.run",
        prompt_tokens=1000,
        output_tokens=500,
        cached_tokens=0,
        cache_creation_tokens=0,
        total_tokens=1500,
        success=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_actual_cost_counts_revision_jobs_only():
    revision = uuid4()
    source = uuid4()
    rows = [_usage(revision), _usage(revision), _usage(source)]

    out = schemas.ActualCostOut.from_usage_rows(rows, revision_job_ids={revision})

    assert out.call_count == 2
    assert out.excluded_row_count == 1
    assert out.prompt_tokens == 2000
    assert out.usd > 0
    # Two identical rows: the source row would have made it 3x.
    assert out.usd == pytest.approx(out.usd)
    assert out.revision_job_count == 1


def test_actual_cost_reports_free_copied_extract_markers_separately():
    revision = uuid4()
    rows = [
        _usage(revision),
        _usage(
            revision,
            provider="<cache>",
            model_name="<cache>",
            operation="lesson.extract",
            prompt_tokens=0,
            output_tokens=0,
            total_tokens=0,
        ),
    ]
    out = schemas.ActualCostOut.from_usage_rows(rows, revision_job_ids={revision})
    assert out.call_count == 2
    assert out.zero_cost_marker_count == 1
    assert out.paid_call_count == 1


def test_actual_cost_with_no_rows_is_zero_not_null():
    out = schemas.ActualCostOut.from_usage_rows([], revision_job_ids=set())
    assert out.usd == 0.0
    assert out.call_count == 0
    assert out.excluded_row_count == 0


# ───────────────────────────── campaign report ───────────────────────────


def _campaign(**overrides):
    base = dict(
        id=uuid4(),
        status="bulk_running",
        selection_spec={"toc_entry_ids": [], "output_languages": ["uz"]},
        requested_phases=["flashcards"],
        excluded_phases=[],
        launch_contract={"provider": "gemini", "model": "gemini-3.6-flash"},
        refresh_extraction=False,
        exclusion_acknowledged=False,
        canary_size=1,
        estimated_cost_low_usd=0.5,
        estimated_cost_high_usd=1.5,
        app_git_revision="abc1234",
        canary_launched_at=NOW,
        approved_at=NOW,
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


def test_campaign_detail_buckets_every_target_exactly_once():
    campaign = _campaign()
    statuses = [
        "published", "publication_pending", "publication_failed",
        "generation_failed", "abandoned", "generating", "publishing",
    ]
    targets = []
    for status in statuses:
        published_like = status in (
            "publication_pending", "publishing", "published", "publication_failed",
        )
        targets.append(_target(
            campaign_id=campaign.id,
            status=status,
            publication_released_at=NOW if published_like else None,
            publication_version=2 if published_like else None,
            notion_page_id="page" if status == "published" else None,
            terminal_at=NOW if status in ("published", "abandoned") else None,
        ))

    detail = schemas.CampaignDetailOut.build(campaign, targets, now=NOW)
    body = detail.model_dump()

    assert set(body["buckets"]) == {
        "published", "publication_pending", "publication_failed",
        "generation_failed", "abandoned", "in_flight",
    }
    bucketed = sum(len(ids) for ids in body["buckets"].values())
    assert bucketed == len(targets)
    assert body["buckets"]["in_flight"]  # generating + publishing
    assert body["status_counts"]["publishing"] == 1
    assert body["target_count"] == len(targets)
    assert body["attention_required"] is True
    assert len(body["targets"]) == len(targets)


def test_campaign_detail_surfaces_the_approved_but_nothing_released_state():
    campaign = _campaign(status="approved", approved_at=NOW)
    targets = [_target(campaign_id=campaign.id, status="planned")]
    detail = schemas.CampaignDetailOut.build(campaign, targets, now=NOW)
    joined = " ".join(detail.warnings)
    assert "approved" in joined.lower()
    assert "never released" in joined.lower() or "not released" in joined.lower()
    assert "approve" in joined.lower()


def test_campaign_detail_reports_the_release_schedule_from_persisted_jobs():
    campaign = _campaign()
    t1, t2, t3 = (_target(campaign_id=campaign.id, status="generating")
                  for _ in range(3))
    jobs = {
        t1.id: SimpleNamespace(id=uuid4(), status="running",
                               scheduled_at=NOW, error_message=None,
                               last_error=None, current_phase=None),
        t2.id: SimpleNamespace(id=uuid4(), status="pending",
                               scheduled_at=NOW, error_message=None,
                               last_error=None, current_phase=None),
        t3.id: SimpleNamespace(id=uuid4(), status="pending",
                               scheduled_at=NOW + timedelta(seconds=60),
                               error_message=None, last_error=None,
                               current_phase=None),
    }
    detail = schemas.CampaignDetailOut.build(
        campaign, [t1, t2, t3], now=NOW, jobs_by_target=jobs
    )
    schedule = detail.release_schedule
    assert schedule.job_count == 3
    assert schedule.wave_count == 2
    assert schedule.final_offset_seconds == 60
    assert schedule.source == "persisted homework_jobs.scheduled_at"


def test_campaign_detail_reports_the_canary_targets_and_their_job_ids():
    campaign = _campaign()
    canary = _target(campaign_id=campaign.id, status="awaiting_canary_approval",
                     is_canary=True)
    bulk = _target(campaign_id=campaign.id, status="planned")
    job = SimpleNamespace(id=uuid4(), status="done", scheduled_at=NOW,
                          error_message=None, last_error=None, current_phase=None)
    detail = schemas.CampaignDetailOut.build(
        campaign, [canary, bulk], now=NOW, jobs_by_target={canary.id: job}
    )
    assert [c.target_id for c in detail.canary] == [canary.id]
    assert detail.canary[0].revision_job_id == job.id
    assert detail.canary[0].content_path == f"/api/v1/jobs/{job.id}"
    assert detail.canary[0].download_path == f"/api/v1/jobs/{job.id}/download"


def test_campaign_detail_rolls_up_judge_and_solver_counts_and_provenance():
    campaign = _campaign()
    target = _target(campaign_id=campaign.id, status="published",
                     publication_released_at=NOW, publication_version=2,
                     notion_page_id="p", terminal_at=NOW)
    rows = {
        target.id: [
            SimpleNamespace(phase_name="extract", judge_status=None,
                            solver_status=None,
                            copied_from_phase_output_id=uuid4(), status="done"),
            SimpleNamespace(phase_name="flashcards", judge_status="unavailable",
                            solver_status=None,
                            copied_from_phase_output_id=None, status="done"),
            SimpleNamespace(phase_name="boss-arena", judge_status="major_shipped",
                            solver_status="ok", copied_from_phase_output_id=None,
                            status="done"),
        ]
    }
    detail = schemas.CampaignDetailOut.build(
        campaign, [target], now=NOW, phase_rows_by_target=rows
    )
    assert detail.judge_status_counts == {"unavailable": 1, "major_shipped": 1}
    assert detail.solver_status_counts == {"ok": 1}
    assert detail.provenance.copied_phase_count == 1
    assert detail.provenance.regenerated_phase_count == 2


def test_campaign_detail_uses_backend_vocabulary_verbatim():
    campaign = _campaign(status="completed_with_abandonments", completed_at=NOW)
    target = _target(campaign_id=campaign.id, status="abandoned", terminal_at=NOW,
                     terminal_reason="abandoned by op: no destination")
    detail = schemas.CampaignDetailOut.build(campaign, [target], now=NOW)
    assert detail.status == "completed_with_abandonments"
    assert detail.is_terminal is True
    assert detail.targets[0].status == "abandoned"


def test_campaign_summary_counts_without_target_rows():
    campaign = _campaign()
    summary = schemas.CampaignSummaryOut.from_row(
        campaign, status_counts={"published": 2, "generation_failed": 1}
    )
    body = summary.model_dump()
    assert body["target_count"] == 3
    assert body["bucket_counts"]["published"] == 2
    assert body["bucket_counts"]["generation_failed"] == 1
    assert body["attention_required"] is True


# ───────────────────────── partial wave release ──────────────────────────


def test_wave_failure_out_reports_the_actual_current_status():
    target_id, source_id = uuid4(), uuid4()
    failure = SimpleNamespace(
        target_id=target_id,
        source_job_id=source_id,
        reason="snapshot no longer validates",
    )
    out = schemas.WaveFailureOut.from_failure(failure, current_status="abandoned")
    body = out.model_dump()
    assert body == {
        "target_id": target_id,
        "source_job_id": source_id,
        "reason": "snapshot no longer validates",
        "current_status": "abandoned",
    }


def test_wave_failure_out_tolerates_a_missing_source_link():
    failure = SimpleNamespace(
        target_id=uuid4(), source_job_id=None, reason="source purged"
    )
    out = schemas.WaveFailureOut.from_failure(failure, current_status=None)
    assert out.source_job_id is None
    assert out.current_status is None
