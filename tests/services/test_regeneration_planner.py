"""Tests for the pure regeneration phase planner.

Covers `build_phase_plan` (which phases a selective regeneration must re-run vs.
copy) and `validate_complete_snapshot` (the single authority for "is this phase
row set a complete, usable homework snapshot"). Both are pure: no DB, no I/O.
"""

from dataclasses import dataclass, fields

import pytest

from app.services import flows
from app.services.regeneration_planner import (
    DependencyEdge,
    ExclusionAcknowledgementRequired,
    PhaseRowView,
    RegenerationPhasePlan,
    SnapshotValidation,
    UnknownPhaseError,
    build_phase_plan,
    validate_complete_snapshot,
)

SUBJECT = "biology"

CONTENT_PHASES: tuple[str, ...] = (
    "case-based-preview",
    "flashcards",
    "memory-check",
    "practice-rlc",
    "practice-error-detection",
    "practice-memory-match",
    "practice-tictactoe",
    "practice-jigsaw",
    "practice-sentence",
    "boss-arena",
    "reflection",
)
CANONICAL: tuple[str, ...] = ("extract", *CONTENT_PHASES)
_ALL_CONTENT = set(CONTENT_PHASES)

# Hand-computed transitive downstream closure of every content phase over the
# live PHASE_DEPS edge set (upstream -> downstream), refresh_extraction=False.
EXPECTED_CLOSURES: dict[str, set[str]] = {
    "case-based-preview": {
        "case-based-preview", "practice-rlc", "practice-error-detection",
        "practice-tictactoe", "practice-jigsaw", "practice-sentence",
        "boss-arena", "reflection",
    },
    "flashcards": _ALL_CONTENT - {"case-based-preview"},
    "memory-check": {
        "memory-check", "practice-error-detection", "practice-memory-match",
        "boss-arena", "reflection",
    },
    "practice-rlc": {"practice-rlc"},
    "practice-error-detection": {"practice-error-detection"},
    "practice-memory-match": {"practice-memory-match"},
    "practice-tictactoe": {"practice-tictactoe"},
    "practice-jigsaw": {"practice-jigsaw"},
    "practice-sentence": {"practice-sentence"},
    "boss-arena": {"boss-arena", "reflection"},
    "reflection": {"reflection"},
}

# The four measured examples pinned by the brief (content phases only).
MEASURED_EXAMPLES: dict[str, tuple[str, ...]] = {
    "flashcards": (
        "flashcards", "memory-check", "practice-rlc",
        "practice-error-detection", "practice-memory-match",
        "practice-tictactoe", "practice-jigsaw", "practice-sentence",
        "boss-arena", "reflection",
    ),
    "memory-check": (
        "memory-check", "practice-error-detection", "practice-memory-match",
        "boss-arena", "reflection",
    ),
    "boss-arena": ("boss-arena", "reflection"),
    "reflection": ("reflection",),
}
MEASURED_COUNTS = {"flashcards": 10, "memory-check": 5, "boss-arena": 2, "reflection": 1}


def _in_canonical_order(names) -> tuple[str, ...]:
    chosen = set(names)
    return tuple(p for p in CANONICAL if p in chosen)


def _content(plan: RegenerationPhasePlan) -> tuple[str, ...]:
    return tuple(p for p in plan.regenerated_phases if p != "extract")


# ───────────────────────── build_phase_plan ─────────────────────────


def test_flow_matches_the_canonical_order_this_module_pins():
    # Guard: if the live flow ever changes, these tests must be revisited.
    assert tuple(flows.flow_for(SUBJECT)) == CONTENT_PHASES
    assert len(flows.SUBJECTS) == 26


@pytest.mark.parametrize("subject", flows.SUBJECTS)
def test_canonical_phases_identical_for_every_subject(subject):
    plan = build_phase_plan(subject=subject, selected_phases=["reflection"])
    assert plan.canonical_phases == CANONICAL
    assert len(plan.canonical_phases) == 12
    assert plan.canonical_phases[0] == "extract"


