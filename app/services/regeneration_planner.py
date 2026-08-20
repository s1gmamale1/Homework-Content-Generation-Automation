"""Pure phase planning for versioned homework regeneration.

Two responsibilities, both deliberately free of I/O, DB access, async, network
and logging so every later lane (discovery, snapshot copying, orchestration,
publication) can import them without dragging in infrastructure:

* :func:`build_phase_plan` — given a selection of content phases, work out which
  phases a selective regeneration must **re-run** and which it may **copy** from
  the source homework, expanding the selection over the flow's dependency graph.
* :func:`validate_complete_snapshot` — the **single authority** for "is this set
  of phase rows a complete, usable homework snapshot". Discovery and the
  copy/publication gate both call this; neither may redefine row completeness.

The only app import is :mod:`app.services.flows` (itself pure).

Canonical phase order mirrors the pipeline: ``("extract", *flow_for(subject))``
with ``extract`` at index 0 and content phases at ``1 + index``. ``extract`` is
never *selectable* — a caller that wants it re-run passes
``refresh_extraction=True`` — but it **is** part of ``canonical_phases`` so that
``regenerated_phases`` and ``copied_phases`` partition the whole snapshot
exactly.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.services import flows

EXTRACT_PHASE = "extract"

# Emitted verbatim (no interpolation) when a snapshot carries a phase row that
# the currently deployed flow does not know about.
FLOW_DRIFT_REASON = "source flow differs from the currently deployed flow"


class UnknownPhaseError(ValueError):
    """A phase name is not part of the subject's selectable content phases."""


class ExclusionAcknowledgementRequired(ValueError):
    """Excluding a phase would leave it stale relative to regenerated upstreams.

    The caller must re-submit with ``exclusion_acknowledged=True`` to confirm it
    understands the dependency edges it is breaking.
    """


@dataclass(frozen=True)
class DependencyEdge:
    upstream: str
    downstream: str


@dataclass(frozen=True)
class RegenerationPhasePlan:
    canonical_phases: tuple[str, ...]
    selected_phases: tuple[str, ...]
    auto_included_phases: tuple[str, ...]
    regenerated_phases: tuple[str, ...]
    copied_phases: tuple[str, ...]
    excluded_affected_phases: tuple[str, ...]
    broken_dependency_edges: tuple[DependencyEdge, ...]
    refresh_extraction: bool


class PhaseRowView(Protocol):
    """The read-only shape of a ``phase_outputs`` row this module needs."""

    phase_name: str
    phase_order: int
    status: str
    output_md: str | None
    content_json: dict | None


@dataclass(frozen=True)
class SnapshotValidation:
    usable: bool
    reasons: tuple[str, ...]
    canonical_order: Mapping[str, int]


def _canonical_phases(subject: str) -> tuple[tuple[str, ...], list[str]]:
    """``(canonical tuple, content phase list)`` — raises ``KeyError`` for an
    unknown subject (propagated straight from ``flows.flow_for``)."""
    content_phases = list(flows.flow_for(subject))
    return (EXTRACT_PHASE, *content_phases), content_phases


def _validate_phase_names(
    names: Collection[str], content_phases: Collection[str], *, field: str
) -> set[str]:
    """Deduplicate `names` and reject anything that is not a content phase.

    The caller's ordering is discarded on purpose: every tuple this module
    returns is in canonical order.
    """
    content_set = set(content_phases)
    chosen: set[str] = set()
    for name in names:
        if name == EXTRACT_PHASE:
            raise UnknownPhaseError(
                f"{EXTRACT_PHASE!r} is not a selectable phase in {field}; pass "
                "refresh_extraction=True to re-run the extraction instead"
            )
        if name not in content_set:
            raise UnknownPhaseError(f"unknown phase in {field}: {name!r}")
        chosen.add(name)
    return chosen


