"""Pure phase planning for versioned homework regeneration.

Three responsibilities, all deliberately free of I/O, DB access, async, network
and logging so every later lane (discovery, snapshot copying, orchestration,
publication) can import them without dragging in infrastructure:

* :func:`build_phase_plan` — given a selection of content phases, work out which
  phases a selective regeneration must **re-run** and which it may **copy** from
  the source homework, expanding the selection over the flow's dependency graph.
* :func:`validate_complete_snapshot` — the **single authority** for "is this set
  of phase rows a complete, usable homework snapshot". Discovery and the
  copy/publication gate both call this; neither may redefine row completeness.
* :meth:`RegenerationPhasePlan.to_json` / :meth:`RegenerationPhasePlan.from_json`
  — the **only** serializer for a stored plan. ``regeneration_targets.phase_plan``
  is a JSONB object written by ``to_json`` and read back exclusively through
  ``from_json``. No later lane may hand-roll plan JSON: three independent
  dataclass<->JSON conversions are three independent chances to disagree about
  tuple-vs-list, :class:`DependencyEdge` key names or field order, and the
  column would then mean different things to the orchestrator, the publisher
  and the UI.

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


# Bumped only when the stored shape changes incompatibly. `from_json` refuses
# any other value outright rather than guessing at a migration.
PHASE_PLAN_JSON_VERSION = 1


class UnknownPhaseError(ValueError):
    """A phase name is not part of the subject's selectable content phases."""


class PhasePlanSerializationError(ValueError):
    """A stored ``phase_plan`` payload is not a plan this module produced.

    Raised by :meth:`RegenerationPhasePlan.from_json` for every refusal —
    malformed JSON shape, a foreign version, and structural round-trip
    violations alike. A regeneration lane that trips this has read a plan that
    no ``build_phase_plan`` call could have emitted, so it must fail loudly
    rather than run half a homework.
    """


class ExclusionAcknowledgementRequired(ValueError):
    """Excluding a phase would leave it stale relative to regenerated upstreams.

    The caller must re-submit with ``exclusion_acknowledged=True`` to confirm it
    understands the dependency edges it is breaking.
    """


@dataclass(frozen=True)
class DependencyEdge:
    upstream: str
    downstream: str


