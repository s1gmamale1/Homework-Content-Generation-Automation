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

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Optional

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

#: The one canary status that IS the evidence the gate exists to collect.
CANARY_REVIEWABLE_STATUSES = frozenset({"awaiting_canary_approval"})
#: Canary statuses that neither block the gate nor count as evidence for it.
#: Abandonment was the operator's own decision to drop that lesson, so holding
#: the wave for it would make an abandonment un-recoverable — but a dropped
#: canary is not something anybody reviewed either, which is why
#: :func:`canary_gate_verdict` still refuses a wave made only of them.
CANARY_EXCUSED_STATUSES = frozenset({"abandoned"})

#: What an operator does NEXT about a canary in this status. Keyed by target
#: status so a refusal can name a real move instead of a generic one — the
#: wave most likely to hit the gate (every canary abandoned) is exactly the
#: one where "retry or abandon them" is a dead end.
CANARY_BLOCKER_REMEDIES: dict[str, str] = {
    "planned": "launch the canary wave — this canary has no revision job yet",
    "generating": "wait for this canary's revision to finish",
    "generation_failed": "retry or abandon this canary",
    "publication_pending": "wait for the publisher to deliver this canary",
    "publishing": "wait for the publisher to deliver this canary",
    "published": "roll the campaign up — this canary is already published",
    "publication_failed": "retry this canary's publication, or abandon it",
    "abandoned": (
        "cancel or reject this campaign — an abandoned canary cannot be "
        "retried, so this gate can no longer open"
    ),
}

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
    # `canary_running` and `attention_required` are RETRACTIONS, and they are
    # what makes this status safe to derive. The gate is an invitation, not a
    # commitment: it is one compare-and-set away from `approved`, which is the
    # predicate `trg_regeneration_targets_publication_gate` reads before any
    # target may publish. Whatever put the campaign here, the next rollup has
    # to be able to lower it again — a canary abandoned since, a wave only
    # half launched, an operator override. Without these edges the derived
    # status is unreachable, `_apply_derived_status` leaves the row exactly as
    # it found it, and the campaign goes on advertising a gate that
    # `assert_canary_gate_ready` will refuse. `draft` is deliberately NOT here:
    # nothing in this machine ever returns to draft.
    "awaiting_canary_approval": frozenset({
        "approved", "canary_running", "attention_required", "rejected",
        "cancelled",
    }),
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


@dataclass(frozen=True)
class CanaryGateVerdict:
    """Whether a canary wave may be approved, and if not, what to say about it.

    ``blockers`` is DEDUPED and sorted: it is what an operator (and the API)
    reads, and "generation_failed, generation_failed, generation_failed" says
    nothing "generation_failed" did not. ``total`` carries the count instead.
    """

    ready: bool
    #: ``""`` when ready, else ``no_canaries`` / ``all_abandoned`` /
    #: ``not_reviewable`` — the three genuinely different refusals.
    reason: str
    blockers: tuple[str, ...]
    total: int
    reviewable: int


def canary_gate_verdict(canary_statuses: Collection[str]) -> CanaryGateVerdict:
    """May this canary wave be approved? The ONE definition of that question.

    Shared by :func:`roll_up_campaign` (which decides whether to *report* the
    gate) and the campaign service's ``assert_canary_gate_ready`` (which
    decides whether to *honour* it). Those two answering differently is the
    exact shape of the bug this replaces: a status that invites a click the
    guard behind it then refuses.

    Three refusals, and the third is the one worth naming. An abandoned canary
    is excused rather than blocking — that was the operator's own decision to
    drop the lesson, and holding the wave for it would make an abandonment
    un-recoverable. But it is not evidence either, so a wave made ONLY of
    abandoned canaries is refused: nothing there was ever reviewed, and
    approval releases every remaining lesson in one click.
    """
    statuses = [_check_target_status(s) for s in canary_statuses]
    if not statuses:
        return CanaryGateVerdict(
            ready=False, reason="no_canaries", blockers=(), total=0, reviewable=0
        )
    reviewable = sum(1 for s in statuses if s in CANARY_REVIEWABLE_STATUSES)
    blocking = {
        s for s in statuses
        if s not in CANARY_REVIEWABLE_STATUSES and s not in CANARY_EXCUSED_STATUSES
    }
    if blocking:
        return CanaryGateVerdict(
            ready=False, reason="not_reviewable", blockers=tuple(sorted(blocking)),
            total=len(statuses), reviewable=reviewable,
        )
    if not reviewable:
        return CanaryGateVerdict(
            ready=False, reason="all_abandoned",
            # Nothing is blocking: every row has already reached the
            # operator-chosen terminal exclusion. The gate is closed because
            # the wave contains no review evidence, not because an abandoned
            # row needs another action.
            blockers=(),
            total=len(statuses), reviewable=0,
        )
    return CanaryGateVerdict(
        ready=True, reason="", blockers=(), total=len(statuses),
        reviewable=reviewable,
    )


