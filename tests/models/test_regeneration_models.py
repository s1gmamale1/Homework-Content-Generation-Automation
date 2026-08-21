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
from app.schemas.regeneration_contract import (
    LaunchContract,
    LaunchDefaultsSnapshot,
    ResolvedLaunchContract,
    ensure_resolved,
    resolve_launch_contract,
)
from app.models.launch_defaults import LaunchDefaults
from app.services.agent_models import (
    default_model,
    resolve_role_transport,
    resolve_session_limit_strategy,
)

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


def test_target_phase_plan_is_mapped_as_a_dict_not_a_list():
    """`phase_plan` holds `RegenerationPhasePlan.to_json()` — an OBJECT.

    A bare phase-name list carries none of what the later lanes read: Task 6
    needs `copied_phases` told apart from `regenerated_phases`, and Task 9
    renders the expanded/excluded plan, the broken dependency edges and
    `refresh_extraction`. Pinning `Mapped[dict]` is what stops a `Mapped[list]`
    regression from type-checking a flat list back into the column.
    """
    import typing

    assert typing.get_args(RegenerationTarget.__annotations__["phase_plan"]) == (dict,)


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


# ── guided regeneration: campaign version + reviewed destination ────────────
# Two different columns share the name `publication_version`. The one asserted
# below is on `regeneration_campaigns` — the version the WHOLE campaign
# publishes. `regeneration_targets.publication_version` already existed and is
# the per-lesson allocation; every assertion here names its table explicitly so
# a future edit cannot satisfy one by touching the other.


def test_new_campaign_version_and_target_destination_columns_are_declared():
    from sqlalchemy import Integer, String, Text

    campaign_cols = RegenerationCampaign.__table__.c
    assert "publication_version" in campaign_cols, (
        "regeneration_campaigns.publication_version missing"
    )
    assert campaign_cols["publication_version"].nullable is True
    assert isinstance(campaign_cols["publication_version"].type, Integer)

    target_cols = RegenerationTarget.__table__.c
    # Nullable on purpose: historical targets predate the guided wizard and must
    # not be assigned a destination decision nobody made. Task 4 is what makes
    # these mandatory on a NEW service-created campaign.
    for name, sqltype, length in (
        ("notion_container_policy", String, 16),
        ("reviewed_notion_container_page_id", String, 128),
        ("notion_parent_policy", String, 16),
        ("reviewed_notion_lesson_page_id", String, 128),
        ("reviewed_notion_lesson_title", Text, None),
    ):
        assert name in target_cols, f"regeneration_targets.{name} missing"
        assert target_cols[name].nullable is True, f"{name} must stay nullable"
        assert isinstance(target_cols[name].type, sqltype), name
        if length is not None:
            assert target_cols[name].type.length == length, name


def test_campaign_version_check_refuses_v1_on_the_campaigns_table():
    checks = _check_constraints(RegenerationCampaign)
    assert "ck_regeneration_campaigns_publication_version" in checks
    sqltext = checks["ck_regeneration_campaigns_publication_version"]
    # NULL stays legal (historical campaigns); 1 never does — logical V1 is the
    # pre-existing `Homework` page, which no campaign produced.
    assert "publication_version IS NULL" in sqltext
    assert "publication_version >= 2" in sqltext


def test_reviewed_destination_check_is_declared_and_null_safe():
    """The destination CHECK must be TOTAL, not merely correct on non-nulls.

    SQL is three-valued and a CHECK is SATISFIED by UNKNOWN, so a predicate
    built from bare `col = 'reuse'` comparisons evaluates to NULL — and is
    therefore ACCEPTED — for exactly the half-filled shapes this constraint
    exists to refuse. `IS NOT DISTINCT FROM` and the leading `IS NOT NULL`
    guard are what make every comparison total. The behavioural proof is in
    `tests/integration/test_regeneration_constraints.py`; this pins the SQL so
    a "simplification" back to `=` cannot pass unnoticed.
    """
    checks = _check_constraints(RegenerationTarget)
    assert "ck_regeneration_targets_notion_parent_decision" in checks
    sqltext = checks["ck_regeneration_targets_notion_parent_decision"]
    assert "notion_parent_policy IS NOT NULL" in sqltext
    assert "IS NOT DISTINCT FROM" in sqltext
    # No bare equality against a policy column may survive.
    assert "notion_parent_policy = " not in sqltext
    assert "notion_container_policy = " not in sqltext
    for literal in ("'reuse'", "'create'"):
        assert literal in sqltext
    for column in (
        "reviewed_notion_container_page_id",
        "reviewed_notion_lesson_page_id",
        "reviewed_notion_lesson_title",
    ):
        assert column in sqltext, f"{column} not constrained"


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


