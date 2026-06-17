import pytest
from app.services import flows


def test_flow_generates_all_seven_gamified_games():
    # Every job generates the FULL Gamified Practices set (7), skipping none:
    # rlc + error-detection + all four interactive mini-games + boss-arena.
    # Which game "fits" a subject is curated downstream, not by skipping.
    base = ["case-based-preview", "flashcards", "memory-check",
            "practice-rlc", "practice-error-detection"]
    games = ["practice-memory-match", "practice-tictactoe",
             "practice-jigsaw", "practice-sentence"]
    tail = ["boss-arena", "reflection"]
    all_gamified = {"practice-rlc", "practice-error-detection",
                    "practice-memory-match", "practice-tictactoe",
                    "practice-jigsaw", "practice-sentence", "boss-arena"}
    for subject in flows.SUPPORTED_SUBJECTS:
        seq = flows.flow_for(subject)
        assert len(seq) == 11
        assert seq[:5] == base
        assert seq[5:9] == games        # all four mini-games, deterministic order
        assert seq[9:] == tail
        assert all_gamified <= set(seq)  # all 7 gamified present, none skipped
        assert len(seq) == len(set(seq)) # no duplicate phases


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