def is_canary_gate_ready(canary_statuses: Collection[str]) -> bool:
    """Boolean form of :func:`canary_gate_verdict`, for the rollup."""
    return canary_gate_verdict(canary_statuses).ready


def canary_gate_remedy(blockers: Sequence[str]) -> str:
    """The operator's next move for these blocking statuses, deduped in order.

    Empty for an empty list — a caller with no blocker to explain (the
    ``no_canaries`` refusal) supplies its own sentence.
    """
    seen: list[str] = []
    for status in blockers:
        remedy = CANARY_BLOCKER_REMEDIES.get(_check_target_status(status))
        if remedy and remedy not in seen:
            seen.append(remedy)
    return "; ".join(seen)


def roll_up_campaign(
    target_statuses: Collection[str],
    approved: bool,
    cancelled: bool,
    *,
    canary_statuses: Optional[Collection[str]] = None,
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
       otherwise ``canary_running`` (anything still generating), then
       ``attention_required`` (anything failed — it outranks the gate, because
       the gate is an invitation to release the bulk), then the CANARY gate
       question (:func:`canary_gate_verdict` over ``canary_statuses``), else
       ``draft``
    4. after approval → ``bulk_running`` while any work is in flight (that beats
       attention-required; the report buckets still show the failures), else
       ``attention_required``

    ``canary_statuses`` is the subset of ``target_statuses`` belonging to
    canary rows. ``None`` means the caller does not know which rows are
    canaries and the pre-approval reading falls back to the target statuses
    alone; every production caller passes it, because the gate is a claim
    about the CANARIES and a flat list cannot tell a half-launched wave from a
    reviewable one.

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
        # Attention BEATS the gate, and the order is the whole point.
        # `awaiting_canary_approval` is not a description, it is an INVITATION:
        # the operator's next click approves, and approval releases the entire
        # bulk wave. A canary wave holding one reviewable revision and one
        # `generation_failed` has nothing to review for that second lesson, so
        # reporting the gate would offer a decision over evidence that does not
        # exist. Failed first means such a wave reads `attention_required` —
        # retry it or abandon it, and the gate reappears once every canary is
        # genuinely reviewable.
        if present & ATTENTION_TARGET_STATUSES:
            return "attention_required"
        if canary_statuses is None:
            # No canary information: report on the target statuses alone, which
            # is all this function can honestly say. Every production caller
            # passes the canary rows — see the branch below for why.
            if "awaiting_canary_approval" in present:
                return "awaiting_canary_approval"
            return "draft"
        # The flat status list CANNOT answer the gate question, and the two
        # ways it gets it wrong are opposite. Pre-approval every bulk target
        # sits in `planned` by definition, so `planned` beside an awaiting
        # canary is indistinguishable from a canary that never got a job —
        # the first is the gate, the second is a half-launched wave. And a
        # campaign whose every canary was abandoned reads as `draft` (nothing
        # failed, nothing running, nothing awaiting), which is not reachable
        # from `awaiting_canary_approval` and so leaves the campaign
        # advertising a gate that can never open again.
        verdict = canary_gate_verdict(canary_statuses)
        if verdict.ready:
            return "awaiting_canary_approval"
        if verdict.reason == "all_abandoned":
            # Every canary dropped, with live bulk targets behind them (rule 1
            # already returned for a wholly terminal campaign). No reviewable
            # revision will ever arrive: it needs an operator decision.
            return "attention_required"
        if "awaiting_canary_approval" in present:
            # Something IS reviewable but the wave as a whole is not — a canary
            # still `planned`, most likely. Approval is not per lesson.
            return "attention_required"
        # Nothing has started yet. A campaign whose every row is `planned` is a
        # draft, not something to attend to.
        return "draft"

    # 4 — post-approval.
    if present & _IN_FLIGHT_TARGET_STATUSES:
        return "bulk_running"
    return "attention_required"