SESSION_LIMIT_CHECK = (
    "session_limit_strategy IS NULL OR "
    "session_limit_strategy IN ('pause','switch','inherit')"
)
# The IS NOT NULL conjunct is NULL-safety, not a relaxation of the accepted
# set: `NULL IN ('pause','switch')` is UNKNOWN and a CHECK constraint is
# SATISFIED by UNKNOWN, so the predicate without it would ACCEPT a revision
# carrying no strategy at all.
REVISION_SESSION_LIMIT_CHECK = (
    "revision_of_job_id IS NULL OR "
    "(session_limit_strategy IS NOT NULL "
    "AND session_limit_strategy IN ('pause','switch'))"
)


def test_homework_job_persists_the_session_limit_strategy_for_revisions():
    """`LaunchContract.session_limit_strategy` was a guaranteed no-op without
    this column.

    `session_limit_strategy` otherwise lives only on `batches` and in
    `settings`, and `ck_homework_jobs_revision_no_batch` forces every revision
    to have `batch_id IS NULL` — so an approved, frozen, validated value always
    fell through to the mutable fleet-wide default. The revision check is
    `IN ('pause','switch')` and NOT merely `IS NOT NULL`: a stored `'inherit'`
    would re-resolve against `settings.session_limit_strategy` at run time and
    recreate exactly the no-op this column closes.

    Ordinary jobs leave it NULL and keep the existing batch-then-global
    resolution untouched.
    """
    from sqlalchemy import String

    col = HomeworkJob.__table__.c["session_limit_strategy"]
    assert col.nullable is True
    assert isinstance(col.type, String)
    assert col.type.length == 16

    checks = _check_constraints(HomeworkJob)
    assert checks["ck_homework_jobs_session_limit_strategy"] == SESSION_LIMIT_CHECK
    assert (
        checks["ck_homework_jobs_revision_session_limit_strategy"]
        == REVISION_SESSION_LIMIT_CHECK
    )
    # Declared in __table_args__, so the migration and the ORM cannot drift.
    declared = {
        c.name
        for c in HomeworkJob.__table_args__
        if getattr(c, "name", None) is not None
    }
    assert "ck_homework_jobs_session_limit_strategy" in declared
    assert "ck_homework_jobs_revision_session_limit_strategy" in declared


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
    )
    base.update(overrides)
    return LaunchContract(**base)


def test_launch_contract_defaults_and_immutability():
    c = _contract()
    assert c.extract_transport == "inherit"
    assert c.judge_transport == "inherit"
    assert c.solver_transport == "inherit"
    assert c.session_limit_strategy == "inherit"
    with pytest.raises(ValidationError):
        c.provider = "claude"  # frozen — the approved contract never mutates


def test_launch_contract_does_not_freeze_the_global_solver_toggle():
    """Solver enablement is a PROCESS-GLOBAL, not a per-launch option.

    `pipeline.py`'s `_solver_on` reads `settings.solver_enabled`. There is no
    per-launch solver surface anywhere in the product — not on `homework_jobs`,
    not on `batches`, not on `launch_defaults` — and `jobs_repo.create` has no
    such parameter. Freezing it here would invent a control that does not
    exist: a campaign approved with `solver_enabled=False` would still run the
    solver, and one approved with `True` would stop the moment an operator
    flipped `SOLVER_ENABLED` in `.env`. Regeneration observes and REPORTS the
    global value instead.
    """
    assert "solver_enabled" not in LaunchContract.model_fields
    # extra="forbid" turns an attempt to freeze it into a loud draft-time error
    # rather than a silently dropped key.
    with pytest.raises(ValidationError):
        _contract(solver_enabled=True)


