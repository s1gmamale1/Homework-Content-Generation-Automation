"""`regeneration_campaign` — the campaign orchestrator and its one human gate.

The service owns every transition a campaign or target makes outside the
pipeline: creation (which freezes the launch contract and the per-target phase
plans), the canary launch, the single approval, rejection, cancellation,
generation/publication retry and explicit abandonment.

Four properties are what these tests exist to hold down:

* **one resolution.** The contract is resolved once, at creation, and every
  later wave COPIES it. The test that can actually catch a second resolution
  mutates `launch_defaults` and `settings.session_limit_strategy` BETWEEN the
  canary and the bulk wave — a single-wave test is structurally blind to it.
* **no spend before the gate.** Preflight covers every destination before any
  job exists, and only canary targets get a revision job before approval.
* **no lost/duplicated work across a commit boundary.** `create_revision_job`
  commits internally, so every call gets its own dedicated session with no
  pending campaign writes, and a crash mid-wave resumes without duplicating a
  job or losing a campaign write.
* **no terminal campaign hiding a live target.** Cancellation converges through
  the rollup; it never writes `cancelled` while anything is still in flight.

The pure tests below run everywhere. The rest need a real Postgres because the
invariants under test are the row locks, the compare-and-set updates and the
`trg_regeneration_targets_publication_gate` trigger itself — a faked session
would be asserting on the fake.
"""
from __future__ import annotations

import copy
import os
import pickle
import uuid
from types import SimpleNamespace

import pytest

from app.schemas.regeneration_contract import LaunchContract, ResolvedLaunchContract
from app.services import regeneration_campaign as svc
from app.services import regeneration_states
from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_MARKER = "pytest-regen-campaign"

_CONTRACT = ResolvedLaunchContract(
    provider="gemini", model="gemini-3.6-flash", transport="api",
    extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
    extract_transport="api",
    judge_provider="gemini", judge_model="gemini-3.5-flash", judge_transport="api",
    solver_provider="gemini", solver_model="gemini-3.1-pro-preview",
    solver_transport="api",
    session_limit_strategy="pause",
)

# Every leg on a provider/model pair the CLI actually supports, so a `cli`
# transport in these tests is refused by THIS service rather than by the
# manifest's own api-only rule (gemini 3.x flash is api-only).
_CLI_CAPABLE = {
    "provider": "claude", "model": "claude-sonnet-4-6", "transport": "api",
    "extract_provider": "claude", "extract_model": "claude-haiku-4-5-20251001",
    "extract_transport": "api",
    "judge_provider": "claude", "judge_model": "claude-opus-4-7",
    "judge_transport": "api",
    "solver_provider": "claude", "solver_model": "claude-opus-4-7",
    "solver_transport": "api",
    "session_limit_strategy": "pause",
}

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _notion_destinations(monkeypatch):
    """A configured Notion destination for the seeded book, in both languages.

    Preflight requires the TARGET LANGUAGE's own `{lang}:{subject}|{grade}`
    subject page for every lesson — a stamped `notion_lesson_page_id` does not
    excuse it, because that column is language-blind and the publisher no longer
    follows it across languages. So a lifecycle test that launches needs a real
    mapping, and the tests about a MISSING destination set their own.

    Pinned here rather than inherited from the ambient `NOTION_SUBJECT_PAGES`,
    which would make this file's verdicts depend on an operator's live config.
    """
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "notion_subject_pages",
        {f"{_SUBJECT}|5": "page-uz-5", f"ru:{_SUBJECT}|5": "page-ru-5"},
        raising=False,
    )


# ═════════════════════════════════════════════════════════════════════════
# pure: the interface, the guards, the arithmetic and the rollup
# ═════════════════════════════════════════════════════════════════════════


def test_service_exposes_the_whole_task_7_interface():
    """The plan's eight entry points, by name — a caller (Task 9) codes to
    these and nothing else."""
    for name in (
        "create_campaign", "launch_canary", "approve_canary", "reject_canary",
        "cancel", "retry_generation", "retry_publication", "abandon",
    ):
        assert callable(getattr(svc.RegenerationCampaignService, name)), name


# ─── API-only (binding: campaigns never enter the CLI path) ──────────────


@pytest.mark.parametrize(
    "override, offender",
    [
        ({"transport": "cli"}, "transport"),
        ({"extract_transport": "cli"}, "extract_transport"),
        ({"judge_transport": "cli"}, "judge_transport"),
        ({"solver_transport": "cli"}, "solver_transport"),
    ],
)
def test_a_non_api_effective_transport_is_refused(override, offender):
    """Regeneration is a paid, fleet-wide workflow priced against api rates.
    A cli leg would be mispriced by the estimator AND would bypass the
    credential limiter, so it is refused rather than labelled."""
    contract = ResolvedLaunchContract(**{**_CLI_CAPABLE, **override})
    with pytest.raises(svc.NonApiTransport) as exc:
        svc.require_api_transport(contract)
    assert offender in str(exc.value)


def test_inherit_role_transport_follows_an_api_job_transport():
    """`inherit` is concrete: it resolves against the contract's own
    transport, which is already fixed. An api job with inherited roles is a
    legal API-only campaign."""
    contract = ResolvedLaunchContract(
        **{**_CONTRACT.model_dump(),
           "extract_transport": "inherit", "judge_transport": "inherit",
           "solver_transport": "inherit"}
    )
    svc.require_api_transport(contract)  # must not raise


def test_inherit_role_transport_on_a_cli_job_is_still_refused():
    """The refusal is on the EFFECTIVE transport, not the literal field —
    `inherit` over a cli job is a cli call."""
    contract = ResolvedLaunchContract(**{
        **_CLI_CAPABLE, "transport": "cli",
        "extract_transport": "inherit", "judge_transport": "inherit",
        "solver_transport": "inherit"})
    with pytest.raises(svc.NonApiTransport):
        svc.require_api_transport(contract)


# ─── retired models (binding: fail closed, operator-readable) ────────────


@pytest.mark.parametrize(
    "role, fields",
    [
        ("content", {"provider": "gemini", "model": "gemini-2.5-flash"}),
        ("extract", {"extract_provider": "gemini",
                     "extract_model": "gemini-2.5-flash-lite"}),
        ("judge", {"judge_provider": "gemini", "judge_model": "gemini-2.5-pro"}),
        ("solver", {"solver_provider": "gemini", "solver_model": "gemini-2.5-flash"}),
    ],
)
def test_a_retired_model_on_any_role_fails_closed(role, fields):
    """A campaign stored while a model was live keeps that stamp forever. The
    check runs on the RAW stored values, before `ensure_resolved` — a retired
    model has been REMOVED from MODEL_MANIFEST, so pydantic would refuse it
    with a validation error that tells an operator nothing about retirement."""
    stored = {**_CONTRACT.model_dump(), **fields}
    with pytest.raises(svc.RetiredModelRefusal) as exc:
        svc.require_live_models(SimpleNamespace(**stored), what="approve")
    message = str(exc.value)
    assert role in message
    assert fields[[k for k in fields][-1]] in message
    assert [(r, p, m) for r, p, m in exc.value.retired][0][0] == role


def test_a_live_contract_passes_the_retired_check():
    svc.require_live_models(SimpleNamespace(**_CONTRACT.model_dump()), what="approve")


def test_the_retired_check_is_the_shared_fleet_helper_not_a_second_copy():
    """One definition of 'retired', shared with `jobs.py::retry_job`."""
    from app.services import job_reactivation

    assert svc.retired_models_in_job is job_reactivation.retired_models_in_job


# ─── launch stagger (reuses app.services.launch_stagger) ─────────────────


def test_stagger_offsets_are_the_shared_wave_rule():
    from app.services.launch_stagger import stagger_offset

    plan = svc.plan_launch_stagger(10, wave_size=4, interval_seconds=60)
    assert plan.offsets == tuple(
        stagger_offset(i, wave_size=4, interval_seconds=60) for i in range(10)
    )
    assert plan.offsets == (0, 0, 0, 0, 60, 60, 60, 60, 120, 120)
    assert plan.wave_count == 3
    assert plan.final_offset_seconds == 120


def test_a_launch_inside_one_wave_starts_immediately():
    plan = svc.plan_launch_stagger(4, wave_size=4, interval_seconds=60)
    assert plan.offsets == (0, 0, 0, 0)
    assert plan.wave_count == 1 and plan.final_offset_seconds == 0


def test_a_one_target_canary_starts_immediately():
    plan = svc.plan_launch_stagger(1, wave_size=4, interval_seconds=60)
    assert plan.offsets == (0,) and plan.wave_count == 1


@pytest.mark.parametrize("wave_size, interval", [(0, 60), (4, 0), (0, 0)])
def test_zero_knobs_are_the_explicit_kill_switch(wave_size, interval):
    plan = svc.plan_launch_stagger(9, wave_size=wave_size, interval_seconds=interval)
    assert plan.offsets == (0,) * 9
    assert plan.wave_count == 1 and plan.final_offset_seconds == 0


def test_stagger_defaults_come_from_the_regeneration_knobs(monkeypatch):
    """Not the Fleet batch knobs — a regeneration wave re-runs whole snapshots
    on top of whatever normal generation the fleet is already doing."""
    from app.config import settings

    monkeypatch.setattr(settings, "regeneration_launch_wave_size", 2)
    monkeypatch.setattr(settings, "regeneration_launch_wave_interval_seconds", 30)
    monkeypatch.setattr(settings, "batch_launch_wave_size", 999)
    monkeypatch.setattr(settings, "batch_launch_wave_interval_seconds", 999)
    plan = svc.plan_launch_stagger(5)
    assert plan.offsets == (0, 0, 30, 30, 60)


def test_an_empty_release_has_no_waves():
    plan = svc.plan_launch_stagger(0, wave_size=4, interval_seconds=60)
    assert plan.offsets == () and plan.wave_count == 0
    assert plan.final_offset_seconds == 0


# ─── campaign rollup derivation ──────────────────────────────────────────


