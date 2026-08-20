"""Model / contract / config surface for versioned homework regeneration.

Pure metadata + pydantic assertions — no database. The real database behavior
is proven in ``tests/migrations/test_regeneration_schema.py`` (DDL) and
``tests/integration/test_regeneration_constraints.py`` (constraints under
concurrency); this file pins the ORM declaration so a model edit that silently
drops a constraint name or an ``ondelete`` cannot pass.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import RegenerationCampaign, RegenerationTarget
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput
from app.schemas.regeneration_contract import LaunchContract

CAMPAIGN_STATUSES = (
    "draft",
    "canary_running",
    "awaiting_canary_approval",
    "approved",
    "bulk_running",
    "attention_required",
    "completed",
    "completed_with_abandonments",
    "rejected",
    "cancelled",
)

TARGET_STATUSES = (
    "planned",
    "generating",
    "awaiting_canary_approval",
    "publication_pending",
    "publishing",
    "published",
    "generation_failed",
    "publication_failed",
    "abandoned",
)


def _check_constraints(model) -> dict[str, str]:
    from sqlalchemy import CheckConstraint

    return {
        c.name: str(c.sqltext)
        for c in model.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name
    }


def _ondelete(model, column: str) -> str | None:
    fks = list(model.__table__.c[column].foreign_keys)
    assert len(fks) == 1, f"{column} should have exactly one FK, got {fks}"
    return fks[0].ondelete


# ── campaign ────────────────────────────────────────────────────────────────


def test_campaign_table_and_columns():
    cols = RegenerationCampaign.__table__.c
    assert RegenerationCampaign.__tablename__ == "regeneration_campaigns"
    for name in (
        "status",
        "requested_phases",
        "excluded_phases",
        "selection_spec",
        "launch_contract",
        "refresh_extraction",
        "exclusion_acknowledged",
        "canary_size",
        "estimated_cost_low_usd",
        "estimated_cost_high_usd",
        "app_git_revision",
        "canary_launched_at",
        "approved_at",
        "rejected_at",
        "cancel_requested_at",
        "completed_at",
        "rejected_reason",
        "cancel_requested_reason",
        "created_at",
        "updated_at",
    ):
        assert name in cols, f"regeneration_campaigns.{name} missing"
    # The immutable spec fields are the campaign's identity — never nullable.
    for name in ("requested_phases", "excluded_phases", "selection_spec", "launch_contract"):
        assert cols[name].nullable is False, f"{name} must be NOT NULL"
    for name in ("status", "refresh_extraction", "exclusion_acknowledged", "canary_size"):
        assert cols[name].nullable is False
    # Audit trail is written as it happens; every timestamp/reason starts NULL.
    for name in ("approved_at", "rejected_at", "completed_at", "rejected_reason"):
        assert cols[name].nullable is True


def test_campaign_status_check_lists_every_lifecycle_state():
    checks = _check_constraints(RegenerationCampaign)
    assert "ck_regeneration_campaigns_status" in checks
    sqltext = checks["ck_regeneration_campaigns_status"]
    for status in CAMPAIGN_STATUSES:
        assert f"'{status}'" in sqltext, f"campaign status {status!r} not accepted"


# ── target ──────────────────────────────────────────────────────────────────


def test_target_table_and_columns():
    cols = RegenerationTarget.__table__.c
    assert RegenerationTarget.__tablename__ == "regeneration_targets"
    for name in (
        "campaign_id",
        "toc_entry_id",
        "output_language",
        "source_job_id",
        "is_canary",
        "phase_plan",
        "status",
        "publication_released_at",
        "publication_version",
        "notion_page_id",
        "publication_claim_token",
        "publication_claimed_at",
        "publication_attempts",
        "publication_next_attempt_at",
        "publication_last_error",
        "terminal_at",
        "terminal_reason",
        "abandon_requested_at",
        "abandon_requested_reason",
    ):
        assert name in cols, f"regeneration_targets.{name} missing"
    assert cols["phase_plan"].nullable is False
    assert cols["publication_attempts"].nullable is False
    assert cols["terminal_at"].nullable is True
    # Deliberately ABSENT: the target must not carry its own revision_job_id.
    # The authoritative link is the unique homework_jobs.regeneration_target_id,
    # so the pair of foreign keys can never disagree.
    assert "revision_job_id" not in cols


def test_target_status_check_lists_every_state():
    checks = _check_constraints(RegenerationTarget)
    assert "ck_regeneration_targets_status" in checks
    sqltext = checks["ck_regeneration_targets_status"]
    for status in TARGET_STATUSES:
        assert f"'{status}'" in sqltext, f"target status {status!r} not accepted"


def test_target_terminality_and_publication_checks_exist():
    checks = _check_constraints(RegenerationTarget)
    for name in (
        "ck_regeneration_targets_output_language",
        "ck_regeneration_targets_terminal_at",
        "ck_regeneration_targets_published_complete",
        "ck_regeneration_targets_publication_released",
        "ck_regeneration_targets_publication_attempts",
    ):
        assert name in checks, f"{name} missing from regeneration_targets"
    published = checks["ck_regeneration_targets_published_complete"]
    for required in ("publication_version", "notion_page_id", "publication_released_at", "terminal_at"):
        assert required in published


def test_target_uniqueness_and_partial_active_lineage_index():
    from sqlalchemy import UniqueConstraint

    uniques = {
        c.name: [col.name for col in c.columns]
        for c in RegenerationTarget.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert uniques.get("uq_regeneration_targets_campaign_toc_language") == [
        "campaign_id",
        "toc_entry_id",
        "output_language",
    ]

    idx = {i.name: i for i in RegenerationTarget.__table__.indexes}
    lineage = idx["uq_regeneration_targets_active_lineage"]
    assert lineage.unique is True
    assert [c.name for c in lineage.columns] == ["toc_entry_id", "output_language"]
    where = str(lineage.dialect_options["postgresql"]["where"])
    assert "terminal_at IS NULL" in where

    version = idx["uq_regeneration_targets_publication_version"]
    assert version.unique is True
    assert [c.name for c in version.columns] == [
        "toc_entry_id",
        "output_language",
        "publication_version",
    ]
    assert "publication_version IS NOT NULL" in str(
        version.dialect_options["postgresql"]["where"]
    )


def test_target_foreign_keys_are_restrictive():
    # Audit history must survive: no implicit cascade may erase a target (and
    # with it a consumed publication version) when a TOC row or a campaign is
    # deleted.
    assert _ondelete(RegenerationTarget, "toc_entry_id") == "RESTRICT"
    assert _ondelete(RegenerationTarget, "campaign_id") == "RESTRICT"


def test_target_source_job_link_is_set_null_not_restrict():
    """Spec §8.3 splits source-deletion protection across TWO foreign keys.

    The *restrictive* half is ``homework_jobs.revision_of_job_id``: while a live
    revision child exists, deleting its source fails cleanly. The target's
    historical source link is the *nullable* half — after an explicitly ordered
    child-first purge removes the revision, deleting the source must succeed and
    leave the reporting row alive with a null source.

    RESTRICT on both sides permanently blocks that documented purge: the target
    keeps referencing the source forever, so ``source_job_id`` could never
    actually become null and the nullable column would be unreachable.
    """
    assert _ondelete(RegenerationTarget, "source_job_id") == "SET NULL"
    assert RegenerationTarget.__table__.c["source_job_id"].nullable is True
    # The restrictive half must stay restrictive, or nothing guards a live
    # revision against losing its source.
    assert _ondelete(HomeworkJob, "revision_of_job_id") == "RESTRICT"


def test_target_revision_job_is_read_through_the_unique_job_link():
    rel = RegenerationTarget.__mapper__.relationships["revision_job"]
    assert rel.mapper.class_ is HomeworkJob
    assert rel.uselist is False
    assert {c.name for c in rel.local_remote_pairs[0]} <= {"id", "regeneration_target_id"}
    assert "regeneration_target_id" in {
        c.name for pair in rel.local_remote_pairs for c in pair
    }
    campaign_rel = RegenerationTarget.__mapper__.relationships["campaign"]
    assert campaign_rel.mapper.class_ is RegenerationCampaign
    assert "targets" in RegenerationCampaign.__mapper__.relationships


# ── homework_jobs / phase_outputs ───────────────────────────────────────────


def test_homework_job_revision_columns_and_checks():
    from sqlalchemy import UniqueConstraint

    cols = HomeworkJob.__table__.c
    assert cols["revision_of_job_id"].nullable is True
    assert cols["regeneration_target_id"].nullable is True
    assert _ondelete(HomeworkJob, "revision_of_job_id") == "RESTRICT"
    assert _ondelete(HomeworkJob, "regeneration_target_id") == "RESTRICT"

    checks = _check_constraints(HomeworkJob)
    assert "ck_homework_jobs_revision_pair" in checks
    assert "ck_homework_jobs_revision_no_batch" in checks
    pair = checks["ck_homework_jobs_revision_pair"]
    assert "revision_of_job_id" in pair and "regeneration_target_id" in pair
    assert "batch_id" in checks["ck_homework_jobs_revision_no_batch"]

    uniques = {
        c.name: [col.name for col in c.columns]
        for c in HomeworkJob.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert uniques.get("uq_homework_jobs_regeneration_target_id") == [
        "regeneration_target_id"
    ]


def test_phase_output_copy_provenance_column():
    cols = PhaseOutput.__table__.c
    assert "copied_from_phase_output_id" in cols
    assert cols["copied_from_phase_output_id"].nullable is True
    assert _ondelete(PhaseOutput, "copied_from_phase_output_id") == "RESTRICT"


def test_phase_output_judge_status_comment_documents_refused():
    """Documentation parity only: pipeline.py already writes 'refused' and
    app/schemas/job.py already documents it, so the model comment must too."""
    import app.models.phase_output as module

    lines = inspect.getsource(module).splitlines()
    start = next(i for i, line in enumerate(lines) if "LLM-judge verdict" in line)
    # The comment may wrap; read the whole contiguous block up to the column.
    block = []
    for line in lines[start:]:
        if not line.strip().startswith("#"):
            break
        block.append(line.strip())
    assert "judge_status" in lines[start + len(block)]
    assert "refused" in " ".join(block), block


# ── LaunchContract ──────────────────────────────────────────────────────────


def _contract(**overrides):
    base = dict(
        provider="gemini",
        model="gemini-3.5-flash",
        transport="api",
        output_language="uz",
    )
    base.update(overrides)
    return LaunchContract(**base)


def test_launch_contract_defaults_and_immutability():
    c = _contract()
    assert c.extract_transport == "inherit"
    assert c.judge_transport == "inherit"
    assert c.solver_transport == "inherit"
    assert c.session_limit_strategy == "inherit"
    assert c.solver_enabled is True
    with pytest.raises(ValidationError):
        c.provider = "claude"  # frozen — the approved contract never mutates


def test_launch_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        _contract(kind="teacher_material")


def test_launch_contract_serializes_to_json_safe_dict():
    payload = _contract().model_dump(mode="json")
    assert payload["provider"] == "gemini"
    assert payload["output_language"] == "uz"
    # Round-trips out of the campaign JSONB column unchanged.
    assert LaunchContract(**payload) == _contract()


def test_launch_contract_rejects_offmanifest_provider_model():
    with pytest.raises(ValidationError) as exc:
        _contract(model="gemini-2.5-flash")  # retired 2026-08-03
    assert "gemini-2.5-flash" in str(exc.value)
    with pytest.raises(ValidationError):
        _contract(provider="nope")


def test_launch_contract_enforces_transport_rules():
    # transport=api requires an explicit model (the explicit-model rule).
    with pytest.raises(ValidationError):
        _contract(model=None)
    # gemini-3.5-flash is api-only — cli must be rejected for it.
    with pytest.raises(ValidationError):
        _contract(transport="cli")
    # codex has no api transport at all.
    with pytest.raises(ValidationError):
        _contract(provider="codex", model="gpt-5.5", transport="api")
    # ...but is fine over cli.
    assert _contract(provider="codex", model="gpt-5.5", transport="cli").transport == "cli"


def test_launch_contract_validates_role_selections():
    ok = _contract(
        extract_provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        solver_provider="gemini",
        solver_model="gemini-3.5-flash",
    )
    assert ok.extract_model == "gemini-3.5-flash-lite"
    with pytest.raises(ValidationError):
        _contract(judge_provider="gemini", judge_model="not-a-model")
    with pytest.raises(ValidationError):
        _contract(extract_transport="bogus")
    # extract cannot run on an api-only provider (vision fallbacks need a CLI).
    with pytest.raises(ValidationError):
        _contract(extract_provider="clodex", extract_model="gpt-5.5")
    # A role whose resolved transport is api must carry an explicit model.
    with pytest.raises(ValidationError):
        _contract(judge_provider="gemini", judge_model=None, judge_transport="api")


def test_launch_contract_validates_language_and_session_strategy():
    with pytest.raises(ValidationError):
        _contract(output_language="fr")
    with pytest.raises(ValidationError):
        _contract(output_language=None)
    with pytest.raises(ValidationError):
        _contract(session_limit_strategy="explode")
    assert _contract(session_limit_strategy="switch").session_limit_strategy == "switch"


# ── config ──────────────────────────────────────────────────────────────────


def test_regeneration_settings_defaults():
    fields = Settings.model_fields
    # Both flags ship OFF — no task in this feature turns them on.
    assert fields["regeneration_enabled"].default is False
    assert fields["regeneration_publisher_enabled"].default is False
    assert fields["regeneration_publisher_interval_seconds"].default == 30
    assert fields["regeneration_publisher_lease_seconds"].default == 300
    assert fields["regeneration_publisher_max_attempts"].default == 5
    assert fields["regeneration_publisher_backoff_base_seconds"].default == 60
    assert fields["regeneration_publisher_backoff_max_seconds"].default == 3600
    assert fields["regeneration_launch_wave_size"].default == 4
    assert fields["regeneration_launch_wave_interval_seconds"].default == 60


def test_regeneration_wave_knobs_accept_zero_as_a_kill_switch():
    s = Settings(regeneration_launch_wave_size=0, regeneration_launch_wave_interval_seconds=0)
    assert s.regeneration_launch_wave_size == 0
    assert s.regeneration_launch_wave_interval_seconds == 0
    with pytest.raises(ValidationError):
        Settings(regeneration_launch_wave_size=-1)
    with pytest.raises(ValidationError):
        Settings(regeneration_publisher_max_attempts=0)
