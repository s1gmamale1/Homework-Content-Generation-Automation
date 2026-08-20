"""Tests for the pure regeneration campaign/target state rules.

The two transition tables below are re-declared here on purpose: the test is the
independent statement of the contract, and every *non*-listed edge is asserted
illegal, so a future edge added to the implementation cannot silently pass.
"""

import pytest

from app.services import regeneration_states as rs
from app.services.regeneration_states import (
    ATTENTION_TARGET_STATUSES,
    CAMPAIGN_STATUSES,
    TARGET_STATUSES,
    TERMINAL_CAMPAIGN_STATUSES,
    TERMINAL_TARGET_STATUSES,
    IllegalTransition,
    assert_campaign_transition,
    assert_target_transition,
    can_transition_campaign,
    can_transition_target,
    is_terminal_campaign,
    is_terminal_target,
    roll_up_campaign,
)

CAMPAIGN_ALLOWED: dict[str, set[str]] = {
    "draft": {"canary_running", "cancelled"},
    "canary_running": {
        "awaiting_canary_approval", "attention_required", "rejected", "cancelled",
    },
    "awaiting_canary_approval": {"approved", "rejected", "cancelled"},
    "approved": {
        "bulk_running", "attention_required", "completed",
        "completed_with_abandonments", "cancelled",
    },
    "bulk_running": {
        "attention_required", "completed", "completed_with_abandonments", "cancelled",
    },
    "attention_required": {
        "canary_running", "awaiting_canary_approval", "approved", "bulk_running",
        "completed", "completed_with_abandonments", "rejected", "cancelled",
    },
    "completed": set(),
    "completed_with_abandonments": set(),
    "rejected": set(),
    "cancelled": set(),
}

TARGET_ALLOWED: dict[str, set[str]] = {
    "planned": {"generating", "abandoned"},
    "generating": {
        "awaiting_canary_approval", "publication_pending", "generation_failed",
        "abandoned",
    },
    "awaiting_canary_approval": {"publication_pending", "abandoned"},
    "publication_pending": {"publishing", "abandoned"},
    "publishing": {"published", "publication_failed", "abandoned"},
    "published": set(),
    "generation_failed": {"generating", "abandoned"},
    "publication_failed": {"publication_pending", "publishing", "abandoned"},
    "abandoned": set(),
}


# ───────────────────────── vocabularies ─────────────────────────


def test_campaign_vocabulary_is_exactly_the_ten_pinned_statuses():
    assert CAMPAIGN_STATUSES == frozenset({
        "draft", "canary_running", "awaiting_canary_approval", "approved",
        "bulk_running", "attention_required", "completed",
        "completed_with_abandonments", "rejected", "cancelled",
    })
    assert TERMINAL_CAMPAIGN_STATUSES == frozenset({
        "completed", "completed_with_abandonments", "rejected", "cancelled",
    })
    assert TERMINAL_CAMPAIGN_STATUSES <= CAMPAIGN_STATUSES


def test_target_vocabulary_is_exactly_the_nine_pinned_statuses():
    assert TARGET_STATUSES == frozenset({
        "planned", "generating", "awaiting_canary_approval", "publication_pending",
        "publishing", "published", "generation_failed", "publication_failed",
        "abandoned",
    })
    assert TERMINAL_TARGET_STATUSES == frozenset({"published", "abandoned"})
    assert ATTENTION_TARGET_STATUSES == frozenset({
        "generation_failed", "publication_failed",
    })
    assert TERMINAL_TARGET_STATUSES <= TARGET_STATUSES
    assert ATTENTION_TARGET_STATUSES <= TARGET_STATUSES


def test_failure_statuses_are_retryable_not_terminal():
    assert ATTENTION_TARGET_STATUSES.isdisjoint(TERMINAL_TARGET_STATUSES)
    assert is_terminal_target("generation_failed") is False
    assert is_terminal_target("publication_failed") is False


def test_tables_cover_every_status():
    assert set(CAMPAIGN_ALLOWED) == set(CAMPAIGN_STATUSES)
    assert set(TARGET_ALLOWED) == set(TARGET_STATUSES)


# ───────────────────────── terminality ─────────────────────────


@pytest.mark.parametrize("status", sorted(TARGET_STATUSES))
def test_is_terminal_target(status):
    assert is_terminal_target(status) is (status in {"published", "abandoned"})


@pytest.mark.parametrize("status", sorted(CAMPAIGN_STATUSES))
def test_is_terminal_campaign(status):
    assert is_terminal_campaign(status) is (
        status in {"completed", "completed_with_abandonments", "rejected", "cancelled"}
    )