@pytest.mark.parametrize(
    "statuses, approved, rejected, cancelled, expected",
    [
        # pre-approval
        (["generating"], False, False, False, "canary_running"),
        (["awaiting_canary_approval"], False, False, False, "awaiting_canary_approval"),
        (["awaiting_canary_approval", "planned"], False, False, False,
         "awaiting_canary_approval"),
        (["generation_failed", "planned"], False, False, False, "attention_required"),
        (["planned"], False, False, False, "draft"),
        # post-approval
        (["publication_pending", "generating"], True, False, False, "bulk_running"),
        (["published", "generation_failed"], True, False, False, "attention_required"),
        (["published"], True, False, False, "completed"),
        (["published", "abandoned"], True, False, False, "completed_with_abandonments"),
        # rejection converges to `rejected` only once every target is terminal
        (["abandoned"], False, True, False, "rejected"),
        (["abandoned", "generating"], False, True, False, "attention_required"),
        # cancellation NEVER reports terminal while a target is in flight
        (["abandoned", "publishing"], True, False, True, "attention_required"),
        (["abandoned", "generating"], True, False, True, "attention_required"),
        (["abandoned"], True, False, True, "cancelled"),
        (["abandoned", "published"], True, False, True,
         "completed_with_abandonments"),
    ],
)
def test_campaign_status_is_derived_from_its_targets(
    statuses, approved, rejected, cancelled, expected
):
    assert svc.derive_campaign_status(
        target_statuses=statuses,
        approved=approved,
        rejected=rejected,
        cancelled=cancelled,
    ) == expected


def test_derivation_defers_to_the_shared_pure_rollup():
    """The state module owns the rule; this service must not restate it."""
    from app.services import regeneration_states

    assert svc.roll_up_campaign is regeneration_states.roll_up_campaign


def test_derivation_carries_the_canary_rows_through_to_the_rollup():
    """The gate is a claim about the CANARIES. If this wrapper drops them, the
    rollup falls back to its flat reading and the stale gate comes back."""
    assert svc.derive_campaign_status(
        target_statuses=["awaiting_canary_approval", "planned"],
        approved=False,
        canary_statuses=["awaiting_canary_approval", "planned"],
    ) == "attention_required"
    assert svc.derive_campaign_status(
        target_statuses=["awaiting_canary_approval", "planned"],
        approved=False,
        canary_statuses=["awaiting_canary_approval"],
    ) == "awaiting_canary_approval"


@pytest.mark.parametrize(
    "status", sorted(regeneration_states.TARGET_STATUSES)
)
def test_the_rollup_and_the_approval_guard_agree_on_every_canary_status(status):
    """One predicate, two consumers — asserted behaviourally rather than by
    identity, because the failure mode is not "someone re-implemented it", it
    is "the two answers drifted". A status the rollup calls a gate and the
    guard refuses is exactly the bug: the operator is shown a button whose
    handler 409s.
    """
    gate_ready = regeneration_states.is_canary_gate_ready([status])
    try:
        svc.assert_canary_gate_ready([status])
        guard_ready = True
    except svc.CanaryNotReviewable:
        guard_ready = False
    assert gate_ready is guard_ready, status


@pytest.mark.parametrize("terminal", ["completed", "completed_with_abandonments",
                                      "cancelled", "rejected"])
def test_a_terminal_campaign_status_over_live_targets_is_refused(terminal):
    """The one rule that cannot be delegated: a terminal campaign must never
    hide a non-terminal target. Guarded at the service, because
    `set_campaign_status` is a dumb compare-and-set."""
    with pytest.raises(svc.TerminalCampaignWithLiveTargets):
        svc.assert_not_hiding_live_targets(
            terminal, ["abandoned", "publishing"]
        )


def test_a_terminal_campaign_status_over_terminal_targets_is_allowed():
    svc.assert_not_hiding_live_targets("cancelled", ["abandoned", "abandoned"])
    svc.assert_not_hiding_live_targets("bulk_running", ["publishing"])


# ─── the canary gate: a stale rollup must never release the bulk ─────────
#
# The campaign status is DERIVED, and it can lag: the report-driven `roll_up`
# is debounced per campaign, a worker may have moved a canary since it last
# ran, and `approve_canary`'s own compare-and-set deliberately accepts
# `attention_required` (a canary that failed and was retried back to health
# arrives in exactly that status). So the status is not evidence that a human
# had something to review. The canary ROWS are.


def test_the_gate_opens_only_when_every_canary_is_reviewable():
    svc.assert_canary_gate_ready(["awaiting_canary_approval"])
    svc.assert_canary_gate_ready(
        ["awaiting_canary_approval", "awaiting_canary_approval"]
    )


@pytest.mark.parametrize(
    "blocker",
    ["planned", "generating", "generation_failed", "publication_failed"],
)
def test_one_unreviewable_canary_closes_the_gate_for_the_whole_wave(blocker):
    """Approval is not per lesson — ONE click releases every remaining target.
    A wave holding one reviewable revision beside one that nobody could review
    is therefore not a decision an operator is in a position to make."""
    with pytest.raises(svc.CanaryNotReviewable):
        svc.assert_canary_gate_ready(["awaiting_canary_approval", blocker])


def test_an_abandoned_canary_is_excluded_only_beside_a_reviewable_one():
    """Abandonment is the operator's own decision to drop that lesson, so it
    does not hold the others hostage — but it is not evidence either. A wave
    whose every canary was abandoned has nothing anybody approved."""
    svc.assert_canary_gate_ready(["abandoned", "awaiting_canary_approval"])
    with pytest.raises(svc.CanaryNotReviewable):
        svc.assert_canary_gate_ready(["abandoned"])
    with pytest.raises(svc.CanaryNotReviewable):
        svc.assert_canary_gate_ready(["abandoned", "abandoned"])


def test_a_campaign_with_no_canary_row_at_all_cannot_be_approved():
    with pytest.raises(svc.CanaryNotReviewable):
        svc.assert_canary_gate_ready([])


def test_the_gate_refusal_is_an_illegal_campaign_action():
    """So every existing caller — the API's 409 mapping included — keeps
    handling it without growing a second branch."""
    assert issubclass(svc.CanaryNotReviewable, svc.IllegalCampaignAction)


def test_gate_refusals_preserve_actionable_metadata_across_copy_and_pickle():
    """A middleware/logging boundary may copy or pickle an exception. Losing
    the remedy or blocker kinds there would turn the structured 409 into a
    generic refusal even though the service produced the useful answer."""
    original = pytest.raises(
        svc.CanaryNotReviewable,
        svc.assert_canary_gate_ready,
        ["generation_failed", "generation_failed", "planned"],
    ).value

    assert original.blockers == ("generation_failed", "planned")
    assert "launch" in original.remedy and "retry" in original.remedy
    for restored in (copy.copy(original), pickle.loads(pickle.dumps(original))):
        assert restored.blockers == original.blockers
        assert restored.total == 3
        assert restored.reason_code == "not_reviewable"
        assert restored.remedy == original.remedy
        assert str(restored) == str(original)


def test_all_abandoned_and_unlaunched_canaries_name_the_real_next_action():
    with pytest.raises(svc.CanaryNotReviewable) as abandoned:
        svc.assert_canary_gate_ready(["abandoned", "abandoned"])
    assert abandoned.value.blockers == ()
    assert "retry or abandon" not in str(abandoned.value).lower()
    assert "cancel" in abandoned.value.remedy or "reject" in abandoned.value.remedy

    with pytest.raises(svc.CanaryNotReviewable) as planned:
        svc.assert_canary_gate_ready(["planned"])
    assert planned.value.blockers == ("planned",)
    assert "launch" in planned.value.remedy


def test_selection_limit_refusal_is_copy_and_pickle_safe():
    original = svc.SelectionTooLarge(
        501, maximum=500, what="create a regeneration campaign"
    )
    for restored in (copy.copy(original), pickle.loads(pickle.dumps(original))):
        assert restored.count == 501
        assert restored.maximum == 500
        assert restored.what == "create a regeneration campaign"
        assert str(restored) == str(original)


def _returns(value):
    """A stand-in for any awaitable repository/service call."""
    async def _call(*_args, **_kwargs):
        return value
    return _call