@pytest.mark.parametrize("subject", flows.SUBJECTS)
def test_measured_closures_hold_for_every_subject(subject):
    for selection, expected in MEASURED_EXAMPLES.items():
        plan = build_phase_plan(subject=subject, selected_phases=[selection])
        assert _content(plan) == expected
        assert len(_content(plan)) == MEASURED_COUNTS[selection]


@pytest.mark.parametrize("phase", CONTENT_PHASES)
def test_every_phase_closure_matches_hand_computed_edge_set(phase):
    plan = build_phase_plan(subject=SUBJECT, selected_phases=[phase])
    assert set(_content(plan)) == EXPECTED_CLOSURES[phase]
    # canonical ordering of the regenerated tuple
    assert _content(plan) == _in_canonical_order(EXPECTED_CLOSURES[phase])


def test_pinned_measured_example_flashcards_copies_only_the_preview():
    plan = build_phase_plan(subject=SUBJECT, selected_phases=["flashcards"])
    assert _content(plan) == MEASURED_EXAMPLES["flashcards"]
    assert plan.copied_phases == ("extract", "case-based-preview")
    assert plan.refresh_extraction is False
    assert plan.selected_phases == ("flashcards",)
    assert plan.auto_included_phases == MEASURED_EXAMPLES["flashcards"][1:]
    assert plan.excluded_affected_phases == ()
    assert plan.broken_dependency_edges == ()


def test_pinned_measured_example_memory_check():
    plan = build_phase_plan(subject=SUBJECT, selected_phases=["memory-check"])
    assert _content(plan) == MEASURED_EXAMPLES["memory-check"]
    assert plan.copied_phases == (
        "extract", "case-based-preview", "flashcards", "practice-rlc",
        "practice-tictactoe", "practice-jigsaw", "practice-sentence",
    )


def test_pinned_measured_example_boss_arena():
    plan = build_phase_plan(subject=SUBJECT, selected_phases=["boss-arena"])
    assert _content(plan) == ("boss-arena", "reflection")
    assert plan.auto_included_phases == ("reflection",)


def test_pinned_measured_example_reflection_is_a_leaf():
    plan = build_phase_plan(subject=SUBJECT, selected_phases=["reflection"])
    assert _content(plan) == ("reflection",)
    assert plan.auto_included_phases == ()
    assert plan.copied_phases == CANONICAL[:-1]


@pytest.mark.parametrize("phase", CONTENT_PHASES)
@pytest.mark.parametrize("refresh", [False, True])
def test_regenerated_and_copied_partition_canonical_phases(phase, refresh):
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=[phase], refresh_extraction=refresh
    )
    regenerated, copied = set(plan.regenerated_phases), set(plan.copied_phases)
    assert regenerated.isdisjoint(copied)
    assert regenerated | copied == set(CANONICAL)
    assert len(plan.regenerated_phases) + len(plan.copied_phases) == len(CANONICAL)
    # both tuples are themselves in canonical order
    assert plan.regenerated_phases == _in_canonical_order(regenerated)
    assert plan.copied_phases == _in_canonical_order(copied)


def test_partition_holds_with_exclusions_too():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["flashcards"],
        excluded_affected_phases=["memory-check"],
        exclusion_acknowledged=True,
    )
    regenerated, copied = set(plan.regenerated_phases), set(plan.copied_phases)
    assert regenerated.isdisjoint(copied)
    assert regenerated | copied == set(CANONICAL)
    assert "memory-check" in copied


def test_plan_is_deterministic_regardless_of_input_order():
    a = build_phase_plan(
        subject=SUBJECT, selected_phases=["reflection", "flashcards", "memory-check"]
    )
    b = build_phase_plan(
        subject=SUBJECT, selected_phases=["memory-check", "reflection", "flashcards"]
    )
    assert a == b
    assert a.selected_phases == ("flashcards", "memory-check", "reflection")


def test_duplicate_selection_and_exclusion_inputs_are_deduplicated():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["flashcards", "flashcards", "flashcards"],
        excluded_affected_phases=["memory-check", "memory-check"],
        exclusion_acknowledged=True,
    )
    assert plan.selected_phases == ("flashcards",)
    assert plan.excluded_affected_phases == ("memory-check",)
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="flashcards", downstream="memory-check"),
    )