def test_terminality_helpers_reject_unknown_statuses():
    for fn in (is_terminal_target, is_terminal_campaign):
        with pytest.raises(ValueError) as exc:
            fn("banana")
        assert "banana" in str(exc.value)


# ───────────────────────── transitions ─────────────────────────


@pytest.mark.parametrize("current", sorted(CAMPAIGN_STATUSES))
def test_campaign_transitions_match_the_table_exactly(current):
    for nxt in sorted(CAMPAIGN_STATUSES):
        expected = nxt == current or nxt in CAMPAIGN_ALLOWED[current]
        assert can_transition_campaign(current, nxt) is expected, (current, nxt)


@pytest.mark.parametrize("current", sorted(TARGET_STATUSES))
def test_target_transitions_match_the_table_exactly(current):
    for nxt in sorted(TARGET_STATUSES):
        expected = nxt == current or nxt in TARGET_ALLOWED[current]
        assert can_transition_target(current, nxt) is expected, (current, nxt)


@pytest.mark.parametrize("status", sorted(CAMPAIGN_STATUSES))
def test_campaign_self_transition_is_always_legal(status):
    assert can_transition_campaign(status, status) is True
    assert assert_campaign_transition(status, status) is None


@pytest.mark.parametrize("status", sorted(TARGET_STATUSES))
def test_target_self_transition_is_always_legal(status):
    assert can_transition_target(status, status) is True
    assert assert_target_transition(status, status) is None


@pytest.mark.parametrize("current", sorted(CAMPAIGN_STATUSES))
def test_assert_campaign_transition_raises_on_every_illegal_edge(current):
    for nxt in sorted(CAMPAIGN_STATUSES):
        if nxt == current or nxt in CAMPAIGN_ALLOWED[current]:
            assert assert_campaign_transition(current, nxt) is None
        else:
            with pytest.raises(IllegalTransition) as exc:
                assert_campaign_transition(current, nxt)
            assert current in str(exc.value) and nxt in str(exc.value)


@pytest.mark.parametrize("current", sorted(TARGET_STATUSES))
def test_assert_target_transition_raises_on_every_illegal_edge(current):
    for nxt in sorted(TARGET_STATUSES):
        if nxt == current or nxt in TARGET_ALLOWED[current]:
            assert assert_target_transition(current, nxt) is None
        else:
            with pytest.raises(IllegalTransition) as exc:
                assert_target_transition(current, nxt)
            assert current in str(exc.value) and nxt in str(exc.value)


@pytest.mark.parametrize("current,nxt", [
    ("published", "abandoned"),
    ("published", "publishing"),
    ("planned", "published"),
    ("planned", "publication_pending"),
    ("publication_pending", "published"),
    ("generation_failed", "published"),
    ("abandoned", "published"),
    ("abandoned", "generating"),
    ("awaiting_canary_approval", "publishing"),
])
def test_deliberate_target_holes_are_illegal(current, nxt):
    assert can_transition_target(current, nxt) is False
    with pytest.raises(IllegalTransition):
        assert_target_transition(current, nxt)


@pytest.mark.parametrize("current,nxt", [
    ("draft", "approved"),
    ("draft", "completed"),
    ("completed", "cancelled"),
    ("rejected", "draft"),
    ("cancelled", "bulk_running"),
    ("approved", "rejected"),
])
def test_deliberate_campaign_holes_are_illegal(current, nxt):
    assert can_transition_campaign(current, nxt) is False
    with pytest.raises(IllegalTransition):
        assert_campaign_transition(current, nxt)


def test_transition_helpers_reject_unknown_statuses():
    for fn in (can_transition_campaign, assert_campaign_transition):
        with pytest.raises(ValueError) as exc:
            fn("draft", "banana")
        assert "banana" in str(exc.value)
        with pytest.raises(ValueError) as exc:
            fn("banana", "draft")
        assert "banana" in str(exc.value)
    for fn in (can_transition_target, assert_target_transition):
        with pytest.raises(ValueError) as exc:
            fn("planned", "banana")
        assert "banana" in str(exc.value)
        with pytest.raises(ValueError) as exc:
            fn("banana", "planned")
        assert "banana" in str(exc.value)