class _NullSession:
    """Enough session for the guard to be REACHED. Every real read below is
    monkeypatched, so nothing here talks to a database."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None


async def test_approval_reads_the_canary_rows_not_the_campaign_status(monkeypatch):
    """The guard is wired AHEAD of the approval write, not beside it.

    `attention_required` is in `approve_canary`'s own expected-status set, so a
    campaign whose canary failed sits exactly one compare-and-set away from
    `approved` — and `approved` is the predicate
    `trg_regeneration_targets_publication_gate` checks before a target may
    publish. This drives the REAL method and asserts nothing was stamped.
    """
    campaign_id = uuid.uuid4()
    campaign = SimpleNamespace(
        id=campaign_id, status="attention_required", approved_at=None,
        rejected_at=None, cancel_requested_at=None,
        launch_contract=_CONTRACT.model_dump(),
    )
    monkeypatch.setattr(
        svc.RegenerationCampaignService, "roll_up", _returns(campaign)
    )
    monkeypatch.setattr(
        svc.campaigns_repo, "get_campaign_for_update", _returns(campaign)
    )
    monkeypatch.setattr(
        svc.targets_repo, "canary_statuses_for_campaign",
        _returns(["generation_failed"]),
    )
    stamped: list = []
    monkeypatch.setattr(
        svc.campaigns_repo, "set_campaign_status",
        lambda *a, **kw: stamped.append(kw) or _returns(True)(),
    )

    service = svc.RegenerationCampaignService(session_factory=_NullSession)
    with pytest.raises(svc.CanaryNotReviewable):
        await service.approve_canary(campaign_id, actor="operator")
    assert stamped == [], (
        "the campaign was approved over a canary nobody could review"
    )


# ─── selection scope: a campaign is never fleet-wide by accident ─────────


def test_a_selection_must_name_books_or_lessons():
    with pytest.raises(svc.UnboundedSelection):
        svc.require_bounded_selection(svc.CampaignSelection())


def test_output_languages_alone_are_not_a_scope():
    """A language FILTERS a scope; it does not bound one. On its own it selects
    every regenerable lesson of every book in that language — the whole fleet's
    content, priced and then spent."""
    with pytest.raises(svc.UnboundedSelection):
        svc.require_bounded_selection(
            svc.CampaignSelection(output_languages=("uz",))
        )


def test_books_or_lessons_each_bound_a_selection():
    svc.require_bounded_selection(svc.CampaignSelection(book_ids=(uuid.uuid4(),)))
    svc.require_bounded_selection(
        svc.CampaignSelection(toc_entry_ids=(uuid.uuid4(),))
    )
    svc.require_bounded_selection(
        svc.CampaignSelection(book_ids=(uuid.uuid4(),), output_languages=("uz",))
    )


def test_the_campaign_scope_cap_defaults_to_five_hundred_targets():
    assert svc.settings.regeneration_max_campaign_targets == 500


def test_the_cap_admits_its_own_boundary_and_refuses_one_more(monkeypatch):
    monkeypatch.setattr(svc.settings, "regeneration_max_campaign_targets", 7)
    svc.require_selection_within_cap(
        7, what="create a regeneration campaign"
    )
    with pytest.raises(svc.SelectionTooLarge) as excinfo:
        svc.require_selection_within_cap(
            8, what="create a regeneration campaign"
        )
    assert excinfo.value.count == 8
    assert excinfo.value.maximum == 7


async def test_an_unbounded_selection_is_refused_before_a_session_is_opened():
    """The cheapest possible refusal: an unfiltered discovery is a scan of
    every lineage the fleet has ever generated, so it must not even start."""
    def _explode():
        raise AssertionError(
            "create_campaign opened a session for an unbounded selection"
        )

    service = svc.RegenerationCampaignService(session_factory=_explode)
    with pytest.raises(svc.UnboundedSelection):
        await service.create_campaign(svc.CreateCampaignSpec(
            selection=svc.CampaignSelection(output_languages=("uz",)),
            contract=LaunchContract(**_CONTRACT.model_dump()),
            selected_phases=("flashcards",),
        ))


async def test_an_oversized_selection_is_refused_before_any_campaign_row(
    monkeypatch
):
    """The cap is applied to what DISCOVERY returned and BEFORE the insert, so
    an over-wide selection costs one read and leaves no row, no target and no
    active-lineage lock behind it."""
    def _never(*_args, **_kwargs):
        raise AssertionError("an oversized selection reached a write")

    monkeypatch.setattr(
        svc.RegenerationCampaignService, "_resolve_contract_once",
        _returns(_CONTRACT),
    )
    monkeypatch.setattr(svc.settings, "regeneration_max_campaign_targets", 2)
    source = SimpleNamespace(
        book_id=uuid.uuid4(), order_index=0, subject=_SUBJECT,
    )
    monkeypatch.setattr(
        svc.discovery, "list_source_candidates",
        _returns([
            SimpleNamespace(
                toc_entry_id=uuid.uuid4(), output_language="uz",
                source=source, reasons=(),
            )
            for _ in range(3)
        ]),
    )
    monkeypatch.setattr(svc.campaigns_repo, "create_campaign", _never)
    monkeypatch.setattr(svc.targets_repo, "create_target", _never)

    service = svc.RegenerationCampaignService(session_factory=_NullSession)
    with pytest.raises(svc.SelectionTooLarge):
        await service.create_campaign(svc.CreateCampaignSpec(
            selection=svc.CampaignSelection(book_ids=(uuid.uuid4(),)),
            contract=LaunchContract(**_CONTRACT.model_dump()),
            selected_phases=("flashcards",),
        ))


async def test_campaign_cap_counts_only_eligible_targets(monkeypatch):
    """Rejected lineages remain audit-visible but consume neither a target
    row nor campaign capacity."""
    monkeypatch.setattr(svc.settings, "regeneration_max_campaign_targets", 2)
    source = SimpleNamespace(
        book_id=uuid.uuid4(), order_index=0, subject=_SUBJECT,
        toc_entry_id=uuid.uuid4(), output_language="uz",
        source_job_id=uuid.uuid4(),
    )
    candidates = [
        SimpleNamespace(
            toc_entry_id=source.toc_entry_id, output_language="uz",
            source=source, reasons=(),
        ),
        *[
            SimpleNamespace(
                toc_entry_id=uuid.uuid4(), output_language="uz",
                source=None, reasons=("ineligible",),
            )
            for _ in range(5)
        ],
    ]
    monkeypatch.setattr(
        svc.RegenerationCampaignService, "_resolve_contract_once",
        _returns(_CONTRACT),
    )
    monkeypatch.setattr(
        svc.discovery, "list_source_candidates", _returns(candidates)
    )
    monkeypatch.setattr(
        svc.targets_repo, "active_targets_for_lineages", _returns([])
    )

    reached_write = False

    async def _campaign_write(*_args, **_kwargs):
        nonlocal reached_write
        reached_write = True
        raise RuntimeError("stop after proving the eligible-count guard")

    monkeypatch.setattr(svc.campaigns_repo, "create_campaign", _campaign_write)
    service = svc.RegenerationCampaignService(session_factory=_NullSession)

    with pytest.raises(RuntimeError, match="eligible-count guard"):
        await service.create_campaign(svc.CreateCampaignSpec(
            selection=svc.CampaignSelection(book_ids=(uuid.uuid4(),)),
            contract=LaunchContract(**_CONTRACT.model_dump()),
            selected_phases=("flashcards",),
        ))
    assert reached_write is True


# ─── deterministic ordering ──────────────────────────────────────────────


def test_targets_are_ordered_by_book_then_toc_order_then_language_then_id():
    book_a, book_b = uuid.UUID(int=1), uuid.UUID(int=2)
    id_lo, id_hi = uuid.UUID(int=10), uuid.UUID(int=11)
    rows = [
        (book_b, 0, "uz", id_lo),
        (book_a, 5, "uz", id_lo),
        (book_a, 1, "uz", id_hi),
        (book_a, 1, "ru", id_lo),
        (book_a, 1, "uz", id_lo),
    ]
    assert sorted(rows, key=lambda r: svc.target_sort_key(*r)) == [
        (book_a, 1, "ru", id_lo),
        (book_a, 1, "uz", id_lo),
        (book_a, 1, "uz", id_hi),
        (book_a, 5, "uz", id_lo),
        (book_b, 0, "uz", id_lo),
    ]


# ═════════════════════════════════════════════════════════════════════════
# real Postgres
# ═════════════════════════════════════════════════════════════════════════


async def _seed(
    *, lessons: int = 1, languages=("uz",), subject: str = _SUBJECT,
    notion: bool = True, complete: bool = True,
):
    """A book, `lessons` TOC entries and one DONE source job per lesson and
    language, each carrying a full phase snapshot."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        book = Book(
            subject=subject, original_filename="regen_campaign.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready", grade="5",
        )
        session.add(book)
        await session.flush()
        toc_ids, jobs = [], {}
        canonical = ("extract", *flow_for(subject))
        phases = canonical if complete else canonical[:-1]
        for index in range(lessons):
            toc = TOCEntry(
                book_id=book.id, section_title=f"Lesson {index}",
                order_index=index,
                notion_lesson_page_id=f"page-{uuid.uuid4()}" if notion else None,
            )
            session.add(toc)
            await session.flush()
            toc_ids.append(toc.id)
            for language in languages:
                job = HomeworkJob(
                    book_id=book.id, toc_entry_id=toc.id, subject=subject,
                    status="done", provider="gemini", model="gemini-3.6-flash",
                    transport="api", output_language=language,
                )
                session.add(job)
                await session.flush()
                for order, name in enumerate(phases):
                    session.add(PhaseOutput(
                        job_id=job.id, phase_name=name, phase_order=order,
                        prompt_hash=f"builtin:{name}:v9", provider="gemini",
                        model_name="gemini-3.6-flash",
                        output_md=f"# {name}\nbody", status="done",
                    ))
                await session.flush()
                jobs[(toc.id, language)] = job.id
        await session.commit()
        return {"book_id": book.id, "toc_ids": toc_ids, "jobs": jobs,
                "subject": subject}


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete, select, text

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        job_ids = list((await session.execute(
            select(HomeworkJob.id).where(HomeworkJob.book_id == ids["book_id"])
        )).scalars().all()) or [uuid.uuid4()]
        await session.execute(
            delete(AgentUsage).where(AgentUsage.homework_job_id.in_(job_ids)))
        await session.execute(
            delete(PhaseOutput).where(PhaseOutput.job_id.in_(job_ids)))
        await session.execute(
            delete(HomeworkJob)
            .where(HomeworkJob.book_id == ids["book_id"])
            .where(HomeworkJob.revision_of_job_id.is_not(None)))
        await session.execute(
            text("DELETE FROM regeneration_targets WHERE toc_entry_id IN "
                 "(SELECT id FROM toc_entries WHERE book_id = :b)"),
            {"b": ids["book_id"]})
        await session.execute(
            delete(RegenerationCampaign)
            .where(RegenerationCampaign.app_git_revision == _MARKER)
            .where(~RegenerationCampaign.targets.any()))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


@pytest.fixture()
async def seeded():
    ids = await _seed()
    try:
        yield ids
    finally:
        await _purge(ids)


def _spec(ids, *, lessons=None, languages=("uz",), canary_size=1, **kw):
    toc_ids = ids["toc_ids"] if lessons is None else ids["toc_ids"][:lessons]
    return svc.CreateCampaignSpec(
        selection=svc.CampaignSelection(
            toc_entry_ids=tuple(toc_ids), output_languages=tuple(languages),
        ),
        contract=kw.pop("contract", LaunchContract(**_CONTRACT.model_dump())),
        selected_phases=kw.pop("selected_phases", ("flashcards",)),
        canary_size=canary_size,
        app_git_revision=_MARKER,
        actor="pytest",
        **kw,
    )


def _service():
    return svc.RegenerationCampaignService()


async def _statuses(campaign_id):
    """(target status, is_canary, publication_version) ordered deterministically."""
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        return [
            (t.status, t.is_canary, t.publication_version)
            for t in await targets_repo.list_for_campaign(session, campaign_id)
        ]


