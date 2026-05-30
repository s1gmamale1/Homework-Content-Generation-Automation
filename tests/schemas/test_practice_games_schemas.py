"""Unit tests for the Practice Arc game content schemas
(``app.schemas.practice_games``).

The Practice Arc replaces the single generic ``game-breaks`` with typed,
source-traced conceptual games (PR-3). Three content contracts cover the six
games from ``docs/Infra_prompts/Gamified Practices``:

- ``RealLifeChallenge`` — first-person expert decision game (role + 2-4 decisions,
  each MC + Why + Confidence). Standalone mechanic; absorbs the legacy
  ``real-life`` phase.
- ``ErrorDetection`` — spot-the-broken-piece + type-the-correction game. Exactly
  one error per task; Why prompt mandatory for math/science.
- ``CbpModeGame`` — the four "Case-Based Preview Interaction Mode" games
  (Memory Matching, Jigsaw, Sentence Filling, TicTacToe). Shares the CBP
  contract (3 MCQ checkpoints + DPE + correct/wrong simulation) plus an
  ``interaction_mode`` discriminator.

These tests pin the spec non-negotiables, not the prose.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.practice_games import (
    CbpModeGame,
    ErrorBlock,
    ErrorDetection,
    RealLifeChallenge,
    RlcDecision,
)


# ─────────────────────────────────────────────────────────────────────
# Real-Life Challenge
# ─────────────────────────────────────────────────────────────────────


def _decision(**overrides) -> dict:
    base = dict(
        question="Asosiy sabab nimada bo'lishi mumkin?",
        options=[
            "Kislorod yetishmovchiligi",
            "Suvsizlanish",
            "Yuqori harorat",
            "Charchoq",
        ],
        correct_option=0,
        expected_reasoning=["low_oxygen", "cellular_respiration"],
        correct_feedback="Mahalliy ekspert siz bilan rozi.",
        partial_feedback="Aniq sabab to'g'ri, lekin mexanizm haqida ko'proq fikrlang.",
        wrong_feedback="Hali emas. Lablar nima uchun ko'karadi?",
    )
    base.update(overrides)
    return base


def _rlc(**overrides) -> dict:
    base = dict(
        concept_ids=["cellular-respiration", "oxygen-transport"],
        role="hamshira yordamchisi",
        task="Bemorda nafas olish qiyinligi sababini taxmin qilish.",
        context="Bemor 14 yoshda, kecha sportzalda mashq qilgan, hozir tinch o'tiribdi.",
        prediction_prompt="Sizningcha bemorda nima bo'lgan?",
        decisions=[_decision(), _decision(correct_option=1)],
        final_summary="Kuchli fikrlash, agar talaba kislorod bilan bog'lasa.",
    )
    base.update(overrides)
    return base


def test_rlc_valid() -> None:
    rlc = RealLifeChallenge(**_rlc())
    assert rlc.role and rlc.task and rlc.context and rlc.prediction_prompt
    assert 2 <= len(rlc.decisions) <= 4


def test_rlc_requires_at_least_one_concept_id() -> None:
    with pytest.raises(ValidationError):
        RealLifeChallenge(**_rlc(concept_ids=[]))


def test_rlc_requires_2_to_4_decisions() -> None:
    with pytest.raises(ValidationError):
        RealLifeChallenge(**_rlc(decisions=[_decision()]))  # only 1
    with pytest.raises(ValidationError):
        RealLifeChallenge(**_rlc(decisions=[_decision()] * 5))  # 5


def test_rlc_decision_correct_option_must_index_options() -> None:
    # correct_option out of range for the options list is a malformed decision.
    with pytest.raises(ValidationError):
        RlcDecision(**_decision(correct_option=9))


def test_rlc_decision_requires_two_options() -> None:
    with pytest.raises(ValidationError):
        RlcDecision(**_decision(options=["only one"], correct_option=0))


def test_rlc_decision_defaults_require_why_and_confidence() -> None:
    # Both the Why prompt and the Confidence rating are mandatory per spec.
    d = RlcDecision(**_decision())
    assert d.why_required is True
    assert d.confidence_required is True


# ─────────────────────────────────────────────────────────────────────
# Error Detection
# ─────────────────────────────────────────────────────────────────────


def _err(**overrides) -> dict:
    base = dict(
        pattern="math_equation",
        concept_ids=["linear-equation-subtraction"],
        blocks=[
            dict(id="b1", content="3x + 5 = 11", is_error=False),
            dict(id="b2", content="3x = 11 - 5", is_error=False),
            dict(id="b3", content="3x = 16", is_error=True),
            dict(id="b4", content="x = 16/3", is_error=False),
        ],
        correct_answer_for_error_block="3x = 6",
        accepted_variants=["3x=6", "3x = 6"],
        common_mistake_source="11 - 5 miscalculated as 16",
        hint="Check the arithmetic. What is 11 minus 5?",
        why_prompt="Why was the original wrong?",
        expected_reasoning_keywords=["11 - 5", "6"],
        correct_feedback="Aniq! Xatoni topdingiz va to'g'riladingiz.",
        wrong_correction_feedback="Hali emas. Hintni ko'rishni xohlaysizmi?",
        reveal_feedback="To'g'ri javob: 3x = 6.",
    )
    base.update(overrides)
    return base


def test_error_detection_valid() -> None:
    err = ErrorDetection(**_err())
    assert sum(1 for b in err.blocks if b.is_error) == 1


def test_error_detection_requires_exactly_one_error_block() -> None:
    # Zero error blocks.
    blocks_zero = [dict(id=f"b{i}", content=str(i), is_error=False) for i in range(3)]
    with pytest.raises(ValidationError):
        ErrorDetection(**_err(blocks=blocks_zero))
    # Two error blocks.
    blocks_two = [
        dict(id="b1", content="a", is_error=True),
        dict(id="b2", content="b", is_error=True),
        dict(id="b3", content="c", is_error=False),
    ]
    with pytest.raises(ValidationError):
        ErrorDetection(**_err(blocks=blocks_two))


def test_error_detection_requires_min_three_blocks() -> None:
    with pytest.raises(ValidationError):
        ErrorDetection(
            **_err(
                blocks=[
                    dict(id="b1", content="a", is_error=True),
                    dict(id="b2", content="b", is_error=False),
                ]
            )
        )


def test_error_detection_why_prompt_mandatory_for_math_and_science() -> None:
    # math_equation + science_diagram REQUIRE a Why prompt (interactivity std).
    for pattern in ("math_equation", "science_diagram"):
        with pytest.raises(ValidationError):
            ErrorDetection(**_err(pattern=pattern, why_prompt=""))


def test_error_detection_why_prompt_optional_for_grammar() -> None:
    # grammar_sentence may omit the Why prompt (mechanical fix).
    err = ErrorDetection(**_err(pattern="grammar_sentence", why_prompt=""))
    assert err.pattern == "grammar_sentence"


def test_error_detection_requires_at_least_one_concept_id() -> None:
    with pytest.raises(ValidationError):
        ErrorDetection(**_err(concept_ids=[]))


def test_error_detection_rejects_unknown_pattern() -> None:
    with pytest.raises(ValidationError):
        ErrorDetection(**_err(pattern="freeform"))


def test_error_block_minimal() -> None:
    b = ErrorBlock(id="b1", content="x = 1")
    assert b.is_error is False  # defaults to not-the-error


# ─────────────────────────────────────────────────────────────────────
# CBP-mode games (Memory Matching / Jigsaw / Sentence Filling / TicTacToe)
# ─────────────────────────────────────────────────────────────────────


def _checkpoint(**overrides) -> dict:
    base = dict(
        intent="identify",
        form="mcq",
        question="Qaysi ikki karta bir xil tushunchaga tegishli?",
        options=["A juftlik", "B juftlik", "C juftlik", "D juftlik"],
        correct_index=0,
        feedback="To'g'ri.",
    )
    base.update(overrides)
    return base


def _cbp_mode(**overrides) -> dict:
    base = dict(
        interaction_mode="memory_match",
        title="Xotira moslashtirish: Hujayra qismlari",
        student_role="laboratoriya yordamchisi",
        case_type="Memory reconstruction case",
        source_concept_ids=["mitochondria-function"],
        case_setup=dict(
            narrative="Siz hujayra qismlarini tartibga solishingiz kerak.",
            student_role="laboratoriya yordamchisi",
            task="Juftliklarni eslab qoling.",
        ),
        checkpoints=[
            _checkpoint(intent="identify"),
            _checkpoint(intent="decide"),
            _checkpoint(intent="justify_or_avoid_mistake"),
        ],
        decision_process_explanation=dict(
            prompt="Fikrlashingizni tushuntiring: tushuncha, ma'no, xato.",
            expected_components=["concept", "method", "mistake"],
            rubric={"full": "all three"},
            sample_acceptable_answer="Mitoxondriya energiya ishlab chiqaradi...",
        ),
        learning_block_1=dict(
            explanation="Ikki karta faqat manba ularni bog'laganda juftlik bo'ladi.",
        ),
        learning_block_2=dict(
            explanation="Bog'lanishning yo'nalishi bor: A qism B ni qo'llab-quvvatlaydi.",
        ),
        final_simulation=dict(
            correct_path="Talaba ma'noni qayta tiklaydi -> Recalled.",
            wrong_path="Talaba faqat joyni eslaydi -> Position Memory Only.",
            why_wrong_fails="Joy xotirasi tez unutiladi; faqat ma'no qayta tiklash qoladi.",
        ),
        feedback_summary=dict(
            understood="Tushuncha ma'nosi.",
            mistake="Joy xotirasi.",
            review="Qayta ko'rib chiqish.",
        ),
        completion_rules=dict(
            pass_condition="Ma'noni qayta tiklaydi.",
            retry_condition="Faqat joyni eslaydi.",
        ),
    )
    base.update(overrides)
    return base


def test_cbp_mode_valid() -> None:
    game = CbpModeGame(**_cbp_mode())
    assert game.interaction_mode == "memory_match"
    assert len(game.checkpoints) == 3
    # Inherits the CBP contract: source concepts, DPE, simulation present.
    assert game.source_concept_ids
    assert game.decision_process_explanation.options is None  # DPE never an MCQ


@pytest.mark.parametrize(
    "mode", ["memory_match", "jigsaw", "sentence_fill", "tictactoe"]
)
def test_cbp_mode_accepts_all_four_interaction_modes(mode: str) -> None:
    game = CbpModeGame(**_cbp_mode(interaction_mode=mode))
    assert game.interaction_mode == mode


def test_cbp_mode_rejects_unknown_interaction_mode() -> None:
    with pytest.raises(ValidationError):
        CbpModeGame(**_cbp_mode(interaction_mode="flashcards"))


def test_cbp_mode_requires_exactly_three_checkpoints() -> None:
    # Inherited from CaseBasedPreview: exactly 3 MCQ checkpoints.
    with pytest.raises(ValidationError):
        CbpModeGame(**_cbp_mode(checkpoints=[_checkpoint(), _checkpoint()]))


def test_cbp_mode_requires_at_least_one_source_concept_id() -> None:
    with pytest.raises(ValidationError):
        CbpModeGame(**_cbp_mode(source_concept_ids=[]))
