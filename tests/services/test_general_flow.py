import pytest
from app.services import flows


def test_flow_is_8_phases_with_subject_game():
    base = ["case-based-preview", "flashcards", "memory-check",
            "practice-rlc", "practice-error-detection"]
    tail = ["boss-arena", "reflection"]
    for subject in flows.SUPPORTED_SUBJECTS:
        seq = flows.flow_for(subject)
        assert len(seq) == 8
        assert seq[:5] == base and seq[6:] == tail
        assert seq[5] == flows.SUBJECT_GAME[subject]   # subject-matched game at position 5


def test_every_subject_game_is_registered_and_has_prompt():
    import pathlib
    from app.services.prompts import get_prompt
    gdir = pathlib.Path(flows.__file__).resolve().parents[2] / "prompts" / "_general"
    for subject, game in flows.SUBJECT_GAME.items():
        prompt = get_prompt(subject, game)
        assert prompt, f"{game} has no prompt for subject {subject}"
        assert (gdir / f"{game}.md").is_file(), f"{game}.md missing in _general"


def test_phase_deps_have_no_reading_but_have_games():
    assert "reading" not in flows.PHASE_DEPS
    for game in ("practice-memory-match", "practice-tictactoe",
                 "practice-jigsaw", "practice-sentence"):
        assert game in flows.PHASE_DEPS


def test_no_easy_hard_or_classify():
    assert not hasattr(flows, "SUBJECT_FLOWS")
    text = open(flows.__file__, encoding="utf-8").read()
    assert "has_classify" not in text
    for phase in flows.flow_for("physics"):
        assert phase != "classify"


def test_unknown_subject_raises():
    with pytest.raises(KeyError):
        flows.flow_for("chemistry-unknown")