async def _targets(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    async with SessionLocal() as session:
        return await targets_repo.list_for_campaign(session, campaign_id)


async def _revision_jobs(campaign_id):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return list((await session.execute(
            select(HomeworkJob)
            .join(RegenerationTarget,
                  RegenerationTarget.id == HomeworkJob.regeneration_target_id)
            .where(RegenerationTarget.campaign_id == campaign_id)
            .order_by(HomeworkJob.created_at)
        )).scalars().all())


async def _campaign(campaign_id):
    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo

    async with SessionLocal() as session:
        return await campaigns_repo.get_campaign(session, campaign_id)


# ─── creation ────────────────────────────────────────────────────────────


@db_only
async def test_creation_resolves_and_stores_one_concrete_contract(seeded):
    """`'inherit'`, a null role provider and a null role model never reach
    storage: the draft is resolved ONCE, here, before the insert."""
    draft = LaunchContract(
        provider="gemini", model="gemini-3.6-flash", transport="api",
        session_limit_strategy="inherit",
    )
    campaign = await _service().create_campaign(_spec(seeded, contract=draft))

    stored = campaign.launch_contract
    assert stored["session_limit_strategy"] in ("pause", "switch")
    for role in ("extract", "judge", "solver"):
        assert stored[f"{role}_provider"] is not None
        assert stored[f"{role}_model"] is not None
    # readable back through the shared boundary, which verifies but cannot resolve
    assert ResolvedLaunchContract.model_validate(stored)
    assert campaign.status == "draft"


@db_only
async def test_creation_makes_no_job_and_no_external_call(seeded):
    campaign = await _service().create_campaign(_spec(seeded))
    assert await _revision_jobs(campaign.id) == []
    assert [s for s, _, _ in await _statuses(campaign.id)] == ["planned"]


@db_only
async def test_creation_stores_the_planner_object_per_target(seeded):
    """Not a bare phase-name list — the copied/regenerated split, the
    auto-included set and the extraction flag must all survive to the wave."""
    from app.services.regeneration_planner import RegenerationPhasePlan

    campaign = await _service().create_campaign(_spec(seeded))
    (target,) = await _targets(campaign.id)
    plan = RegenerationPhasePlan.from_json(target.phase_plan)
    expected = build_phase_plan(subject=_SUBJECT, selected_phases=["flashcards"])
    assert plan == expected
    assert target.source_job_id == seeded["jobs"][(seeded["toc_ids"][0], "uz")]


@db_only
async def test_creation_picks_canaries_deterministically():
    """A stable (book, toc order, language, target id) order — the same
    selection always yields the same canary."""
    ids = await _seed(lessons=4, languages=("uz", "ru"))
    try:
        service = _service()
        first = await service.create_campaign(
            _spec(ids, languages=("uz", "ru"), canary_size=3))
        canaries = [
            (t.toc_entry_id, t.output_language)
            for t in await _targets(first.id) if t.is_canary
        ]
        assert len(canaries) == 3
        # lessons 0 and 1 come first, ru before uz inside a lesson
        assert canaries == [
            (ids["toc_ids"][0], "ru"), (ids["toc_ids"][0], "uz"),
            (ids["toc_ids"][1], "ru"),
        ]
    finally:
        await _purge(ids)


@db_only
async def test_creation_refuses_an_active_lineage_owned_by_another_campaign(seeded):
    service = _service()
    first = await service.create_campaign(_spec(seeded))
    with pytest.raises(svc.ActiveLineageConflict) as exc:
        await service.create_campaign(_spec(seeded))
    assert seeded["toc_ids"][0] in [t for t, _ in exc.value.lineages]
    assert first.id  # the first campaign is untouched


@db_only
async def test_a_terminal_lineage_no_longer_blocks_a_new_campaign(seeded):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo

    service = _service()
    first = await service.create_campaign(_spec(seeded))
    (target,) = await _targets(first.id)
    async with SessionLocal() as session:
        await targets_repo.set_target_status(
            session, target_id=target.id, new_status="abandoned",
            expected_statuses=["planned"],
            terminal_at=datetime.now(timezone.utc), terminal_reason="test",
        )
        await session.commit()
    second = await service.create_campaign(_spec(seeded))
    assert second.id != first.id


@db_only
async def test_creation_refuses_a_selection_with_no_eligible_source():
    ids = await _seed(complete=False)
    try:
        with pytest.raises(svc.NoEligibleTargets):
            await _service().create_campaign(_spec(ids))
    finally:
        await _purge(ids)


@db_only
async def test_creation_refuses_a_non_api_contract(seeded):
    draft = LaunchContract(
        provider="claude", model="claude-sonnet-4-6", transport="cli",
        session_limit_strategy="pause",
    )
    with pytest.raises(svc.NonApiTransport):
        await _service().create_campaign(_spec(seeded, contract=draft))
    assert await _targets_exist_for_book(seeded) is False


async def _targets_exist_for_book(ids) -> bool:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    async with SessionLocal() as session:
        return bool(await session.scalar(
            select(func.count()).select_from(RegenerationTarget)
            .where(RegenerationTarget.toc_entry_id.in_(ids["toc_ids"]))
        ))


# ─── canary launch ───────────────────────────────────────────────────────


@db_only
async def test_launch_creates_only_canary_jobs():
    """No non-canary job row may exist before the human gate — otherwise an
    ordinary worker claims the bulk before anyone approved it."""
    ids = await _seed(lessons=3)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)

        jobs = await _revision_jobs(campaign.id)
        assert len(jobs) == 1
        (canary,) = [t for t in await _targets(campaign.id) if t.is_canary]
        assert jobs[0].regeneration_target_id == canary.id
        assert sorted(s for s, _, _ in await _statuses(campaign.id)) == [
            "generating", "planned", "planned",
        ]
        assert (await _campaign(campaign.id)).status == "canary_running"
        assert (await _campaign(campaign.id)).canary_launched_at is not None
    finally:
        await _purge(ids)


@db_only
async def test_launch_preflights_every_destination_before_any_spend(monkeypatch):
    """A lesson with no subject mapping blocks the WHOLE launch — including the
    canary — before a job exists."""
    from app.config import settings

    monkeypatch.setattr(settings, "notion_subject_pages", {}, raising=False)
    ids = await _seed(lessons=3, notion=False)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        with pytest.raises(svc.PreflightBlocked) as exc:
            await service.launch_canary(campaign.id)
        # every target is reported, not just the canary
        assert len({f.toc_entry_id for f in exc.value.failures}) == 3
        assert await _revision_jobs(campaign.id) == []
        assert (await _campaign(campaign.id)).status == "draft"
    finally:
        await _purge(ids)


async def test_preflight_refuses_a_lesson_whose_pointer_belongs_to_another_language(
    monkeypatch,
):
    """The campaign preflights THROUGH `discovery`, so the destination rule it
    enforces is the publisher's own — including for a lesson that already
    carries a `notion_lesson_page_id`.

    That column is one language-blind pointer, owned by whichever lineage
    archived the lesson first. The publisher resolves the Lesson Topic beneath
    the target's OWN subject tree and refuses non-retryably when that tree is
    unmapped, so a campaign that let the pointer stand in for the mapping would
    pay to generate an `ru` revision that can then only park.

    Runs without Postgres on purpose: the ordering guarantee ("before any spend")
    is the db_only test above; what this pins is the VERDICT the launch gate
    computes, which a fake session is enough to reach.
    """
    from app.config import settings
    from app.repositories import toc_entries as toc_repo
    from app.services import regeneration_discovery as discovery

    monkeypatch.setattr(
        settings, "notion_subject_pages", {f"{_SUBJECT}|5": "page-uz-5"},
        raising=False,
    )

    async def _no_siblings(session, *, subject, grade):
        return []

    monkeypatch.setattr(toc_repo, "titles_for_subject_grade", _no_siblings)

    toc_entry_id, source_job_id = uuid.uuid4(), uuid.uuid4()
    source = discovery.EligibleRegenerationSource(
        source_job_id=source_job_id, toc_entry_id=toc_entry_id,
        book_id=uuid.uuid4(), subject=_SUBJECT, grade="5", output_language="ru",
        source_publication_version=1, next_expected_version=2,
        source_is_revision=False, book_filename="regen_campaign.pdf",
        section_number="1", section_title="Lesson 0", chapter_title="I bob",
        page_start=4, notion_lesson_page_id="uz-owned-lesson-page", order_index=0,
    )

    async def _candidates(session, *, toc_entry_ids, output_languages):
        return [discovery.SourceCandidate(
            toc_entry_id=toc_entry_id, output_language="ru",
            source=source, reasons=(),
        )]

    monkeypatch.setattr(discovery, "list_source_candidates", _candidates)
    target = SimpleNamespace(
        terminal_at=None, toc_entry_id=toc_entry_id,
        output_language="ru", source_job_id=source_job_id,
    )

    (failure,) = await _service()._preflight(None, [target])

    assert failure.reason == discovery.NO_SUBJECT_PAGE_REASON
    assert failure.toc_entry_id == toc_entry_id
    assert f"ru:{_SUBJECT}|5" in failure.detail


@db_only
async def test_launch_is_idempotent(seeded):
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    first = [j.id for j in await _revision_jobs(campaign.id)]
    await service.launch_canary(campaign.id)
    assert [j.id for j in await _revision_jobs(campaign.id)] == first