def test_unknown_selected_phase_raises_unknown_phase_error():
    with pytest.raises(UnknownPhaseError) as exc:
        build_phase_plan(subject=SUBJECT, selected_phases=["teacher-deck"])
    assert "teacher-deck" in str(exc.value)


def test_extract_in_selection_points_the_caller_at_refresh_extraction():
    with pytest.raises(UnknownPhaseError) as exc:
        build_phase_plan(subject=SUBJECT, selected_phases=["extract"])
    assert "refresh_extraction" in str(exc.value)


def test_extract_in_exclusions_raises_unknown_phase_error():
    with pytest.raises(UnknownPhaseError):
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=["flashcards"],
            excluded_affected_phases=["extract"],
        )


def test_unknown_excluded_phase_raises_unknown_phase_error():
    with pytest.raises(UnknownPhaseError) as exc:
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=["flashcards"],
            excluded_affected_phases=["preview-hard"],
        )
    assert "preview-hard" in str(exc.value)


def test_empty_selection_without_refresh_extraction_is_rejected():
    with pytest.raises(ValueError) as exc:
        build_phase_plan(subject=SUBJECT, selected_phases=[])
    assert "phase selection is empty" in str(exc.value)


def test_empty_selection_with_refresh_extraction_is_legal():
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=[], refresh_extraction=True
    )
    assert plan.selected_phases == ()
    assert plan.regenerated_phases == CANONICAL
    assert plan.copied_phases == ()
    assert plan.auto_included_phases == CONTENT_PHASES


def test_unknown_subject_propagates_key_error():
    with pytest.raises(KeyError):
        build_phase_plan(subject="quidditch", selected_phases=["reflection"])


def test_refresh_extraction_regenerates_everything():
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=["reflection"], refresh_extraction=True
    )
    assert plan.refresh_extraction is True
    assert plan.regenerated_phases == CANONICAL
    assert plan.copied_phases == ()
    assert plan.auto_included_phases == tuple(
        p for p in CONTENT_PHASES if p != "reflection"
    )


def test_exclusion_with_broken_edges_requires_acknowledgement():
    with pytest.raises(ExclusionAcknowledgementRequired) as exc:
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=["flashcards"],
            excluded_affected_phases=["memory-check"],
        )
    message = str(exc.value)
    assert "flashcards" in message and "memory-check" in message


def test_acknowledged_exclusion_returns_the_exact_broken_edges():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["flashcards"],
        excluded_affected_phases=["memory-check"],
        exclusion_acknowledged=True,
    )
    assert plan.excluded_affected_phases == ("memory-check",)
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="flashcards", downstream="memory-check"),
    )
    # exclusion does NOT cascade: memory-check's downstreams stay regenerated
    assert "practice-memory-match" in plan.regenerated_phases
    assert "boss-arena" in plan.regenerated_phases
    assert "memory-check" not in plan.regenerated_phases


def test_broken_edges_are_sorted_by_downstream_then_upstream():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["case-based-preview", "flashcards"],
        excluded_affected_phases=["boss-arena"],
        exclusion_acknowledged=True,
    )
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="case-based-preview", downstream="boss-arena"),
        DependencyEdge(upstream="flashcards", downstream="boss-arena"),
        DependencyEdge(upstream="memory-check", downstream="boss-arena"),
    )


def test_acknowledgement_error_names_every_broken_edge():
    """The brief requires the message to name *every* broken edge, not just the
    first — the operator has to see the full blast radius before confirming."""
    kwargs = dict(
        subject=SUBJECT,
        selected_phases=["case-based-preview", "flashcards"],
        excluded_affected_phases=["memory-check", "boss-arena"],
    )
    expected = build_phase_plan(**kwargs, exclusion_acknowledged=True)
    assert len(expected.broken_dependency_edges) == 3

    with pytest.raises(ExclusionAcknowledgementRequired) as exc:
        build_phase_plan(**kwargs)
    message = str(exc.value)
    # Format-agnostic: every endpoint must be mentioned at least as many times
    # as it occurs across the edges, so listing only one edge cannot satisfy it.
    for phase in CONTENT_PHASES:
        occurrences = sum(
            (edge.upstream == phase) + (edge.downstream == phase)
            for edge in expected.broken_dependency_edges
        )
        assert message.count(phase) >= occurrences, (phase, message)