# The six phase-name lists, in dataclass field order.
_PLAN_PHASE_LIST_KEYS = (
    "canonical_phases",
    "selected_phases",
    "auto_included_phases",
    "regenerated_phases",
    "copied_phases",
    "excluded_affected_phases",
)
# The three that may only ever name CONTENT phases (`extract` is never
# selectable, auto-included or excluded — it is driven by `refresh_extraction`).
_PLAN_CONTENT_ONLY_KEYS = (
    "selected_phases",
    "auto_included_phases",
    "excluded_affected_phases",
)
# The exact key order `to_json` emits: "version", then the dataclass fields in
# declaration order. Fixed on purpose, so `json.dumps` of two equal plans is
# byte-identical without `sort_keys`.
_PLAN_JSON_KEYS = (
    "version",
    *_PLAN_PHASE_LIST_KEYS,
    "broken_dependency_edges",
    "refresh_extraction",
)


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

    def to_json(self) -> dict:
        """The canonical JSON-safe mapping stored in
        ``regeneration_targets.phase_plan``.

        Plain ``str``/``list``/``dict``/``bool``/``int`` only, with keys emitted
        in the fixed order of :data:`_PLAN_JSON_KEYS` (which mirrors the
        dataclass field order), so ``json.dumps(plan.to_json())`` is byte-stable
        for equal plans without needing ``sort_keys``.
        """
        return {
            "version": PHASE_PLAN_JSON_VERSION,
            "canonical_phases": list(self.canonical_phases),
            "selected_phases": list(self.selected_phases),
            "auto_included_phases": list(self.auto_included_phases),
            "regenerated_phases": list(self.regenerated_phases),
            "copied_phases": list(self.copied_phases),
            "excluded_affected_phases": list(self.excluded_affected_phases),
            "broken_dependency_edges": [
                {"upstream": edge.upstream, "downstream": edge.downstream}
                for edge in self.broken_dependency_edges
            ],
            "refresh_extraction": bool(self.refresh_extraction),
        }

    @classmethod
    def from_json(cls, data) -> "RegenerationPhasePlan":
        """Rebuild a plan from its stored JSON. The ONLY way a plan is read back.

        Strict by design: it never coerces a value and never fills in a default.
        Anything that :func:`build_phase_plan` could not have produced raises
        :class:`PhasePlanSerializationError` naming the offending key or rule —
        a silently-repaired plan would regenerate the wrong phases, and copying
        forward a phase that should have been re-run is invisible in the output.

        It validates **internal consistency only**. It deliberately does NOT
        call ``flows.flow_for`` or otherwise re-derive the subject's flow: a
        stored plan may legitimately predate a flow change, and that drift
        signal belongs to :func:`validate_complete_snapshot`, which grades a
        real snapshot against the deployed flow. Re-deriving here would turn
        every historical campaign row unreadable the day a phase is added.

        ``from_json(plan.to_json()) == plan`` is exact dataclass equality: the
        phase lists come back as ``tuple``s and the edges as
        :class:`DependencyEdge` instances.
        """
        if not isinstance(data, Mapping):
            raise PhasePlanSerializationError(
                "phase_plan must be a JSON mapping, got "
                f"{type(data).__name__}: {data!r}"
            )

        present = set(data)
        missing = [key for key in _PLAN_JSON_KEYS if key not in present]
        if missing:
            raise PhasePlanSerializationError(
                f"phase_plan is missing key(s): {missing}"
            )
        unknown = sorted(present - set(_PLAN_JSON_KEYS))
        if unknown:
            raise PhasePlanSerializationError(
                f"phase_plan has unknown key(s): {unknown}"
            )

        # `type(...) is not int` (not isinstance) so a JSON `true` is refused.
        version = data["version"]
        if type(version) is not int or version != PHASE_PLAN_JSON_VERSION:
            raise PhasePlanSerializationError(
                f"phase_plan version {version!r} is not "
                f"{PHASE_PLAN_JSON_VERSION}"
            )

        # ── shape ────────────────────────────────────────────────────────
        lists: dict[str, list] = {}
        for key in _PLAN_PHASE_LIST_KEYS:
            value = data[key]
            if type(value) is not list or any(type(p) is not str for p in value):
                raise PhasePlanSerializationError(
                    f"phase_plan.{key} must be a list of str, got {value!r}"
                )
            lists[key] = value

        raw_edges = data["broken_dependency_edges"]
        if type(raw_edges) is not list:
            raise PhasePlanSerializationError(
                "phase_plan.broken_dependency_edges must be a list of "
                f"{{'upstream': str, 'downstream': str}} mappings, got {raw_edges!r}"
            )
        edges: list[DependencyEdge] = []
        for raw in raw_edges:
            if (
                not isinstance(raw, Mapping)
                or set(raw) != {"upstream", "downstream"}
                or type(raw["upstream"]) is not str
                or type(raw["downstream"]) is not str
            ):
                raise PhasePlanSerializationError(
                    "phase_plan.broken_dependency_edges entries must have exactly "
                    "the keys 'upstream' and 'downstream' with str values, got "
                    f"{raw!r}"
                )
            edges.append(
                DependencyEdge(
                    upstream=raw["upstream"], downstream=raw["downstream"]
                )
            )

        # `isinstance(True, int)` is True, so a lazy check would let `1` mean
        # "the extraction was refreshed". Test the type exactly.
        refresh_extraction = data["refresh_extraction"]
        if type(refresh_extraction) is not bool:
            raise PhasePlanSerializationError(
                "phase_plan.refresh_extraction must be a real bool, got "
                f"{refresh_extraction!r}"
            )

        # ── structural round-trip rules ──────────────────────────────────
        canonical = lists["canonical_phases"]
        if not canonical:
            raise PhasePlanSerializationError("phase_plan.canonical_phases is empty")
        if len(set(canonical)) != len(canonical):
            raise PhasePlanSerializationError(
                "phase_plan.canonical_phases contains duplicate phase names"
            )
        if canonical[0] != EXTRACT_PHASE:
            raise PhasePlanSerializationError(
                f"phase_plan.canonical_phases must start with {EXTRACT_PHASE!r}, "
                f"got {canonical[0]!r}"
            )
        canonical_set = set(canonical)
        content_set = canonical_set - {EXTRACT_PHASE}
        canonical_index = {name: i for i, name in enumerate(canonical)}

        for key in _PLAN_PHASE_LIST_KEYS[1:]:
            value = lists[key]
            if len(set(value)) != len(value):
                raise PhasePlanSerializationError(
                    f"phase_plan.{key} contains duplicate phase names"
                )

        for key in _PLAN_CONTENT_ONLY_KEYS:
            outside = [p for p in lists[key] if p not in content_set]
            if outside:
                raise PhasePlanSerializationError(
                    f"phase_plan.{key} names {outside}, which are not canonical "
                    "content phase(s)"
                )

        regenerated = lists["regenerated_phases"]
        copied = lists["copied_phases"]
        overlap = sorted(set(regenerated) & set(copied))
        if overlap:
            raise PhasePlanSerializationError(
                "phase_plan.regenerated_phases and phase_plan.copied_phases must "
                f"partition canonical_phases; both claim {overlap}"
            )
        if set(regenerated) | set(copied) != canonical_set:
            raise PhasePlanSerializationError(
                "phase_plan.regenerated_phases and phase_plan.copied_phases must "
                "partition canonical_phases exactly; their union is "
                f"{sorted(set(regenerated) | set(copied))}"
            )

        # Safe now: every name in every list is known to be canonical.
        for key in _PLAN_PHASE_LIST_KEYS[1:]:
            value = lists[key]
            if value != sorted(value, key=canonical_index.__getitem__):
                raise PhasePlanSerializationError(
                    f"phase_plan.{key} is not in canonical order: {value}"
                )

        for edge in edges:
            for side in ("upstream", "downstream"):
                name = getattr(edge, side)
                if name not in canonical_set:
                    raise PhasePlanSerializationError(
                        f"phase_plan.broken_dependency_edges {side} {name!r} is "
                        "not in canonical_phases"
                    )

        if refresh_extraction != (EXTRACT_PHASE in regenerated):
            raise PhasePlanSerializationError(
                f"phase_plan.refresh_extraction={refresh_extraction} disagrees "
                f"with {EXTRACT_PHASE!r} in phase_plan.regenerated_phases"
            )

        stray = sorted(set(lists["selected_phases"]) - set(regenerated))
        if stray:
            raise PhasePlanSerializationError(
                f"phase_plan.selected_phases {stray} are not in "
                "phase_plan.regenerated_phases"
            )

        excluded = lists["excluded_affected_phases"]
        stray = sorted(set(excluded) - set(lists["auto_included_phases"]))
        if stray:
            raise PhasePlanSerializationError(
                f"phase_plan.excluded_affected_phases {stray} are not in "
                "phase_plan.auto_included_phases"
            )
        stray = sorted(set(excluded) - set(copied))
        if stray:
            raise PhasePlanSerializationError(
                f"phase_plan.excluded_affected_phases {stray} are not in "
                "phase_plan.copied_phases"
            )

        excluded_set = set(excluded)
        regenerated_set = set(regenerated)
        for edge in edges:
            if edge.downstream not in excluded_set:
                raise PhasePlanSerializationError(
                    "phase_plan.broken_dependency_edges downstream "
                    f"{edge.downstream!r} is not in "
                    "phase_plan.excluded_affected_phases"
                )
            if edge.upstream not in regenerated_set:
                raise PhasePlanSerializationError(
                    "phase_plan.broken_dependency_edges upstream "
                    f"{edge.upstream!r} is not in phase_plan.regenerated_phases"
                )

        return cls(
            canonical_phases=tuple(canonical),
            selected_phases=tuple(lists["selected_phases"]),
            auto_included_phases=tuple(lists["auto_included_phases"]),
            regenerated_phases=tuple(regenerated),
            copied_phases=tuple(copied),
            excluded_affected_phases=tuple(excluded),
            broken_dependency_edges=tuple(edges),
            refresh_extraction=refresh_extraction,
        )


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