@db_only
async def test_launch_resumes_a_canary_whose_job_creation_crashed(monkeypatch):
    """`create_revision_job` commits internally, so a crash mid-wave leaves a
    `generating` target with no job. Re-launching must finish it — exactly
    once."""
    ids = await _seed(lessons=2)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=2))
        real = svc.regeneration_snapshot.create_revision_job
        calls = {"n": 0}

        async def flaky(session, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return await real(session, **kw)

        monkeypatch.setattr(svc.regeneration_snapshot, "create_revision_job", flaky)
        with pytest.raises(RuntimeError):
            await service.launch_canary(campaign.id)
        assert len(await _revision_jobs(campaign.id)) == 1

        monkeypatch.setattr(svc.regeneration_snapshot, "create_revision_job", real)
        await service.launch_canary(campaign.id)
        jobs = await _revision_jobs(campaign.id)
        assert len(jobs) == 2
        assert len({j.regeneration_target_id for j in jobs}) == 2
    finally:
        await _purge(ids)


@db_only
async def test_a_permanently_failing_target_does_not_take_the_wave_with_it():
    """The PERMANENT sibling of the resume test above, and the case that one
    cannot see.

    A transient failure resumes on the next call; a permanent one never does.
    One bulk target whose source link was purged can NEVER be given a revision
    job, and the wave is created in a deterministic order — so without
    per-target isolation the first permanent failure aborts the loop, strands
    every later (healthy) target in `generating` with no job, and re-running
    the action hits the same target first and aborts identically, forever.
    Those stranded targets hold `uq_regeneration_targets_active_lineage`, so no
    future campaign can regenerate those lessons either.

    What must happen instead: the healthy targets get their jobs, the broken
    one lands `generation_failed` with a readable reason (attention-required,
    retryable, abandonable), and the failure is reported — after the healthy
    work is committed, never instead of it.
    """
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.regeneration_target import RegenerationTarget

    ids = await _seed(lessons=3)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)

        canary, broken, healthy = await _targets(campaign.id)  # canonical order
        # The documented child-first purge NULLs this link while the reporting
        # row survives: `create_revision_job` raises `MissingRevisionSource`
        # here on every attempt, forever.
        async with SessionLocal() as session:
            await session.execute(
                update(RegenerationTarget)
                .where(RegenerationTarget.id == broken.id)
                .values(source_job_id=None))
            await session.commit()

        with pytest.raises(svc.PartialWaveRelease) as exc:
            await service.approve_canary(campaign.id, actor="pytest")
        assert [f.target_id for f in exc.value.failures] == [broken.id]
        assert str(broken.id) in str(exc.value)

        jobs = await _revision_jobs(campaign.id)
        # the healthy target ORDERED AFTER the broken one still got its job
        assert {j.regeneration_target_id for j in jobs} == {canary.id, healthy.id}
        by_id = {t.id: t for t in await _targets(campaign.id)}
        assert by_id[healthy.id].status == "generating"
        assert by_id[canary.id].status == "publication_pending"
        # ...and the broken one is visibly failed, not invisible in-flight
        assert by_id[broken.id].status == "generation_failed"
        assert by_id[broken.id].terminal_at is None
        assert "source" in (by_id[broken.id].terminal_reason or "")

        # repeating the action is NOT blocked by the failed target, and creates
        # no duplicate job
        again = await service.approve_canary(campaign.id, actor="pytest")
        assert again.status == "bulk_running"
        assert len(await _revision_jobs(campaign.id)) == 2

        # both operator exits are open on the failed target: retry says so
        # loudly and leaves it retryable, and abandon still works
        with pytest.raises(svc.PartialWaveRelease):
            await service.retry_generation(broken.id)
        assert (await _targets(campaign.id))[1].status == "generation_failed"
        abandoned = await service.abandon(
            broken.id, actor="pytest", reason="source purged")
        assert abandoned.status == "abandoned"
        assert len(await _revision_jobs(campaign.id)) == 2
    finally:
        await _purge(ids)


@db_only
async def test_job_creation_gets_a_dedicated_session_with_no_pending_writes(
    seeded, monkeypatch
):
    """`create_revision_job` COMMITS. If it were handed the session holding the
    campaign's own writes, that commit would publish half a campaign
    transition — and a later rollback could not take it back."""
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    real = svc.regeneration_snapshot.create_revision_job
    seen = {}

    async def spy(session, **kw):
        seen["new"] = list(session.new)
        seen["dirty"] = list(session.dirty)
        seen["in_transaction"] = session.in_transaction()
        # the campaign transition is already COMMITTED and visible elsewhere
        from app.db import SessionLocal
        from app.repositories import regeneration_campaigns as repo
        from app.repositories import regeneration_targets as targets_repo
        async with SessionLocal() as other:
            seen["committed_status"] = (
                await repo.get_campaign(other, campaign.id)).status
            seen["committed_target_status"] = (
                await targets_repo.get_target_for_update(
                    other, kw["target_id"])).status
        return await real(session, **kw)

    monkeypatch.setattr(svc.regeneration_snapshot, "create_revision_job", spy)
    await service.launch_canary(campaign.id)

    assert seen["new"] == [] and seen["dirty"] == []
    assert seen["committed_status"] == "canary_running"
    # ...and the target was already driven to `generating`, committed, BEFORE
    # the job existed: the job is claimable the instant it commits, and a
    # worker that finished first would reconcile a `planned` target — an
    # illegal edge into publication, which wedges it forever.
    assert seen["committed_target_status"] == "generating"


# ─── approval ────────────────────────────────────────────────────────────


async def _finish_canary(campaign_id, *, usable: bool = True):
    """Drive every canary revision job to `done` and reconcile, exactly as the
    worker would — writing the REGENERATED phase rows the pipeline would have
    produced, so the revision is a complete snapshot. No model call involved.
    """
    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo
    from app.services import regeneration_job_state
    from app.services.regeneration_planner import RegenerationPhasePlan

    for job in await _revision_jobs(campaign_id):
        async with SessionLocal() as session:
            if usable:
                from app.models.regeneration_target import RegenerationTarget
                target = await session.get(
                    RegenerationTarget, job.regeneration_target_id)
                plan = RegenerationPhasePlan.from_json(target.phase_plan)
                canonical = ("extract", *flow_for(job.subject))
                have = {r.phase_name for r in await phase_repo.list_for_job(
                    session, job.id)}
                for name in plan.regenerated_phases:
                    if name in have:
                        continue
                    session.add(PhaseOutput(
                        job_id=job.id, phase_name=name,
                        phase_order=canonical.index(name),
                        prompt_hash=f"builtin:{name}:v9", provider="gemini",
                        model_name="gemini-3.6-flash",
                        output_md=f"# regenerated {name}\nbody", status="done",
                    ))
                await session.flush()
            await jobs_repo.set_status(
                session, job.id, "done" if usable else "failed")
            await session.commit()
            await regeneration_job_state.reconcile_revision_job(session, job.id)
            await session.commit()


@db_only
async def test_approval_releases_canaries_and_creates_the_rest_once():
    ids = await _seed(lessons=3)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        assert sorted(s for s, _, _ in await _statuses(campaign.id)) == [
            "awaiting_canary_approval", "planned", "planned"]

        approved = await service.approve_canary(campaign.id, actor="pytest")
        assert approved.approved_at is not None
        statuses = sorted(s for s, _, _ in await _statuses(campaign.id))
        assert statuses == ["generating", "generating", "publication_pending"]
        assert len(await _revision_jobs(campaign.id)) == 3
        assert approved.status == "bulk_running"
    finally:
        await _purge(ids)


@db_only
async def test_repeated_approval_creates_no_duplicate_job_and_restamps_nothing():
    ids = await _seed(lessons=3)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        first = await service.approve_canary(campaign.id, actor="pytest")
        scheduled = {j.id: j.scheduled_at for j in await _revision_jobs(campaign.id)}

        again = await service.approve_canary(campaign.id, actor="pytest")
        assert again.approved_at == first.approved_at
        assert {j.id: j.scheduled_at
                for j in await _revision_jobs(campaign.id)} == scheduled
    finally:
        await _purge(ids)


@db_only
async def test_a_one_target_campaign_has_no_separate_bulk_gate(seeded):
    service = _service()
    campaign = await service.create_campaign(_spec(seeded, canary_size=1))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    approved = await service.approve_canary(campaign.id, actor="pytest")
    assert [s for s, _, _ in await _statuses(campaign.id)] == ["publication_pending"]
    assert len(await _revision_jobs(campaign.id)) == 1
    assert approved.status == "bulk_running"


@db_only
async def test_approval_explicitly_moves_awaiting_canary_targets(seeded):
    """The repair sweep deliberately does NOT cover
    `awaiting_canary_approval -> publication_pending`; approval owns it."""
    from app.db import SessionLocal
    from app.services import regeneration_job_state

    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    await service.approve_canary(campaign.id, actor="pytest")

    async with SessionLocal() as session:
        moved = await regeneration_job_state.reconcile_terminal_revision_jobs(session)
    assert moved == 0
    assert [s for s, _, _ in await _statuses(campaign.id)] == ["publication_pending"]


@db_only
async def test_approval_does_not_release_a_canary_that_is_being_abandoned(seeded):
    """An abandon request beats a successful canary — Task 6's reconciliation
    rule. Approval must never release it: that would publish a public page and
    burn a version number for work an operator has already given up on."""
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    (target,) = await _targets(campaign.id)

    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories import regeneration_targets as targets_repo
    async with SessionLocal() as session:
        await targets_repo.set_target_status(
            session, target_id=target.id,
            new_status="awaiting_canary_approval",
            expected_statuses=["awaiting_canary_approval"],
            abandon_requested_at=datetime.now(timezone.utc),
            abandon_requested_reason="operator gave up")
        await session.commit()

    # `roll_up` reconciles the GENERATION repair's states only, so it leaves an
    # already-reviewable target to the action that owns it — this intent is
    # only reachable here through the raw repository write above; every service
    # path (`cancel`/`reject`/`abandon`) converges an `awaiting_canary_approval`
    # target to `abandoned` in the same transaction that records the intent.
    await service.roll_up(campaign.id)
    assert [t.status for t in await _targets(campaign.id)] == [
        "awaiting_canary_approval"]

    # THE property: approval does not release it. No version is reserved, no
    # publication clock starts, and no public page can follow.
    await service.approve_canary(campaign.id, actor="pytest")
    (after,) = await _targets(campaign.id)
    assert after.status == "awaiting_canary_approval"
    assert after.publication_released_at is None
    assert after.publication_version is None
    assert after.abandon_requested_reason == "operator gave up"

    # and the operator's real exit still converges it — terminal WITH
    # abandonments, never published
    assert (await service.abandon(
        target.id, actor="pytest", reason="operator gave up")).status == "abandoned"
    assert (await service.roll_up(campaign.id)).status == (
        "completed_with_abandonments")


@db_only
async def test_a_failed_canary_is_not_released_by_approval(seeded):
    """Approval is REFUSED, not merely ineffective.

    The campaign parks in `attention_required` — which is one of
    `approve_canary`'s own accepted pre-approval statuses, so nothing about
    the status stops the next click. The refusal has to come from the canary
    ROWS, and it has to leave `approved_at` unstamped: `approved_at` is what
    `trg_regeneration_targets_publication_gate` reads before letting ANY
    target of the campaign publish, so stamping it here would arm every
    remaining lesson behind a canary nobody could review.
    """
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id, usable=False)

    with pytest.raises(svc.CanaryNotReviewable) as excinfo:
        await service.approve_canary(campaign.id, actor="pytest")
    assert excinfo.value.blockers == ("generation_failed",)
    assert "retry or abandon" in str(excinfo.value)

    assert [s for s, _, _ in await _statuses(campaign.id)] == ["generation_failed"]
    after = await service.roll_up(campaign.id)
    assert after.status == "attention_required"
    assert after.approved_at is None, (
        "the publication gate was armed over an unreviewable canary"
    )