def test_broken_edge_sort_key_is_downstream_before_upstream():
    """Pins the *key order* itself, which the single-downstream case above
    cannot: here the two candidate keys disagree, so sorting by
    ``(upstream, downstream)`` would put ``case-based-preview -> boss-arena``
    first instead of ``flashcards -> memory-check``."""
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["case-based-preview", "flashcards"],
        excluded_affected_phases=["memory-check", "boss-arena"],
        exclusion_acknowledged=True,
    )
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="flashcards", downstream="memory-check"),
        DependencyEdge(upstream="case-based-preview", downstream="boss-arena"),
        DependencyEdge(upstream="flashcards", downstream="boss-arena"),
    )
    idx = {name: i for i, name in enumerate(CANONICAL)}
    keys = [(idx[e.downstream], idx[e.upstream]) for e in plan.broken_dependency_edges]
    assert keys == sorted(keys)
    # ...and the transposed key would NOT be sorted, so the tuple above is a
    # real discriminator between the two orderings rather than a coincidence.
    assert [(u, d) for d, u in keys] != sorted((u, d) for d, u in keys)


def test_multiple_exclusions_only_report_edges_from_regenerated_upstreams():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["flashcards"],
        excluded_affected_phases=["boss-arena", "memory-check"],
        exclusion_acknowledged=True,
    )
    assert plan.excluded_affected_phases == ("memory-check", "boss-arena")
    # case-based-preview is copied (not regenerated) so its edge is not broken;
    # memory-check is itself excluded, so it is not a regenerated upstream.
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="flashcards", downstream="memory-check"),
        DependencyEdge(upstream="flashcards", downstream="boss-arena"),
    )


def test_acknowledgement_flag_is_irrelevant_when_no_edges_break():
    kwargs = dict(subject=SUBJECT, selected_phases=["reflection"])
    assert build_phase_plan(**kwargs, exclusion_acknowledged=False) == build_phase_plan(
        **kwargs, exclusion_acknowledged=True
    )


def test_cannot_both_select_and_exclude_a_phase():
    with pytest.raises(ValueError) as exc:
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=["flashcards", "memory-check"],
            excluded_affected_phases=["memory-check"],
            exclusion_acknowledged=True,
        )
    assert "cannot both select and exclude" in str(exc.value)


def test_unaffected_exclusion_is_an_accepted_no_op():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=["reflection"],
        excluded_affected_phases=["flashcards"],
    )
    assert plan.excluded_affected_phases == ()
    assert plan.broken_dependency_edges == ()
    assert plan.regenerated_phases == ("reflection",)
    assert "flashcards" in plan.copied_phases


def test_refresh_extraction_excluding_the_preview_breaks_the_extract_edge():
    plan = build_phase_plan(
        subject=SUBJECT,
        selected_phases=[],
        excluded_affected_phases=["case-based-preview"],
        refresh_extraction=True,
        exclusion_acknowledged=True,
    )
    assert plan.broken_dependency_edges == (
        DependencyEdge(upstream="extract", downstream="case-based-preview"),
    )
    assert plan.copied_phases == ("case-based-preview",)
    assert plan.regenerated_phases[0] == "extract"


def test_refresh_extraction_exclusion_without_acknowledgement_raises():
    with pytest.raises(ExclusionAcknowledgementRequired) as exc:
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=[],
            excluded_affected_phases=["case-based-preview"],
            refresh_extraction=True,
        )
    assert "extract" in str(exc.value)


def test_dependency_edge_is_frozen_and_value_equal():
    edge = DependencyEdge(upstream="flashcards", downstream="memory-check")
    assert edge == DependencyEdge("flashcards", "memory-check")
    with pytest.raises(Exception):
        edge.upstream = "boss-arena"  # type: ignore[misc]