def build_phase_plan(
    *,
    subject: str,
    selected_phases: Collection[str],
    excluded_affected_phases: Collection[str] = (),
    refresh_extraction: bool = False,
    exclusion_acknowledged: bool = False,
) -> RegenerationPhasePlan:
    """Plan a selective regeneration: what re-runs, what is copied forward.

    The regeneration set is the transitive **downstream** closure of the
    selection over the flow's dependency edges (a phase whose input changed must
    be re-generated). With ``refresh_extraction=True`` every content phase enters
    the closure before exclusions are applied, because every phase consumes the
    extraction.

    Exclusions are a deliberate escape hatch and do **not** cascade: excluding
    ``X`` removes only ``X``, and phases downstream of ``X`` stay in the closure.
    Any edge that ends at an excluded phase but starts at a regenerated one is
    reported in ``broken_dependency_edges`` and must be acknowledged.

    Deterministic: identical inputs in any order produce an equal plan.
    """
    canonical, content_phases = _canonical_phases(subject)
    canonical_index = {name: i for i, name in enumerate(canonical)}

    selection = _validate_phase_names(
        selected_phases, content_phases, field="selected_phases"
    )
    if not selection and not refresh_extraction:
        raise ValueError(
            "phase selection is empty — pick at least one phase, or pass "
            "refresh_extraction=True"
        )
    exclusions = _validate_phase_names(
        excluded_affected_phases, content_phases, field="excluded_affected_phases"
    )
    both = selection & exclusions
    if both:
        conflicting = [p for p in canonical if p in both]
        raise ValueError(f"cannot both select and exclude: {conflicting}")

    def in_canonical_order(names: Collection[str]) -> tuple[str, ...]:
        wanted = set(names)
        return tuple(p for p in canonical if p in wanted)

    # ── dependency edges (upstream → downstream) ──
    edges: set[DependencyEdge] = set()
    for phase in content_phases:
        for upstream in flows.resolve_phase_deps(phase, content_phases):
            edges.add(DependencyEdge(upstream=upstream, downstream=phase))
        if refresh_extraction:
            edges.add(DependencyEdge(upstream=EXTRACT_PHASE, downstream=phase))

    downstream_of: dict[str, set[str]] = {}
    for edge in edges:
        downstream_of.setdefault(edge.upstream, set()).add(edge.downstream)

    # ── transitive downstream closure over the content phases ──
    closure: set[str] = set(selection)
    if refresh_extraction:
        closure.update(content_phases)
    pending = list(closure)
    while pending:
        current = pending.pop()
        for downstream in downstream_of.get(current, ()):
            if downstream not in closure:
                closure.add(downstream)
                pending.append(downstream)

    auto_included = in_canonical_order(closure - selection)

    # A canonical phase that was never auto-included is an accepted no-op.
    effective_exclusions = exclusions & set(auto_included)

    regenerated_set = closure - effective_exclusions
    if refresh_extraction:
        regenerated_set.add(EXTRACT_PHASE)
    regenerated = in_canonical_order(regenerated_set)
    copied = tuple(p for p in canonical if p not in regenerated_set)

    broken = sorted(
        {
            edge
            for edge in edges
            if edge.upstream in regenerated_set
            and edge.downstream in effective_exclusions
        },
        key=lambda e: (canonical_index[e.downstream], canonical_index[e.upstream]),
    )
    if broken and not exclusion_acknowledged:
        listed = ", ".join(f"{e.upstream} -> {e.downstream}" for e in broken)
        raise ExclusionAcknowledgementRequired(
            "excluded phases will be left stale by regenerated upstreams: "
            f"{listed}; re-submit with exclusion_acknowledged=True to confirm"
        )

    return RegenerationPhasePlan(
        canonical_phases=canonical,
        selected_phases=in_canonical_order(selection),
        auto_included_phases=auto_included,
        regenerated_phases=regenerated,
        copied_phases=copied,
        excluded_affected_phases=in_canonical_order(effective_exclusions),
        broken_dependency_edges=tuple(broken),
        refresh_extraction=refresh_extraction,
    )


def _row_has_deliverable(row: PhaseRowView) -> bool:
    """The deliverable half of ``pipeline._done_phase_md`` (``pipeline.py:183``):
    a non-blank ``output_md`` **or** a non-``None`` ``content_json``. The
    structured/teacher-deck case (``output_md=""`` plus real ``content_json``)
    therefore counts as content.

    ``output_md`` is read directly, exactly as ``pipeline._done_phase_md`` does
    and as :class:`PhaseRowView` requires. ``content_json`` keeps the pipeline's
    ``getattr`` defensiveness, so a row object that omits that optional column is
    treated as ``None`` rather than raising.
    """
    if (row.output_md or "").strip():
        return True
    return getattr(row, "content_json", None) is not None


def validate_complete_snapshot(
    *, subject: str, rows: Collection[PhaseRowView]
) -> SnapshotValidation:
    """Is `rows` a complete, usable homework snapshot for `subject`?

    Required phases are ``("extract", *flow_for(subject))``. A row counts as
    carrying a deliverable under exactly the pipeline's resumability predicate
    (``pipeline._done_phase_md``): ``status == "done"`` and a non-blank
    ``output_md`` **or** a non-``None`` ``content_json`` — so the structured
    (teacher-deck) case with ``output_md=""`` is usable.

    ``reasons`` are stable operator-facing strings consumed by later lanes: at
    most one per phase, in canonical phase order, with the (uninterpolated)
    flow-drift reason emitted at most once and always last. ``canonical_order``
    is the verified phase→index mapping when usable, and empty when not, so
    callers never recompute the ordering from an unvalidated snapshot.

    Unknown subject propagates ``KeyError`` from ``flows.flow_for``.
    """
    canonical, _content_phases = _canonical_phases(subject)
    canonical_order = {name: i for i, name in enumerate(canonical)}

    all_rows = list(rows)
    by_phase: dict[str, list[PhaseRowView]] = {}
    for row in all_rows:
        by_phase.setdefault(row.phase_name, []).append(row)

    reasons: list[str] = []
    for phase in canonical:
        matches = by_phase.get(phase, [])
        if not matches:
            reasons.append(f"missing phase row: {phase}")
            continue
        if len(matches) > 1:
            reasons.append(f"duplicate phase rows: {phase}")
            continue
        row = matches[0]
        if row.status != "done":
            reasons.append(f"phase not done: {phase} (status={row.status})")
            continue
        if not _row_has_deliverable(row):
            reasons.append(f"phase has no content: {phase}")
            continue
        expected = canonical_order[phase]
        if row.phase_order != expected:
            reasons.append(
                f"phase order drifted: {phase} "
                f"(expected {expected}, found {row.phase_order})"
            )

    if any(row.phase_name not in canonical_order for row in all_rows):
        reasons.append(FLOW_DRIFT_REASON)

    usable = not reasons
    return SnapshotValidation(
        usable=usable,
        reasons=tuple(reasons),
        canonical_order=canonical_order if usable else {},
    )