@db_only
async def test_a_retried_canary_reopens_the_gate_it_had_closed(seeded):
    """The other half of the refusal: it is not a dead end.

    `attention_required` is an accepted pre-approval status precisely so a
    canary that failed and was repaired can be approved without an operator
    having to do anything else. This drives that repair through the real
    service and proves the gate comes back.
    """
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id, usable=False)
    with pytest.raises(svc.CanaryNotReviewable):
        await service.approve_canary(campaign.id, actor="pytest")

    (target,) = await _targets(campaign.id)
    await service.retry_generation(target.id)
    await _finish_canary(campaign.id)
    assert [s for s, _, _ in await _statuses(campaign.id)] == [
        "awaiting_canary_approval"]

    approved = await service.approve_canary(campaign.id, actor="pytest")
    assert approved.approved_at is not None
    assert [s for s, _, _ in await _statuses(campaign.id)] == [
        "publication_pending"]


@db_only
async def test_the_rollup_retracts_a_gate_whose_canaries_were_all_abandoned():
    """The stale-gate bug, end to end and through the real service.

    A campaign genuinely reaches `awaiting_canary_approval` — reviewable
    canary, bulk target still `planned` — and then the operator abandons the
    canary. Under the flat rollup the targets imply `draft`, which is not a
    legal move out of `awaiting_canary_approval`, so `_apply_derived_status`
    left the row exactly as it found it: a campaign advertising a gate over a
    wave with nothing in it, one compare-and-set from `approved`.
    """
    ids = await _seed(lessons=2)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        assert (await service.roll_up(campaign.id)).status == (
            "awaiting_canary_approval")

        (canary,) = [t for t in await _targets(campaign.id) if t.is_canary]
        await service.abandon(canary.id, actor="pytest", reason="dropped it")

        retracted = await service.roll_up(campaign.id)
        assert retracted.status == "attention_required", (
            "the campaign is still advertising a canary gate that can never open"
        )
        assert retracted.approved_at is None
        with pytest.raises(svc.CanaryNotReviewable) as excinfo:
            await service.approve_canary(campaign.id, actor="pytest")
        assert excinfo.value.reason == "all_abandoned"
    finally:
        await _purge(ids)


@db_only
async def test_a_half_launched_wave_does_not_report_a_gate():
    """One canary reviewable, one that never got a revision job.

    `planned` is also every bulk target's pre-approval status, so this is the
    shape a flat status list cannot see — and the one where reporting the gate
    invites an operator to release the bulk on half a wave.
    """
    ids = await _seed(lessons=3)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=2))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)

        # Push ONE canary back to `planned`, exactly as a mid-wave crash
        # between the campaign's commit and the job's would leave it.
        from app.db import SessionLocal
        from app.models.homework_job import HomeworkJob
        from app.repositories import regeneration_targets as targets_repo

        canaries = [t for t in await _targets(campaign.id) if t.is_canary]
        stalled = canaries[0]
        async with SessionLocal() as session:
            job = await targets_repo.revision_job_for_target(
                session, target_id=stalled.id
            )
            await session.delete(await session.get(HomeworkJob, job.id))
            await targets_repo.set_target_status(
                session, target_id=stalled.id, new_status="planned",
                expected_statuses=["awaiting_canary_approval", "planned"],
            )
            await session.commit()

        rolled = await service.roll_up(campaign.id)
        assert rolled.status == "attention_required", (
            "half a canary wave was reported as a reviewable gate"
        )
        with pytest.raises(svc.CanaryNotReviewable) as excinfo:
            await service.approve_canary(campaign.id, actor="pytest")
        assert excinfo.value.blockers == ("planned",)
        assert "launch" in str(excinfo.value)
    finally:
        await _purge(ids)


@db_only
async def test_bulk_release_staggers_across_waves(monkeypatch):
    """More jobs than one wave must decorrelate — the incident this knob
    exists for was synchronisation, not capacity."""
    from datetime import timezone

    from app.config import settings

    monkeypatch.setattr(settings, "regeneration_launch_wave_size", 2)
    monkeypatch.setattr(settings, "regeneration_launch_wave_interval_seconds", 60)
    ids = await _seed(lessons=5)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        canary_job = (await _revision_jobs(campaign.id))[0]
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")

        jobs = await _revision_jobs(campaign.id)
        bulk = [j for j in jobs if j.id != canary_job.id]
        assert len(bulk) == 4
        base = min(j.scheduled_at for j in bulk).astimezone(timezone.utc)
        offsets = sorted(
            round((j.scheduled_at.astimezone(timezone.utc) - base).total_seconds())
            for j in bulk
        )
        # four jobs, wave size two → 0,0,60,60 (within clock jitter)
        assert offsets[0] == 0 and offsets[1] <= 1
        assert 59 <= offsets[2] <= 61 and 59 <= offsets[3] <= 61
    finally:
        await _purge(ids)


@db_only
async def test_zero_wave_knobs_release_the_whole_bulk_at_once(monkeypatch):
    from datetime import timezone

    from app.config import settings

    monkeypatch.setattr(settings, "regeneration_launch_wave_size", 0)
    monkeypatch.setattr(settings, "regeneration_launch_wave_interval_seconds", 60)
    ids = await _seed(lessons=4)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        canary_job = (await _revision_jobs(campaign.id))[0]
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")
        bulk = [j for j in await _revision_jobs(campaign.id) if j.id != canary_job.id]
        base = min(j.scheduled_at for j in bulk).astimezone(timezone.utc)
        assert all(
            (j.scheduled_at.astimezone(timezone.utc) - base).total_seconds() <= 1
            for j in bulk
        )
    finally:
        await _purge(ids)


@db_only
async def test_canary_and_bulk_revisions_share_one_resolved_launch_contract(
    monkeypatch,
):
    """THE test for "resolved once": both resolution inputs are mutated between
    the canary and the bulk wave. A second resolution would show up as a
    different stamp on the bulk jobs — a single-wave test cannot see it."""
    from app.config import settings
    from app.db import SessionLocal
    from app.repositories import launch_defaults as ld_repo
    from app.schemas.regeneration_contract import (
        LaunchDefaultsSnapshot, resolve_launch_contract,
    )

    _MUTATED = (
        "judge_provider", "judge_model", "extract_provider", "extract_model",
        "solver_provider", "solver_model",
    )
    ids = await _seed(lessons=3)
    restore: dict = {}
    try:
        # Snapshot what is ACTUALLY there before touching it. `launch_defaults`
        # is a repository-global singleton shared with every other test in a
        # whole-suite run; restoring a hardcoded tuple would silently rewrite
        # someone else's row (a failing `test_put_partial_update` leaves it
        # elsewhere) instead of putting back what this test took.
        async with SessionLocal() as session:
            row = await ld_repo.get(session)
            restore = {name: getattr(row, name) for name in _MUTATED}
        service = _service()
        draft = LaunchContract(
            provider="gemini", model="gemini-3.6-flash", transport="api",
            session_limit_strategy="inherit",
        )
        monkeypatch.setattr(settings, "session_limit_strategy", "pause")
        campaign = await service.create_campaign(
            _spec(ids, canary_size=1, contract=draft))
        stored = dict(campaign.launch_contract)
        assert stored["session_limit_strategy"] == "pause"
        await service.launch_canary(campaign.id)
        canary_job = (await _revision_jobs(campaign.id))[0]
        await _finish_canary(campaign.id)

        # ── mutate BOTH resolution inputs between the waves ──
        monkeypatch.setattr(settings, "session_limit_strategy", "switch")
        async with SessionLocal() as session:
            await ld_repo.update(session, {
                "judge_provider": "claude", "judge_model": "claude-opus-4-7",
                "extract_provider": "claude", "extract_model": "claude-opus-4-7",
                "solver_provider": "claude", "solver_model": "claude-opus-4-7",
            })
            await session.commit()
            fresh = LaunchDefaultsSnapshot.model_validate(await ld_repo.get(session))
        # the mutation is REAL: resolving the same draft now differs
        assert resolve_launch_contract(
            draft, defaults=fresh, session_limit_strategy="switch",
        ).model_dump() != stored

        await service.approve_canary(campaign.id, actor="pytest")

        fields = (
            "provider", "model", "transport", "session_limit_strategy",
            "extract_provider", "extract_model", "extract_transport",
            "judge_provider", "judge_model", "judge_transport",
            "solver_provider", "solver_model", "solver_transport",
        )
        jobs = await _revision_jobs(campaign.id)
        assert len(jobs) == 3
        for job in jobs:
            for field in fields:
                assert getattr(job, field) == stored[field], (job.id, field)
        assert any(j.id == canary_job.id for j in jobs)
    finally:
        if restore:
            async with SessionLocal() as session:
                await ld_repo.update(session, restore)
                await session.commit()
        await _purge(ids)


