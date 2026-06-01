"""The pipeline renders a readable Markdown body from each Practice Arc game's
structured output (used in phase rows + bundled homework.md). These tests pin
that every game type produces a non-empty render naming its load-bearing parts.
"""

from __future__ import annotations

from app.schemas.flow_v2 import CaseBasedPreview
from app.schemas.practice_games import CbpModeGame, ErrorDetection, RealLifeChallenge
from app.schemas.practice_games import (
    MemoryMatchPayload, MemoryMatchPair, JigsawPayload, JigsawPiece,
    SentenceFillPayload, TicTacToePayload, GameChoice,
)
from app.services.pipeline import _TEACHER_MARK, _synth_md_for_structured

from tests.schemas.test_flow_v2_schemas import _valid_cbp_kwargs
from tests.schemas.test_practice_games_schemas import _err, _rlc


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


def _compact_game(mode):
    payloads = {
        "memory_match": MemoryMatchPayload(
            pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)]),
        "jigsaw": JigsawPayload(
            pieces=[JigsawPiece(id=f"p{i}", content=f"piece {i}") for i in range(3)],
            allowed_assembly_types=["theorem ↔ condition"]),
        "sentence_fill": SentenceFillPayload(
            sentence="A ____ B",
            chips=[GameChoice(label="right", is_correct=True),
                   GameChoice(label="wrong1", reason="nope1"),
                   GameChoice(label="wrong2", reason="nope2")]),
        "tictactoe": TicTacToePayload(
            cells=[GameChoice(label=f"cell{i}", is_correct=(i == 0),
                              reason=(None if i == 0 else f"bad{i}")) for i in range(9)]),
    }
    return CbpModeGame(
        title=f"{mode} game",
        source_concept_ids=["concept_x"],
        interaction_mode=mode,
        instruction=f"Do the {mode} task.",
        interaction_payload=payloads[mode],
        why_prompt="Explain the concept, the link, and the trap.",
    )


def test_synth_cbp_mode_compact_render_and_hides_answers() -> None:
    for phase, mode in (
        ("practice-memory-match", "memory_match"),
        ("practice-tictactoe", "tictactoe"),
        ("practice-jigsaw", "jigsaw"),
        ("practice-sentence", "sentence_fill"),
    ):
        md = _synth_md_for_structured(phase, _compact_game(mode))
        assert mode in md, f"{phase} render should name its interaction mode"
        assert f"{mode} game" in md            # title rendered
        assert f"Do the {mode} task." in md    # instruction rendered
        assert "Explain the concept" in md     # why_prompt rendered
        # Compact shape: no full-CBP scaffolding leaks in.
        assert "Checkpoint" not in md
        assert "Decision Process Explanation" not in md
        assert "Learning Block" not in md
        # §8: correct-option flags are teacher-only.
        student = "\n".join(l for l in md.splitlines() if _TEACHER_MARK not in l)
        if mode == "tictactoe":
            assert _TEACHER_MARK in md and "Correct cell" in md
            assert "Correct cell" not in student
        if mode == "sentence_fill":
            assert _TEACHER_MARK in md and "Correct chip" in md
            assert "Correct chip" not in student


def test_synth_cbp_interleaves_learning_blocks() -> None:
    md = _synth_md_for_structured("case-based-preview", CaseBasedPreview(**_valid_cbp_kwargs()))
    assert "Learning Block 1" in md and "Learning Block 2" in md
    pos = {k: md.index(k) for k in (
        "Checkpoint 1", "Learning Block 1", "Checkpoint 2", "Learning Block 2", "Checkpoint 3")}
    assert pos["Checkpoint 1"] < pos["Learning Block 1"] < pos["Checkpoint 2"] < pos["Learning Block 2"] < pos["Checkpoint 3"]