# ───────────────────── validate_complete_snapshot ─────────────────────


@dataclass
class Row:
    phase_name: str
    phase_order: int
    status: str = "done"
    output_md: str | None = "content"
    content_json: dict | None = None


class RowWithoutContentJson:
    """A row object that has no `content_json` attribute at all."""

    def __init__(self, phase_name: str, phase_order: int, status: str = "done",
                 output_md: str | None = "content"):
        self.phase_name = phase_name
        self.phase_order = phase_order
        self.status = status
        self.output_md = output_md


CANONICAL_ORDER = {name: i for i, name in enumerate(CANONICAL)}


def _complete_rows() -> list[Row]:
    return [Row(name, i) for i, name in enumerate(CANONICAL)]


def test_complete_snapshot_is_usable_and_reports_canonical_order():
    result = validate_complete_snapshot(subject=SUBJECT, rows=_complete_rows())
    assert isinstance(result, SnapshotValidation)
    assert result.usable is True
    assert result.reasons == ()
    assert dict(result.canonical_order) == CANONICAL_ORDER
    assert result.canonical_order["extract"] == 0
    assert result.canonical_order["reflection"] == 11


def test_row_order_in_the_input_does_not_matter():
    rows = list(reversed(_complete_rows()))
    assert validate_complete_snapshot(subject=SUBJECT, rows=rows).usable is True


def test_missing_row_is_reported_and_clears_canonical_order():
    rows = [r for r in _complete_rows() if r.phase_name != "boss-arena"]
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.usable is False
    assert result.reasons == ("missing phase row: boss-arena",)
    assert dict(result.canonical_order) == {}


def test_duplicate_rows_are_reported():
    rows = [*_complete_rows(), Row("flashcards", 2)]
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("duplicate phase rows: flashcards",)


def test_non_done_row_is_reported_with_its_status():
    rows = _complete_rows()
    rows[3].status = "failed"
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("phase not done: memory-check (status=failed)",)


def test_done_row_without_any_deliverable_is_reported():
    rows = _complete_rows()
    rows[5].output_md = "   "
    rows[5].content_json = None
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("phase has no content: practice-error-detection",)


def test_done_row_with_none_output_md_and_no_content_json_is_reported():
    rows = _complete_rows()
    rows[0].output_md = None
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("phase has no content: extract",)


def test_blank_output_md_with_content_json_is_usable():
    # Mirrors pipeline._done_phase_md — the teacher-deck/structured case.
    rows = _complete_rows()
    rows[4].output_md = ""
    rows[4].content_json = {"cards": []}
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.usable is True
    assert result.reasons == ()


def test_row_object_without_content_json_attribute_is_treated_as_none():
    rows: list = [
        RowWithoutContentJson(name, i) for i, name in enumerate(CANONICAL)
    ]
    assert validate_complete_snapshot(subject=SUBJECT, rows=rows).usable is True

    rows[2].output_md = ""
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("phase has no content: flashcards",)


def test_output_md_is_read_directly_unlike_content_json():
    """The asymmetry with the test above is deliberate, not an oversight.

    ``pipeline._done_phase_md`` defends only ``content_json`` with ``getattr``,
    and :class:`PhaseRowView` pins ``output_md`` as a *required* attribute. A row
    object missing it is a caller bug and must surface, rather than be silently
    graded "phase has no content".
    """
    class RowWithoutOutputMd:
        def __init__(self, phase_name: str, phase_order: int):
            self.phase_name = phase_name
            self.phase_order = phase_order
            self.status = "done"
            self.content_json = None

    rows: list = _complete_rows()
    assert CANONICAL[6] == "practice-memory-match"
    rows[6] = RowWithoutOutputMd("practice-memory-match", 6)
    with pytest.raises(AttributeError):
        validate_complete_snapshot(subject=SUBJECT, rows=rows)


def test_order_drift_is_reported_with_expected_and_found():
    rows = _complete_rows()
    rows[9].phase_order = 42
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == (
        "phase order drifted: practice-sentence (expected 9, found 42)",
    )


