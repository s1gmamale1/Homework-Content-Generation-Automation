"""The pipeline renders a readable Markdown body from each Practice Arc game's
structured output (used in phase rows + bundled homework.md). These tests pin
that every game type produces a non-empty render naming its load-bearing parts.
"""

from __future__ import annotations

from app.schemas.practice_games import CbpModeGame, ErrorDetection, RealLifeChallenge
from app.services.pipeline import _synth_md_for_structured

from tests.schemas.test_practice_games_schemas import _cbp_mode, _err, _rlc


def test_synth_rlc_names_role_decisions_and_summary() -> None:
    md = _synth_md_for_structured("practice-rlc", RealLifeChallenge(**_rlc()))
    assert "Real-Life Challenge" in md
    assert "hamshira yordamchisi" in md       # role
    assert "Decision 1" in md and "Decision 2" in md
    assert "Final summary" in md
    assert "✓" in md                          # correct option marked


def test_synth_error_detection_marks_broken_block_and_correction() -> None:
    md = _synth_md_for_structured("practice-error-detection", ErrorDetection(**_err()))
    assert "Error Detection" in md
    assert "← broken" in md                    # the one error block flagged
    assert "3x = 6" in md                      # correction surfaced
    assert "Hint" in md


def test_synth_cbp_mode_names_mode_and_checkpoints() -> None:
    for phase, mode in (
        ("practice-memory-match", "memory_match"),
        ("practice-tictactoe", "tictactoe"),
        ("practice-jigsaw", "jigsaw"),
        ("practice-sentence", "sentence_fill"),
    ):
        md = _synth_md_for_structured(phase, CbpModeGame(**_cbp_mode(interaction_mode=mode)))
        assert mode in md, f"{phase} render should name its interaction mode"
        assert "Checkpoint 1" in md
        assert "Decision Process Explanation" in md