@db_only
async def test_approval_rechecks_the_stored_contract_for_retired_models(seeded):
    """The contract was valid when it was stored. Models retire; the bulk wave
    must not be created against a dead one."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.regeneration_campaign import RegenerationCampaign

    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)

    retired = {**campaign.launch_contract, "judge_provider": "gemini",
               "judge_model": "gemini-2.5-flash"}
    async with SessionLocal() as session:
        await session.execute(
            update(RegenerationCampaign)
            .where(RegenerationCampaign.id == campaign.id)
            .values(launch_contract=retired))
        await session.commit()

    with pytest.raises(svc.RetiredModelRefusal) as exc:
        await service.approve_canary(campaign.id, actor="pytest")
    assert "judge" in str(exc.value)
    # fail CLOSED: nothing was approved and nothing was released
    assert (await _campaign(campaign.id)).approved_at is None
    assert [s for s, _, _ in await _statuses(campaign.id)] == [
        "awaiting_canary_approval"]


@db_only
async def test_approval_after_rejection_is_refused(seeded):
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    await service.reject_canary(campaign.id, actor="pytest", reason="no")
    with pytest.raises(svc.IllegalCampaignAction):
        await service.approve_canary(campaign.id, actor="pytest")


# ─── the reject / cancel / abandon table ─────────────────────────────────


async def _campaign_with_target_in(state: str, ids):
    """One campaign whose single target sits in `state`, reached the way the
    real system reaches it (approved campaign for the publication states)."""
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.repositories import regeneration_campaigns as campaigns_repo
    from app.repositories import regeneration_targets as targets_repo

    service = _service()
    campaign = await service.create_campaign(_spec(ids))
    now = datetime.now(timezone.utc)

    if state == "planned":
        return service, campaign
    if state in ("generating", "awaiting_canary_approval", "generation_failed"):
        await service.launch_canary(campaign.id)
        if state == "generating":
            return service, campaign
        await _finish_canary(campaign.id, usable=(state != "generation_failed"))
        return service, campaign

    # publication states need an approved campaign (the DB trigger enforces it)
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    await service.approve_canary(campaign.id, actor="pytest")
    (target,) = await _targets(campaign.id)
    if state == "publication_pending":
        return service, campaign
    async with SessionLocal() as session:
        if state == "publishing":
            await targets_repo.claim_target_publication(
                session, target_id=target.id, claim_token=uuid.uuid4(),
                lease_seconds=300)
        elif state == "publication_failed":
            await targets_repo.set_target_status(
                session, target_id=target.id, new_status="publishing",
                expected_statuses=["publication_pending"])
            await targets_repo.set_target_status(
                session, target_id=target.id, new_status="publication_failed",
                expected_statuses=["publishing"],
                publication_version=2, notion_page_id=None,
                publication_last_error="notion 500",
                publication_next_attempt_at=now)
        elif state == "published":
            await targets_repo.set_target_status(
                session, target_id=target.id, new_status="publishing",
                expected_statuses=["publication_pending"])
            await targets_repo.set_target_status(
                session, target_id=target.id, new_status="published",
                expected_statuses=["publishing"], publication_version=2,
                notion_page_id="page-v2", terminal_at=now,
                terminal_reason="published")
        elif state == "abandoned":
            await targets_repo.set_target_status(
                session, target_id=target.id, new_status="abandoned",
                expected_statuses=["publication_pending"], terminal_at=now,
                terminal_reason="earlier abandon")
        await session.commit()
    assert campaigns_repo  # imported for symmetry with the service's lock order
    return service, campaign


_REJECTABLE = ["generating", "awaiting_canary_approval", "generation_failed"]


@db_only
@pytest.mark.parametrize("canary_state", _REJECTABLE)
async def test_reject_before_approval_abandons_every_target(canary_state):
    """Both rows of the reject column at once: the canary in each state it can
    be reviewed from, and a `planned` sibling that never launched. No version,
    no publication, no bulk launch."""
    ids = await _seed(lessons=2)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        if canary_state != "generating":
            await _finish_canary(
                campaign.id, usable=(canary_state == "awaiting_canary_approval"))

        await service.reject_canary(campaign.id, actor="pytest", reason="bad canary")

        targets = await _targets(campaign.id)
        assert [t.status for t in targets] == ["abandoned", "abandoned"]
        assert all(t.publication_version is None for t in targets)
        assert all(t.terminal_at is not None for t in targets)
        assert all("bad canary" in (t.terminal_reason or "") for t in targets)
        after = await _campaign(campaign.id)
        assert after.status == "rejected"
        assert after.rejected_at is not None
        assert after.rejected_reason == "bad canary"
        assert after.approved_at is None
    finally:
        await _purge(ids)


@db_only
@pytest.mark.parametrize(
    "state, expected, keeps_version",
    [
        ("planned", "abandoned", False),
        ("generating", "abandoned", False),
        ("publication_pending", "abandoned", True),
        ("publishing", "publishing", True),
        ("generation_failed", "abandoned", False),
        ("publication_failed", "abandoned", True),
        ("published", "published", True),
        ("abandoned", "abandoned", False),
    ],
)
async def test_cancel_of_an_approved_campaign_follows_the_table(
    state, expected, keeps_version
):
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in(state, ids)
        before = (await _targets(campaign.id))[0]
        await service.cancel(campaign.id, actor="pytest", reason="stop it")
        (target,) = await _targets(campaign.id)

        assert target.status == expected
        if keeps_version:
            assert target.publication_version == before.publication_version
        if expected == "abandoned" and state != "abandoned":
            assert "stop it" in (target.terminal_reason or "")
        if state == "publishing":
            # never revoke an unknown remote request — record the intent only
            assert target.abandon_requested_at is not None
            assert target.terminal_at is None
        if state == "published":
            assert target.terminal_reason == "published"
    finally:
        await _purge(ids)


@db_only
@pytest.mark.parametrize(
    "state, expected",
    [
        ("planned", "abandoned"),
        ("generating", "abandoned"),
        ("awaiting_canary_approval", "abandoned"),
        ("publication_pending", "abandoned"),
        ("publishing", "publishing"),
        ("generation_failed", "abandoned"),
        ("publication_failed", "abandoned"),
        ("abandoned", "abandoned"),
    ],
)
async def test_explicit_abandon_follows_the_table(state, expected):
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in(state, ids)
        (target,) = await _targets(campaign.id)
        got = await service.abandon(target.id, actor="pytest", reason="operator")
        assert got.status == expected
        if expected == "abandoned":
            assert got.terminal_at is not None
        else:
            assert got.abandon_requested_at is not None
    finally:
        await _purge(ids)


@db_only
async def test_abandoning_a_published_target_is_illegal():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("published", ids)
        (target,) = await _targets(campaign.id)
        with pytest.raises(svc.IllegalTargetAction):
            await service.abandon(target.id, actor="pytest", reason="operator")
        assert (await _targets(campaign.id))[0].status == "published"
    finally:
        await _purge(ids)


@db_only
async def test_abandon_is_idempotent():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("planned", ids)
        (target,) = await _targets(campaign.id)
        first = await service.abandon(target.id, actor="pytest", reason="operator")
        again = await service.abandon(target.id, actor="pytest", reason="again")
        assert again.status == "abandoned"
        assert again.terminal_at == first.terminal_at
        assert again.terminal_reason == first.terminal_reason
    finally:
        await _purge(ids)


@db_only
async def test_cancel_requests_job_cancellation_for_a_running_revision():
    """A running revision is stopped through the EXISTING safe job path; the
    target stays non-terminal until the job actually converges."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.services import regeneration_job_state

    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generating", ids)
        job = (await _revision_jobs(campaign.id))[0]
        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job.id, "running")
            await session.commit()

        await service.cancel(campaign.id, actor="pytest", reason="stop it")
        async with SessionLocal() as session:
            assert await jobs_repo.get_status(session, job.id) == "cancelling"
        (target,) = await _targets(campaign.id)
        assert target.status == "generating"
        assert target.abandon_requested_at is not None
        assert (await _campaign(campaign.id)).status == "attention_required"

        # the worker converges; reconciliation then completes the abandonment
        async with SessionLocal() as session:
            await jobs_repo.mark_cancelled(session, job.id)
            await session.commit()
            await regeneration_job_state.reconcile_revision_job(session, job.id)
            await session.commit()
        assert (await _targets(campaign.id))[0].status == "abandoned"

        rolled = await service.roll_up(campaign.id)
        assert rolled.status == "cancelled"
        assert rolled.completed_at is not None
    finally:
        await _purge(ids)


@db_only
async def test_cancel_stops_a_pending_revision_immediately():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generating", ids)
        job = (await _revision_jobs(campaign.id))[0]
        await service.cancel(campaign.id, actor="pytest", reason="stop it")
        from app.db import SessionLocal
        from app.repositories import jobs as jobs_repo
        async with SessionLocal() as session:
            assert await jobs_repo.get_status(session, job.id) == "cancelled"
        (target,) = await _targets(campaign.id)
        assert target.status == "abandoned"
        assert (await _campaign(campaign.id)).status == "cancelled"
    finally:
        await _purge(ids)


@db_only
async def test_cancel_is_idempotent():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("planned", ids)
        first = await service.cancel(campaign.id, actor="pytest", reason="stop")
        again = await service.cancel(campaign.id, actor="pytest", reason="stop again")
        assert again.status == first.status == "cancelled"
        assert again.cancel_requested_at == first.cancel_requested_at
        assert again.cancel_requested_reason == "stop"
    finally:
        await _purge(ids)


@db_only
async def test_cancel_of_a_draft_campaign_releases_the_lineage(seeded):
    """A draft holds the active-lineage lock until it is launched or cancelled;
    cancellation is the prominent way out and there is no auto-expiry."""
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.cancel(campaign.id, actor="pytest", reason="wrong selection")
    assert (await _campaign(campaign.id)).status == "cancelled"
    # the lesson is free again
    second = await service.create_campaign(_spec(seeded))
    assert second.id != campaign.id


@db_only
async def test_reject_is_idempotent(seeded):
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    first = await service.reject_canary(campaign.id, actor="pytest", reason="no")
    again = await service.reject_canary(campaign.id, actor="pytest", reason="no again")
    assert again.rejected_at == first.rejected_at
    assert again.rejected_reason == "no"
    assert again.status == "rejected"


@db_only
async def test_rejecting_a_never_launched_draft_is_refused(seeded):
    """`draft -> rejected` is not a legal edge and there is no canary to
    decline — abandoning the targets anyway would leave a `draft` campaign
    whose every target is terminal. Cancellation is the draft's way out."""
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    with pytest.raises(svc.IllegalCampaignAction):
        await service.reject_canary(campaign.id, actor="pytest", reason="oops")
    assert [s for s, _, _ in await _statuses(campaign.id)] == ["planned"]
    assert (await _campaign(campaign.id)).status == "draft"


@db_only
async def test_rejecting_an_approved_campaign_is_refused(seeded):
    service = _service()
    campaign = await service.create_campaign(_spec(seeded))
    await service.launch_canary(campaign.id)
    await _finish_canary(campaign.id)
    await service.approve_canary(campaign.id, actor="pytest")
    with pytest.raises(svc.IllegalCampaignAction):
        await service.reject_canary(campaign.id, actor="pytest", reason="too late")


# ─── retries ─────────────────────────────────────────────────────────────


