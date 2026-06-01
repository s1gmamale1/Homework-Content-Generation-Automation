import pytest
from app.services import flows


def test_flow_is_identical_for_every_subject():
    expected = [
        "case-based-preview", "flashcards", "memory-check",
        "practice-rlc", "practice-error-detection",
        "boss-arena", "reflection",
    ]
    for subject in flows.SUPPORTED_SUBJECTS:
        assert flows.flow_for(subject) == expected


def test_no_easy_hard_or_classify():
    assert not hasattr(flows, "SUBJECT_FLOWS")
    text = open(flows.__file__, encoding="utf-8").read()
    assert "has_classify" not in text
    for phase in flows.GENERAL_FLOW:
        assert phase != "classify"


def test_phase_deps_have_no_reading_or_cbp_mode_games():
    assert "reading" not in flows.PHASE_DEPS
    for dead in ("practice-memory-match", "practice-tictactoe",
                 "practice-jigsaw", "practice-sentence"):
        assert dead not in flows.PHASE_DEPS


def test_unknown_subject_raises():
    with pytest.raises(KeyError):
        flows.flow_for("chemistry-unknown")