def test_unknown_status_is_a_plain_value_error_not_an_illegal_transition():
    """`IllegalTransition` subclasses `ValueError`, so `pytest.raises(ValueError)`
    alone cannot tell the two apart. The brief reserves `IllegalTransition` for a
    forbidden edge between two *valid* statuses; a bad vocabulary word is a plain
    `ValueError` and callers key error handling off that distinction."""
    cases = [
        (is_terminal_campaign, ("banana",)),
        (is_terminal_target, ("banana",)),
        (can_transition_campaign, ("draft", "banana")),
        (can_transition_campaign, ("banana", "draft")),
        (can_transition_target, ("planned", "banana")),
        (can_transition_target, ("banana", "planned")),
        (assert_campaign_transition, ("draft", "banana")),
        (assert_campaign_transition, ("banana", "draft")),
        (assert_target_transition, ("planned", "banana")),
        (assert_target_transition, ("banana", "planned")),
        (roll_up_campaign, (["banana"], True, False)),
    ]
    for fn, args in cases:
        with pytest.raises(ValueError) as exc:
            fn(*args)
        assert type(exc.value) is ValueError, (fn.__name__, args, type(exc.value))
        assert not isinstance(exc.value, IllegalTransition), (fn.__name__, args)
        assert "banana" in str(exc.value)


def test_rollup_empty_target_set_is_a_plain_value_error():
    with pytest.raises(ValueError) as exc:
        roll_up_campaign([], approved=False, cancelled=False)
    assert type(exc.value) is ValueError


def test_illegal_transition_is_a_value_error():
    assert issubclass(IllegalTransition, ValueError)


def test_a_campaign_status_is_not_a_target_status_by_accident():
    # A target status accidentally accepted by a campaign helper would be a
    # silent vocabulary bug.
    with pytest.raises(ValueError):
        can_transition_campaign("draft", "publishing")
    with pytest.raises(ValueError):
        can_transition_target("planned", "bulk_running")


# ───────────────────────── rollup ─────────────────────────


def test_rollup_rejects_an_empty_target_set():
    with pytest.raises(ValueError):
        roll_up_campaign([], approved=True, cancelled=False)


def test_rollup_rejects_unknown_target_statuses():
    with pytest.raises(ValueError) as exc:
        roll_up_campaign(["planned", "banana"], approved=True, cancelled=False)
    assert "banana" in str(exc.value)


def test_rollup_parameters_are_positional_or_keyword_in_order():
    """Pins the *positional order* `(target_statuses, approved, cancelled)`.

    Every case must use **differing** bools, otherwise a transposed signature
    binds the same values and the assertion says nothing. `["planned",
    "abandoned"]` is chosen because it is non-terminal, so the two flags select
    genuinely different rules: `cancelled=True` -> rule 2 -> attention_required,
    `approved=True` -> rule 4 (planned is in flight) -> bulk_running.
    """
    # positional, approved=False / cancelled=True -> rule 2
    assert roll_up_campaign(["planned", "abandoned"], False, True) == (
        "attention_required"
    )
    # positional, approved=True / cancelled=False -> rule 4
    assert roll_up_campaign(["planned", "abandoned"], True, False) == "bulk_running"
    # the same two calls by keyword must agree, which pins the parameter *names*
    assert roll_up_campaign(
        target_statuses=["planned", "abandoned"], approved=False, cancelled=True
    ) == "attention_required"
    assert roll_up_campaign(
        target_statuses=["planned", "abandoned"], approved=True, cancelled=False
    ) == "bulk_running"


def test_public_function_parameter_names_are_the_pinned_names():
    """Signature-level guard: every helper is called by keyword at least once, so
    renaming a parameter (or reordering the two-arg helpers) fails here rather
    than silently breaking a positional caller in a later lane."""
    assert is_terminal_target(status="published") is True
    assert is_terminal_campaign(status="completed") is True
    assert can_transition_campaign(current="draft", next_status="canary_running")
    assert can_transition_target(current="planned", next_status="generating")
    assert assert_campaign_transition(
        current="draft", next_status="canary_running"
    ) is None
    assert assert_target_transition(
        current="planned", next_status="generating"
    ) is None
    # ...and the two-arg transition helpers are order-sensitive, not symmetric.
    assert can_transition_campaign("draft", "canary_running") is True
    assert can_transition_campaign("canary_running", "draft") is False
    assert can_transition_target("planned", "generating") is True
    assert can_transition_target("generating", "planned") is False


# rule 1 — all terminal
def test_all_published_is_completed():
    assert roll_up_campaign(
        ["published", "published"], approved=True, cancelled=False
    ) == "completed"


def test_any_abandoned_among_terminals_is_completed_with_abandonments():
    assert roll_up_campaign(
        ["published", "abandoned"], approved=True, cancelled=False
    ) == "completed_with_abandonments"


