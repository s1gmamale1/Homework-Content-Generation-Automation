"""Pure state vocabularies, transition rules and rollup for regeneration.

Kept free of I/O, DB access and async so services never duplicate terminality
rules: a campaign or target status is legal, terminal, or attention-requiring
according to this module and nowhere else.

Two facts worth stating up front because they drive most of the logic:

* ``generation_failed`` and ``publication_failed`` are **not** terminal. They are
  attention-required and retryable — only ``published`` and ``abandoned`` end a
  target's life.
* :func:`roll_up_campaign` never returns ``"rejected"``. Rejection is a
  service-set terminal status (an operator declining the canary), and callers
  short-circuit on :data:`TERMINAL_CAMPAIGN_STATUSES` before rolling up, so the
  rollup only ever describes a campaign that is still being derived from its
  targets.

A self-transition (``current == next_status``) is legal for every valid status,
including terminal ones, so idempotent writes never raise.
"""

from __future__ import annotations

from collections.abc import Collection

CAMPAIGN_STATUSES = frozenset({
    "draft", "canary_running", "awaiting_canary_approval", "approved",
    "bulk_running", "attention_required", "completed",
    "completed_with_abandonments", "rejected", "cancelled",
})
TERMINAL_CAMPAIGN_STATUSES = frozenset({
    "completed", "completed_with_abandonments", "rejected", "cancelled",
})
TARGET_STATUSES = frozenset({
    "planned", "generating", "awaiting_canary_approval", "publication_pending",
    "publishing", "published", "generation_failed", "publication_failed",
    "abandoned",
})
TERMINAL_TARGET_STATUSES = frozenset({"published", "abandoned"})
ATTENTION_TARGET_STATUSES = frozenset({"generation_failed", "publication_failed"})

# Target statuses that mean "work is still in flight" after bulk approval.
_IN_FLIGHT_TARGET_STATUSES = frozenset({
    "planned", "generating", "awaiting_canary_approval", "publication_pending",
    "publishing",
})
# Target statuses that may not exist before the campaign is approved (the DB
# trigger forbids them; seeing one here means a caller bug).
_PUBLICATION_TARGET_STATUSES = frozenset({
    "publication_pending", "publishing", "published",
})

_CAMPAIGN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"canary_running", "cancelled"}),
    "canary_running": frozenset({
        "awaiting_canary_approval", "attention_required", "rejected", "cancelled",
    }),
    "awaiting_canary_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({
        "bulk_running", "attention_required", "completed",
        "completed_with_abandonments", "cancelled",
    }),
    "bulk_running": frozenset({
        "attention_required", "completed", "completed_with_abandonments", "cancelled",
    }),
    "attention_required": frozenset({
        "canary_running", "awaiting_canary_approval", "approved", "bulk_running",
        "completed", "completed_with_abandonments", "rejected", "cancelled",
    }),
    "completed": frozenset(),
    "completed_with_abandonments": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}

_TARGET_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"generating", "abandoned"}),
    "generating": frozenset({
        "awaiting_canary_approval", "publication_pending", "generation_failed",
        "abandoned",
    }),
    "awaiting_canary_approval": frozenset({"publication_pending", "abandoned"}),
    "publication_pending": frozenset({"publishing", "abandoned"}),
    "publishing": frozenset({"published", "publication_failed", "abandoned"}),
    "published": frozenset(),
    "generation_failed": frozenset({"generating", "abandoned"}),
    "publication_failed": frozenset({
        "publication_pending", "publishing", "abandoned",
    }),
    "abandoned": frozenset(),
}


class IllegalTransition(ValueError):
    """A transition between two valid statuses that the state machine forbids."""


def _check_campaign_status(status: str) -> str:
    if status not in CAMPAIGN_STATUSES:
        raise ValueError(f"unknown campaign status: {status!r}")
    return status


def _check_target_status(status: str) -> str:
    if status not in TARGET_STATUSES:
        raise ValueError(f"unknown target status: {status!r}")
    return status


def is_terminal_target(status: str) -> bool:
    """Is this target status final? Only ``published`` / ``abandoned`` are."""
    return _check_target_status(status) in TERMINAL_TARGET_STATUSES


def is_terminal_campaign(status: str) -> bool:
    """Is this campaign status final (no further transition allowed)?"""
    return _check_campaign_status(status) in TERMINAL_CAMPAIGN_STATUSES


def can_transition_campaign(current: str, next_status: str) -> bool:
    """May a campaign move ``current -> next_status``? Self-moves are legal."""
    _check_campaign_status(current)
    _check_campaign_status(next_status)
    return next_status == current or next_status in _CAMPAIGN_TRANSITIONS[current]


def can_transition_target(current: str, next_status: str) -> bool:
    """May a target move ``current -> next_status``? Self-moves are legal."""
    _check_target_status(current)
    _check_target_status(next_status)
    return next_status == current or next_status in _TARGET_TRANSITIONS[current]


def assert_campaign_transition(current: str, next_status: str) -> None:
    """Raise :class:`IllegalTransition` unless the campaign move is allowed."""
    if not can_transition_campaign(current, next_status):
        raise IllegalTransition(
            f"illegal campaign transition: {current} -> {next_status}"
        )


def assert_target_transition(current: str, next_status: str) -> None:
    """Raise :class:`IllegalTransition` unless the target move is allowed."""
    if not can_transition_target(current, next_status):
        raise IllegalTransition(
            f"illegal target transition: {current} -> {next_status}"
        )


def roll_up_campaign(
    target_statuses: Collection[str], approved: bool, cancelled: bool
) -> str:
    """Derive a campaign status from its targets' statuses.

    Evaluated top to bottom, first match wins:

    0. no targets, or an unknown status → ``ValueError``
    1. every target terminal → ``cancelled`` (when cancellation was requested and
       nothing was published), else ``completed_with_abandonments`` if anything
       was abandoned, else ``completed``
    2. cancellation requested but targets still in flight → ``attention_required``
       (cancellation is not terminal until every target converges; a terminal
       campaign must never hide a non-terminal target)
    3. before approval → publication states are a caller bug (``ValueError``);
       otherwise ``canary_running`` / ``awaiting_canary_approval`` /
       ``attention_required`` / ``draft``
    4. after approval → ``bulk_running`` while any work is in flight (that beats
       attention-required; the report buckets still show the failures), else
       ``attention_required``

    Never returns ``"rejected"`` — see the module docstring.
    """
    statuses = list(target_statuses)
    if not statuses:
        raise ValueError("cannot roll up a campaign with no targets")
    for status in statuses:
        _check_target_status(status)
    present = set(statuses)

    # 1 — every target has converged.
    if present <= TERMINAL_TARGET_STATUSES:
        if cancelled and "published" not in present:
            return "cancelled"
        if "abandoned" in present:
            return "completed_with_abandonments"
        return "completed"

    # 2 — cancelling, but not yet converged.
    if cancelled:
        return "attention_required"

    # 3 — pre-approval.
    if not approved:
        premature = present & _PUBLICATION_TARGET_STATUSES
        if premature:
            raise ValueError(
                "publication state before campaign approval: "
                f"{sorted(premature)}"
            )
        if "generating" in present:
            return "canary_running"
        if "awaiting_canary_approval" in present:
            return "awaiting_canary_approval"
        if present & ATTENTION_TARGET_STATUSES:
            return "attention_required"
        return "draft"

    # 4 — post-approval.
    if present & _IN_FLIGHT_TARGET_STATUSES:
        return "bulk_running"
    return "attention_required"
