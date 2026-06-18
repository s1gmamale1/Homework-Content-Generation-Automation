import pytest

from app.services.flows import expand_phase_selection, flow_for

SUBJECT = "math-algebra"


def test_bossarena_pulls_in_its_deps():
    ordered, added = expand_phase_selection(SUBJECT, ["boss-arena"])
    # boss-arena needs case-based-preview + flashcards + memory-check
    for dep in ("case-based-preview", "flashcards", "memory-check"):
        assert dep in ordered
        assert dep in added
    assert "boss-arena" in ordered
    assert "boss-arena" not in added  # user-selected, not auto-added
    # ordering matches the subject's canonical flow
    flow = flow_for(SUBJECT)
    assert ordered == [p for p in flow if p in set(ordered)]


def test_full_selection_adds_nothing():
    flow = flow_for(SUBJECT)
    ordered, added = expand_phase_selection(SUBJECT, list(flow))
    assert set(ordered) == set(flow)
    assert added == []


def test_unknown_phase_raises():
    with pytest.raises(ValueError):
        expand_phase_selection(SUBJECT, ["not-a-phase"])


def test_empty_selection_raises():
    with pytest.raises(ValueError):
        expand_phase_selection(SUBJECT, [])
