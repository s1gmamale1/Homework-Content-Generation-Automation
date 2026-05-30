"""The pipeline renders a readable Markdown body from each Practice Arc game's
structured output (used in phase rows + bundled homework.md). These tests pin
that every game type produces a non-empty render naming its load-bearing parts.
"""

from __future__ import annotations

from app.schemas.flow_v2 import CaseBasedPreview
from app.schemas.practice_games import CbpModeGame, ErrorDetection, RealLifeChallenge
from app.services.pipeline import _TEACHER_MARK, _synth_md_for_structured

from tests.schemas.test_flow_v2_schemas import _valid_cbp_kwargs
from tests.schemas.test_practice_games_schemas import _cbp_mode, _err, _rlc


def test_synth_rlc_names_role_decisions_and_summary() -> None:
    md = _synth_md_for_structured("practice-rlc", RealLifeChallenge(**_rlc()))
    assert "Real-Life Challenge" in md
    assert "hamshira yordamchisi" in md       # role
    assert "Decision 1" in md and "Decision 2" in md
    assert "Final summary" in md
    # Correct action is labeled as a teacher note (plan §8), not flagged inline.
    assert _TEACHER_MARK in md
    student = "\n".join(l for l in md.splitlines() if _TEACHER_MARK not in l)
    assert "✓" not in student


def test_synth_error_detection_marks_broken_block_and_correction() -> None:
    md = _synth_md_for_structured("practice-error-detection", ErrorDetection(**_err()))
    assert "Error Detection" in md
    assert "Hint" in md                        # student-facing scaffolding stays
    # The flawed block + correction are teacher-only (plan §8).
    assert _TEACHER_MARK in md
    assert "← broken" not in md                # legacy inline flag is gone
    teacher = "\n".join(l for l in md.splitlines() if _TEACHER_MARK in l)
    student = "\n".join(l for l in md.splitlines() if _TEACHER_MARK not in l)
    assert "3x = 6" in teacher                  # correction surfaced, teacher-side
    assert "3x = 6" not in student


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


def test_synth_cbp_interleaves_learning_blocks() -> None:
    md = _synth_md_for_structured("case-based-preview", CaseBasedPreview(**_valid_cbp_kwargs()))
    assert "Learning Block 1" in md and "Learning Block 2" in md
    pos = {k: md.index(k) for k in (
        "Checkpoint 1", "Learning Block 1", "Checkpoint 2", "Learning Block 2", "Checkpoint 3")}
    assert pos["Checkpoint 1"] < pos["Learning Block 1"] < pos["Checkpoint 2"] < pos["Learning Block 2"] < pos["Checkpoint 3"]