def test_launch_contract_does_not_freeze_a_campaign_wide_output_language():
    """A revision's language is per TARGET, not per campaign.

    `uq_regeneration_targets_campaign_toc_language` is
    `UNIQUE(campaign_id, toc_entry_id, output_language)`, so ONE campaign may
    hold a UZ and an RU target for the SAME lesson, and discovery takes
    `output_languages` (plural). Task 6 copies `output_language` from the
    IMMEDIATE SOURCE JOB, because a lineage is scoped by
    `(toc_entry_id, output_language)`. A single campaign-wide value here would
    be a frozen, operator-approved field with no read path — and, worse, one an
    implementer could believe and stamp, publishing an RU revision of a UZ
    lesson or colliding with the other language's lineage.
    """
    assert "output_language" not in LaunchContract.model_fields
    # extra="forbid" makes re-introducing it a loud draft-time error rather
    # than a key that silently drops out of the JSONB column.
    with pytest.raises(ValidationError):
        _contract(output_language="uz")


def test_launch_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        _contract(kind="teacher_material")


def test_launch_contract_serializes_to_json_safe_dict():
    payload = _contract().model_dump(mode="json")
    assert payload["provider"] == "gemini"
    assert payload["model"] == "gemini-3.5-flash"
    assert payload["transport"] == "api"
    # The language is a per-target value, never a campaign-wide contract key.
    assert "output_language" not in payload
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


def test_launch_contract_validates_the_session_strategy():
    with pytest.raises(ValidationError):
        _contract(session_limit_strategy="explode")
    assert _contract(session_limit_strategy="switch").session_limit_strategy == "switch"


# ── LaunchContract resolution ───────────────────────────────────────────────
# A campaign is drafted once and launched in two waves separated by a human
# gate. Everything below exists to make ONE campaign mean ONE concrete thing:
# resolution happens once, at draft, and every later reader copies.


def _defaults(**overrides) -> LaunchDefaultsSnapshot:
    """The `launch_defaults` singleton as `create_campaign` reads it, once."""
    base = dict(
        extract_provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        extract_transport="api",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        judge_transport="api",
        solver_provider="claude",
        solver_model="claude-sonnet-4-6",
        solver_transport="inherit",
    )
    base.update(overrides)
    return LaunchDefaultsSnapshot(**base)


def _cli_contract(**overrides):
    """A draft whose content transport is cli — the only shape in which a draft
    may leave a model at "provider default" (transport=api demands an explicit
    model, and the draft validator enforces that before resolution)."""
    base = dict(provider="claude", model="claude-sonnet-4-6", transport="cli")
    base.update(overrides)
    return _contract(**base)


def _resolved(draft=None, defaults=None, session_limit_strategy="pause"):
    return resolve_launch_contract(
        draft if draft is not None else _contract(),
        defaults=defaults if defaults is not None else _defaults(),
        session_limit_strategy=session_limit_strategy,
    )


def test_resolution_stamps_concrete_role_selections_from_the_launch_defaults():
    r = _resolved()
    assert (r.extract_provider, r.extract_model) == ("gemini", "gemini-3.5-flash-lite")
    assert (r.judge_provider, r.judge_model) == ("gemini", "gemini-3.5-flash")
    assert (r.solver_provider, r.solver_model) == ("claude", "claude-sonnet-4-6")
    assert (r.extract_transport, r.judge_transport, r.solver_transport) == (
        "api", "api", "inherit")
    assert r.session_limit_strategy == "pause"


def test_an_explicit_draft_role_pick_beats_the_launch_default():
    """`resolve_role_selection` is the authority: an explicit provider takes
    THAT provider's own default model, never the global default's model (which
    belongs to a different provider)."""
    r = _resolved(draft=_cli_contract(judge_provider="claude"))
    assert (r.judge_provider, r.judge_model) == ("claude", "claude-sonnet-4-6")
    assert _defaults().judge_model == "gemini-3.5-flash"  # not this one


