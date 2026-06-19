import pytest

from app.services.flows import order_phase_selection, flow_for

SUBJECT = "math-algebra"


def test_runs_exactly_what_is_picked_no_deps_added():
    # boss-arena would previously pull in case-based-preview/flashcards/memory-check;
    # now we run exactly what the user picked, nothing more.
    ordered = order_phase_selection(SUBJECT, ["boss-arena"])
    assert ordered == ["boss-arena"]


def test_orders_by_canonical_flow_regardless_of_input_order():
    flow = flow_for(SUBJECT)
    scrambled = ["boss-arena", "flashcards", "case-based-preview"]
    ordered = order_phase_selection(SUBJECT, scrambled)
    assert ordered == [p for p in flow if p in set(scrambled)]


def test_dedups_repeated_phases():
    ordered = order_phase_selection(SUBJECT, ["flashcards", "flashcards"])
    assert ordered == ["flashcards"]


def test_full_selection_returns_full_flow():
    flow = flow_for(SUBJECT)
    assert order_phase_selection(SUBJECT, list(flow)) == list(flow)


def test_unknown_phase_raises():
    with pytest.raises(ValueError):
        order_phase_selection(SUBJECT, ["not-a-phase"])


def test_empty_selection_raises():
    with pytest.raises(ValueError):
        order_phase_selection(SUBJECT, [])