@db_only
async def test_retry_generation_drives_the_target_before_the_job(monkeypatch):
    """`generation_failed -> generating` is the campaign's edge to own: the
    generic job retry cannot make it, so a successful re-run would otherwise
    wedge the target forever."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generation_failed", ids)
        job = (await _revision_jobs(campaign.id))[0]
        plan_before = (await _targets(campaign.id))[0].phase_plan

        seen = {}
        real_reset = svc.jobs_repo.reset_for_retry

        async def spy(session, job_id, batch_id=None, **kw):
            # a SEPARATE session: what it sees is what is committed
            async with SessionLocal() as other:
                from app.repositories import regeneration_targets as targets_repo
                seen["target_status"] = (
                    await targets_repo.get_target_for_update(
                        other, job.regeneration_target_id)).status
            seen["batch_id"] = batch_id
            return await real_reset(session, job_id, batch_id, **kw)

        monkeypatch.setattr(svc.jobs_repo, "reset_for_retry", spy)
        target = await service.retry_generation(job.regeneration_target_id)
        monkeypatch.undo()
        # the campaign owns `generation_failed -> generating`, and it commits it
        # BEFORE the job becomes claimable again
        assert seen["target_status"] == "generating"
        assert seen["batch_id"] is None  # ck_homework_jobs_revision_no_batch
        assert target.status == "generating"
        assert target.phase_plan == plan_before  # snapshot/plan preserved
        async with SessionLocal() as session:
            fresh = await jobs_repo.get(session, job.id)
        assert fresh.status == "pending"
        assert fresh.attempts == 0
        assert fresh.revision_of_job_id is not None
        assert fresh.batch_id is None
        assert len(await _revision_jobs(campaign.id)) == 1  # no second job
    finally:
        await _purge(ids)


@db_only
async def test_retry_generation_refuses_a_retired_model_and_changes_nothing():
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generation_failed", ids)
        job = (await _revision_jobs(campaign.id))[0]
        async with SessionLocal() as session:
            await session.execute(
                update(HomeworkJob).where(HomeworkJob.id == job.id)
                .values(judge_provider="gemini", judge_model="gemini-2.5-flash"))
            await session.commit()

        with pytest.raises(svc.RetiredModelRefusal) as exc:
            await service.retry_generation(job.regeneration_target_id)
        assert "judge" in str(exc.value)
        assert (await _targets(campaign.id))[0].status == "generation_failed"
        from app.repositories import jobs as jobs_repo
        async with SessionLocal() as session:
            assert (await jobs_repo.get(session, job.id)).status == "failed"
    finally:
        await _purge(ids)


@db_only
async def test_retry_generation_is_idempotent_and_refuses_a_live_target():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generation_failed", ids)
        (target,) = await _targets(campaign.id)
        await service.retry_generation(target.id)
        again = await service.retry_generation(target.id)
        assert again.status == "generating"
        assert len(await _revision_jobs(campaign.id)) == 1
    finally:
        await _purge(ids)


@db_only
async def test_retry_generation_refuses_an_abandoned_target():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generation_failed", ids)
        (target,) = await _targets(campaign.id)
        await service.abandon(target.id, actor="pytest", reason="done with it")
        with pytest.raises(svc.IllegalTargetAction):
            await service.retry_generation(target.id)
    finally:
        await _purge(ids)


@db_only
async def test_retry_publication_clears_backoff_and_never_calls_a_model():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("publication_failed", ids)
        (before,) = await _targets(campaign.id)
        assert before.publication_last_error and before.publication_next_attempt_at

        target = await service.retry_publication(before.id)
        assert target.status == "publication_pending"
        assert target.publication_last_error is None
        assert target.publication_next_attempt_at is None
        assert target.publication_claim_token is None
        # the SAME reserved version and the same revision job
        assert target.publication_version == before.publication_version
        assert len(await _revision_jobs(campaign.id)) == 1
    finally:
        await _purge(ids)


@db_only
async def test_retry_publication_is_idempotent():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("publication_failed", ids)
        (before,) = await _targets(campaign.id)
        first = await service.retry_publication(before.id)
        again = await service.retry_publication(before.id)
        assert again.status == first.status == "publication_pending"
        assert again.publication_version == before.publication_version
    finally:
        await _purge(ids)


@db_only
async def test_retry_publication_refuses_a_target_that_never_published():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("generation_failed", ids)
        (target,) = await _targets(campaign.id)
        with pytest.raises(svc.IllegalTargetAction):
            await service.retry_publication(target.id)
    finally:
        await _purge(ids)


@db_only
async def test_retry_generation_requeues_a_done_but_unusable_revision():
    """`done` WITHOUT a usable snapshot is a DESIGNED `generation_failed`
    (`desired_target_status` maps it there deliberately: publishing a packet
    with a missing phase is the one outcome regeneration exists to prevent).

    The design calls that state attention-required and RETRYABLE, so the retry
    has to actually requeue the existing job. Skipping the requeue because the
    job is `done` returns success, re-derives the target straight back to
    `generation_failed`, and tells the operator nothing.
    """
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo
    from app.repositories import phase_outputs as phase_repo
    from app.services import regeneration_job_state

    ids = await _seed()
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids))
        await service.launch_canary(campaign.id)
        job = (await _revision_jobs(campaign.id))[0]
        # the worker finished, but the regenerated phase never landed
        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job.id, "done")
            await session.commit()
            await regeneration_job_state.reconcile_revision_job(session, job.id)
            await session.commit()
        (before,) = await _targets(campaign.id)
        assert before.status == "generation_failed"
        async with SessionLocal() as session:
            rows_before = len(await phase_repo.list_for_job(session, job.id))
        assert rows_before  # the copied snapshot exists and must survive

        target = await service.retry_generation(before.id)

        assert target.status == "generating"
        assert target.phase_plan == before.phase_plan  # plan preserved
        async with SessionLocal() as session:
            fresh = await jobs_repo.get(session, job.id)
            rows_after = len(await phase_repo.list_for_job(session, job.id))
        assert fresh.status == "pending"  # actually requeued, not a no-op
        assert fresh.attempts == 0
        assert fresh.batch_id is None
        assert rows_after == rows_before  # ONE snapshot, not a second copy
        assert len(await _revision_jobs(campaign.id)) == 1  # ONE job
    finally:
        await _purge(ids)


@db_only
async def test_publication_retry_rolls_up_only_when_it_actually_retried():
    """A campaign parked in `attention_required` by a publication failure must
    come back once the operator retries — and the idempotent already-pending
    call must stay side-effect-free."""
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("publication_failed", ids)
        (target,) = await _targets(campaign.id)
        assert (await service.roll_up(campaign.id)).status == "attention_required"

        rolled = []
        real_roll_up = service.roll_up

        async def counting(campaign_id):
            rolled.append(campaign_id)
            return await real_roll_up(campaign_id)

        service.roll_up = counting
        await service.retry_publication(target.id)
        assert rolled == [campaign.id]
        assert (await _campaign(campaign.id)).status == "bulk_running"

        again = await service.retry_publication(target.id)  # idempotent
        assert again.status == "publication_pending"
        assert rolled == [campaign.id]  # the no-op case rolls nothing up
    finally:
        await _purge(ids)


# ─── rollup / no hidden live targets ─────────────────────────────────────


@db_only
async def test_a_campaign_never_reports_terminal_while_a_target_is_in_flight():
    """A RUNNING revision cannot be cancelled synchronously — the worker
    converges it on its next heartbeat. Until then the campaign must stay
    non-terminal, or the report would show a closed campaign over live work."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    ids = await _seed(lessons=2)
    try:
        service = _service()
        campaign = await service.create_campaign(_spec(ids, canary_size=1))
        await service.launch_canary(campaign.id)
        await _finish_canary(campaign.id)
        await service.approve_canary(campaign.id, actor="pytest")
        bulk = [j for j in await _revision_jobs(campaign.id) if j.status == "pending"]
        async with SessionLocal() as session:
            await jobs_repo.set_status(session, bulk[0].id, "running")
            await session.commit()

        cancelled = await service.cancel(campaign.id, actor="pytest", reason="stop")
        statuses = [s for s, _, _ in await _statuses(campaign.id)]
        assert cancelled.status == "attention_required"
        assert "generating" in statuses  # still live, and visibly so
        assert cancelled.completed_at is None

        # once the worker converges, the campaign may become terminal
        from app.services import regeneration_job_state
        async with SessionLocal() as session:
            await jobs_repo.mark_cancelled(session, bulk[0].id)
            await session.commit()
            await regeneration_job_state.reconcile_revision_job(session, bulk[0].id)
            await session.commit()
        assert (await service.roll_up(campaign.id)).status == "cancelled"
    finally:
        await _purge(ids)


@db_only
async def test_roll_up_leaves_a_publication_failed_target_to_the_operator():
    """`roll_up` is documented as safe to call from any action or report — so
    Task 9's report will run it on every page load. It reconciles GENERATION
    crashes only: a target that has reached a publication state belongs to the
    publisher and to `retry_publication`.

    Reconciling one anyway is legal in the transition table
    (`publication_failed -> publication_pending` is the edge the operator retry
    itself uses) and therefore silently re-queues the delivery WITHOUT clearing
    the backoff: the attention signal disappears from the report, and the
    operator's own retry then hits its idempotent branch and does nothing while
    `publication_next_attempt_at` still holds the target unclaimable.
    """
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("publication_failed", ids)
        (before,) = await _targets(campaign.id)
        assert before.publication_last_error and before.publication_next_attempt_at

        rolled = await service.roll_up(campaign.id)

        (after,) = await _targets(campaign.id)
        assert after.status == "publication_failed"  # untouched
        assert after.publication_last_error == before.publication_last_error
        assert after.publication_next_attempt_at is not None
        assert rolled.status == "attention_required"  # and it SHOWS

        # the operator's retry — and only it — clears the safe fields
        retried = await service.retry_publication(after.id)
        assert retried.status == "publication_pending"
        assert retried.publication_last_error is None
        assert retried.publication_next_attempt_at is None
        assert retried.publication_version == before.publication_version
    finally:
        await _purge(ids)


@db_only
async def test_the_service_refuses_a_direct_terminal_status_write():
    ids = await _seed()
    try:
        service, campaign = await _campaign_with_target_in("publishing", ids)
        with pytest.raises(svc.TerminalCampaignWithLiveTargets):
            await service.set_campaign_status(campaign.id, "cancelled")
        assert (await _campaign(campaign.id)).status != "cancelled"
    finally:
        await _purge(ids)


@db_only
async def test_unknown_campaign_and_target_ids_raise(seeded):
    service = _service()
    with pytest.raises(svc.CampaignNotFound):
        await service.launch_canary(uuid.uuid4())
    with pytest.raises(svc.TargetNotFound):
        await service.abandon(uuid.uuid4(), actor="pytest", reason="x")