def test_resolution_refuses_a_draft_that_names_no_content_model():
    """Fail closed: nothing may invent the content model.

    An ordinary launch does NOT resolve it — `jobs.py`/`batch.py` pass
    `model=body.model` verbatim — and the operator's real content default lives
    in `launch_defaults.content_provider`/`content_model`, which this
    resolution deliberately cannot reach (`LaunchDefaultsSnapshot` carries the
    three ROLES only). Substituting `default_model(provider)` would stamp
    `MODEL_MANIFEST[provider][0]` — for gemini the PRO-tier
    `gemini-3.1-pro-preview` — onto a whole approved campaign, which is neither
    the operator's default nor any value a launch path would produce.
    """
    draft = _cli_contract(model=None)
    assert draft.model is None  # legal as a DRAFT input; not a resolvable one
    with pytest.raises(ValueError, match="content model"):
        _resolved(draft=draft)
    # ...and specifically not the manifest-head substitution.
    assert default_model("gemini") == "gemini-3.1-pro-preview"
    with pytest.raises(ValueError, match="content model"):
        _resolved(draft=_contract(model=None, transport="cli", provider="gemini"))


def test_launch_defaults_edited_after_resolution_do_not_alter_the_resolved_contract():
    """The canary wave and the bulk wave are separated by a human gate; editing
    `/settings` in between is an ordinary operator action.

    Without a single draft-time resolution the two waves would run different
    judge/extract models and the canary's approval evidence would stop
    describing the bulk.
    """
    canary = _resolved()
    stored = canary.model_dump(mode="json")  # what create_campaign persists

    edited = _defaults(judge_provider="claude", judge_model="claude-opus-4-8",
                       extract_model="gemini-3.5-flash")
    # The edit is real — the SAME draft resolved against it is a different contract.
    assert _resolved(defaults=edited) != canary

    # ...but the approved contract is untouched, and reading it back out of the
    # JSONB column takes no defaults argument at all, so it cannot drift.
    assert (canary.judge_provider, canary.judge_model) == ("gemini", "gemini-3.5-flash")
    assert ensure_resolved(stored) == canary


def test_the_fleet_session_limit_strategy_is_read_once_and_frozen(monkeypatch):
    """`SESSION_LIMIT_STRATEGY` is env-loaded and restart-mutable. One campaign
    must not run its canary on 'pause' and its bulk on 'switch'."""
    from app.config import settings

    monkeypatch.setattr(settings, "session_limit_strategy", "pause")
    draft = _contract(session_limit_strategy="inherit")
    canary = _resolved(
        draft=draft,
        session_limit_strategy=resolve_session_limit_strategy(draft.session_limit_strategy),
    )
    assert canary.session_limit_strategy == "pause"
    stored = canary.model_dump(mode="json")

    # The operator edits the env and the head restarts between the two waves.
    monkeypatch.setattr(settings, "session_limit_strategy", "switch")
    assert resolve_session_limit_strategy("inherit") == "switch"  # the global really moved
    assert ensure_resolved(stored).session_limit_strategy == "pause"


def test_an_explicit_draft_session_limit_strategy_survives_resolution(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "session_limit_strategy", "pause")
    draft = _contract(session_limit_strategy="switch")
    r = _resolved(
        draft=draft,
        session_limit_strategy=resolve_session_limit_strategy(draft.session_limit_strategy),
    )
    assert r.session_limit_strategy == "switch"


def test_persistence_refuses_an_unresolved_contract():
    # A raw draft: 'inherit' strategy and no role selections at all.
    with pytest.raises(ValidationError):
        ensure_resolved(_contract())
    resolved = _resolved().model_dump(mode="json")
    with pytest.raises(ValidationError):
        ensure_resolved({**resolved, "session_limit_strategy": "inherit"})
    with pytest.raises(ValidationError):
        ensure_resolved({**resolved, "judge_provider": None, "judge_model": None})
    with pytest.raises(ValidationError):
        ensure_resolved({**resolved, "solver_model": None})
    # A content model left at "provider default" is not a concrete meaning
    # either — the boundary refuses it even though resolution can no longer
    # produce it (a null content model is refused there, never substituted).
    cli = _resolved(draft=_cli_contract())
    with pytest.raises(ValidationError):
        ensure_resolved({**cli.model_dump(mode="json"), "model": None})


def test_role_transport_may_stay_inherit_because_the_content_transport_is_concrete():
    """'inherit' is not an unresolved value here: it is a deterministic relation
    to `transport`, which is already fixed on the contract."""
    r = _resolved()
    assert r.solver_transport == "inherit"
    assert r.transport == "api"
    assert resolve_role_transport(r.solver_transport, r.transport) == "api"
    # An explicit launch default still wins, exactly as at an ordinary launch.
    assert _resolved(defaults=_defaults(solver_transport="cli")).solver_transport == "cli"