def test_all_abandoned_and_cancelled_is_cancelled():
    assert roll_up_campaign(
        ["abandoned", "abandoned"], approved=False, cancelled=True
    ) == "cancelled"


def test_cancelled_with_a_published_target_is_completed_with_abandonments():
    assert roll_up_campaign(
        ["published", "abandoned"], approved=True, cancelled=True
    ) == "completed_with_abandonments"


def test_cancelled_with_only_published_targets_is_completed():
    assert roll_up_campaign(
        ["published"], approved=True, cancelled=True
    ) == "completed"


def test_all_abandoned_without_cancellation_is_completed_with_abandonments():
    assert roll_up_campaign(
        ["abandoned"], approved=True, cancelled=False
    ) == "completed_with_abandonments"


# rule 2 — cancelled but not converged
def test_cancelled_with_work_still_in_flight_is_attention_required():
    assert roll_up_campaign(
        ["generating", "published"], approved=True, cancelled=True
    ) == "attention_required"
    assert roll_up_campaign(
        ["planned", "abandoned"], approved=False, cancelled=True
    ) == "attention_required"


def test_cancelled_beats_the_pre_approval_publication_guard():
    # Rule 2 is evaluated before rule 3, so this is attention_required, not a
    # ValueError.
    assert roll_up_campaign(
        ["planned", "publishing"], approved=False, cancelled=True
    ) == "attention_required"


# rule 3 — pre-approval
def test_pre_approval_publication_state_is_a_caller_bug():
    for bad in ("publication_pending", "publishing", "published"):
        with pytest.raises(ValueError) as exc:
            roll_up_campaign(["planned", bad], approved=False, cancelled=False)
        assert "publication state before campaign approval" in str(exc.value)


def test_pre_approval_generating_is_canary_running():
    assert roll_up_campaign(
        ["planned", "generating"], approved=False, cancelled=False
    ) == "canary_running"


def test_pre_approval_generating_beats_awaiting_and_failures():
    assert roll_up_campaign(
        ["generating", "awaiting_canary_approval", "generation_failed"],
        approved=False, cancelled=False,
    ) == "canary_running"


def test_pre_approval_awaiting_canary_approval():
    assert roll_up_campaign(
        ["planned", "awaiting_canary_approval", "generation_failed"],
        approved=False, cancelled=False,
    ) == "awaiting_canary_approval"


def test_pre_approval_failure_is_attention_required():
    assert roll_up_campaign(
        ["planned", "generation_failed"], approved=False, cancelled=False
    ) == "attention_required"
    assert roll_up_campaign(
        ["planned", "publication_failed"], approved=False, cancelled=False
    ) == "attention_required"


def test_pre_approval_planned_only_is_draft():
    assert roll_up_campaign(
        ["planned"], approved=False, cancelled=False
    ) == "draft"
    assert roll_up_campaign(
        ["planned", "abandoned"], approved=False, cancelled=False
    ) == "draft"


# rule 4 — post-approval
@pytest.mark.parametrize("in_flight", [
    "planned", "generating", "awaiting_canary_approval", "publication_pending",
    "publishing",
])
def test_post_approval_work_in_flight_is_bulk_running(in_flight):
    assert roll_up_campaign(
        [in_flight, "published"], approved=True, cancelled=False
    ) == "bulk_running"


def test_post_approval_in_flight_beats_attention_required():
    assert roll_up_campaign(
        ["publishing", "generation_failed", "publication_failed"],
        approved=True, cancelled=False,
    ) == "bulk_running"


def test_post_approval_only_failures_is_attention_required():
    assert roll_up_campaign(
        ["generation_failed", "published"], approved=True, cancelled=False
    ) == "attention_required"
    assert roll_up_campaign(
        ["publication_failed", "abandoned"], approved=True, cancelled=False
    ) == "attention_required"


def test_rollup_only_ever_returns_valid_campaign_statuses():
    seen = set()
    for statuses in (
        ["published"], ["abandoned"], ["planned"], ["planned", "generating"],
        ["planned", "awaiting_canary_approval"], ["planned", "generation_failed"],
        ["publishing", "published"], ["publication_failed", "published"],
    ):
        for approved in (False, True):
            for cancelled in (False, True):
                try:
                    verdict = roll_up_campaign(
                        statuses, approved=approved, cancelled=cancelled
                    )
                except ValueError:
                    continue
                assert verdict in CAMPAIGN_STATUSES
                seen.add(verdict)
    assert "rejected" not in seen


def test_module_docstring_documents_that_rejected_is_never_rolled_up():
    assert "rejected" in (rs.__doc__ or "")