def test_flow_drift_reason_is_literal_emitted_once_and_last():
    rows = [*_complete_rows(), Row("teacher-deck", 12), Row("preview-hard", 13)]
    rows[1].status = "running"
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.usable is False
    assert result.reasons == (
        "phase not done: case-based-preview (status=running)",
        "source flow differs from the currently deployed flow",
    )


def test_flow_drift_alone_makes_a_snapshot_unusable():
    rows = [*_complete_rows(), Row("preview-hard", 99)]
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == ("source flow differs from the currently deployed flow",)
    assert result.usable is False
    assert dict(result.canonical_order) == {}


def test_at_most_one_reason_per_phase():
    rows = _complete_rows()
    rows[7].status = "pending"
    rows[7].output_md = ""
    rows[7].phase_order = 99
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == (
        "phase not done: practice-tictactoe (status=pending)",
    )


def test_multiple_defects_are_reported_in_canonical_phase_order():
    rows = [r for r in _complete_rows() if r.phase_name != "practice-rlc"]
    rows[0].status = "running"          # extract
    rows[2].output_md = ""              # flashcards (no content_json)
    rows[-1].phase_order = 0            # reflection
    rows.append(Row("memory-check", 3))  # duplicate
    rows.append(Row("teacher-deck", 77))
    result = validate_complete_snapshot(subject=SUBJECT, rows=rows)
    assert result.reasons == (
        "phase not done: extract (status=running)",
        "phase has no content: flashcards",
        "duplicate phase rows: memory-check",
        "missing phase row: practice-rlc",
        "phase order drifted: reflection (expected 11, found 0)",
        "source flow differs from the currently deployed flow",
    )
    assert result.usable is False


def test_empty_row_set_reports_every_missing_phase():
    result = validate_complete_snapshot(subject=SUBJECT, rows=[])
    assert result.reasons == tuple(f"missing phase row: {p}" for p in CANONICAL)


def test_snapshot_validation_unknown_subject_propagates_key_error():
    with pytest.raises(KeyError):
        validate_complete_snapshot(subject="quidditch", rows=_complete_rows())


# ───────────────────── pinned public surface ─────────────────────


def test_both_public_functions_are_keyword_only():
    with pytest.raises(TypeError):
        build_phase_plan(SUBJECT, ["reflection"])  # type: ignore[misc]
    with pytest.raises(TypeError):
        validate_complete_snapshot(SUBJECT, _complete_rows())  # type: ignore[misc]


def test_dataclass_field_order_is_the_pinned_order():
    assert [f.name for f in fields(DependencyEdge)] == ["upstream", "downstream"]
    assert [f.name for f in fields(RegenerationPhasePlan)] == [
        "canonical_phases",
        "selected_phases",
        "auto_included_phases",
        "regenerated_phases",
        "copied_phases",
        "excluded_affected_phases",
        "broken_dependency_edges",
        "refresh_extraction",
    ]
    assert [f.name for f in fields(SnapshotValidation)] == [
        "usable",
        "reasons",
        "canonical_order",
    ]


def test_phase_row_view_protocol_declares_the_pipeline_row_shape():
    assert set(PhaseRowView.__annotations__) == {
        "phase_name", "phase_order", "status", "output_md", "content_json",
    }


def test_planner_exception_types_are_exact():
    assert issubclass(UnknownPhaseError, ValueError)
    assert issubclass(ExclusionAcknowledgementRequired, ValueError)
    # The two brief-pinned *plain* ValueError cases must not be dressed up as
    # the phase-name error — callers distinguish "bad name" from "bad request".
    with pytest.raises(ValueError) as exc:
        build_phase_plan(subject=SUBJECT, selected_phases=[])
    assert type(exc.value) is ValueError
    with pytest.raises(ValueError) as exc:
        build_phase_plan(
            subject=SUBJECT,
            selected_phases=["flashcards", "memory-check"],
            excluded_affected_phases=["memory-check"],
        )
    assert type(exc.value) is ValueError