def test_resolution_validates_the_resolved_pair_through_production_rules():
    # extract may not resolve onto an api-only provider (validate_role_provider).
    with pytest.raises(ValidationError):
        _resolved(defaults=_defaults(extract_provider="clodex", extract_model="gpt-5.5"))
    # An off-manifest (retired) launch default fails loudly instead of being stamped.
    with pytest.raises(ValidationError):
        _resolved(defaults=_defaults(judge_model="gemini-2.5-flash"))
    # An api-only judge model may not resolve onto a cli effective transport
    # (judge_transport 'inherit' + a cli content transport).
    with pytest.raises(ValidationError):
        _resolved(draft=_cli_contract(), defaults=_defaults(judge_transport="inherit"))


def test_resolution_refuses_when_no_provider_is_available_anywhere():
    with pytest.raises(ValueError, match="judge"):
        _resolved(defaults=_defaults(judge_provider=None, judge_model=None))


def test_resolution_refuses_a_session_limit_strategy_that_is_not_concrete():
    with pytest.raises(ValueError, match="inherit"):
        _resolved(session_limit_strategy="inherit")
    with pytest.raises(ValueError):
        _resolved(session_limit_strategy="explode")


def test_a_resolved_contract_is_a_launch_contract_and_round_trips_through_jsonb():
    r = _resolved()
    assert isinstance(r, LaunchContract)
    payload = r.model_dump(mode="json")
    assert ResolvedLaunchContract(**payload) == r
    assert ensure_resolved(r) is r
    with pytest.raises(ValidationError):
        ResolvedLaunchContract(**{**payload, "kind": "teacher_material"})
    with pytest.raises(ValidationError):
        r.judge_model = "gemini-3.6-flash"  # frozen, like the draft


def test_the_launch_defaults_snapshot_validates_straight_from_the_orm_row():
    """`create_campaign` reads the `launch_defaults` singleton ROW and hands it
    in — so the snapshot must be constructible from that row.

    Every transport column on it is nullable and an unset one is the launcher's
    "Auto", i.e. `'inherit'` — the exact case this class exists to absorb. If
    the row could not be validated directly, Task 7 would hand-map nine fields
    and re-derive the NULL→'inherit' rule on its own, which is the second
    definition this module exists to prevent.
    """
    ld = LaunchDefaults(
        id=1,
        extract_provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        extract_transport="api",
        judge_provider="gemini",
        judge_model="gemini-3.5-flash",
        judge_transport=None,  # the launcher's "Auto"
        solver_provider="claude",
        solver_model="claude-sonnet-4-6",
        solver_transport=None,
    )
    snap = LaunchDefaultsSnapshot.model_validate(ld)
    assert (snap.judge_transport, snap.solver_transport) == ("inherit", "inherit")
    assert snap.extract_transport == "api"
    assert (snap.extract_provider, snap.extract_model) == (
        "gemini", "gemini-3.5-flash-lite")
    assert (snap.judge_provider, snap.judge_model) == ("gemini", "gemini-3.5-flash")
    assert (snap.solver_provider, snap.solver_model) == ("claude", "claude-sonnet-4-6")
    # The row it came from carries columns the contract has no business
    # freezing (`toc_transport`, `output_language`, the content pair); reading
    # the row must not drag them in.
    assert "toc_transport" not in LaunchDefaultsSnapshot.model_fields
    assert "output_language" not in LaunchDefaultsSnapshot.model_fields
    # A bad value on the row still fails loudly on the attribute path.
    with pytest.raises(ValidationError):
        LaunchDefaultsSnapshot.model_validate(LaunchDefaults(id=1, judge_transport="bogus"))
    # ...and resolution accepts the row-built snapshot like any other.
    assert _resolved(defaults=snap).judge_transport == "inherit"


def test_a_null_launch_default_transport_means_inherit():
    """`launch_defaults` columns are nullable — an unset role transport is the
    launcher's 'Auto', not a missing value."""
    assert _defaults(judge_transport=None).judge_transport == "inherit"
    with pytest.raises(ValidationError):
        _defaults(judge_transport="bogus")


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
