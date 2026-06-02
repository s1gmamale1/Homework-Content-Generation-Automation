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
    GameChoice,
    MemoryMatchPayload,
    MemoryMatchPair,
    RealLifeChallenge,
    RlcDecision,
    TicTacToePayload,
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


def _ttt_cells():
    return [GameChoice(label=f"c{i}", is_correct=(i == 0)) for i in range(9)]



def test_cbp_mode_game_compact_valid():
    g = CbpModeGame(
        title="Match the terms",
        source_concept_ids=["c1"],
        interaction_mode="memory_match",
        instruction="Match each term to its meaning.",
        interaction_payload=MemoryMatchPayload(
            pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)]),
        why_prompt="Explain why these pair up — the concept, the link, the trap.",
    )
    assert g.interaction_mode == "memory_match"
    assert g.why_prompt
    assert not hasattr(g, "checkpoints") and not hasattr(g, "case_setup")
    assert not hasattr(g, "decision_process_explanation")


def test_cbp_mode_game_requires_concept_ids_instruction_why():
    import pytest
    base = dict(title="T", interaction_mode="tictactoe",
                interaction_payload=TicTacToePayload(cells=_ttt_cells()))
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=[], instruction="i", why_prompt="w")
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=["c1"], instruction="", why_prompt="w")
    with pytest.raises(Exception):
        CbpModeGame(**base, source_concept_ids=["c1"], instruction="i", why_prompt="")


def test_cbp_mode_payload_must_match_mode():
    import pytest
    with pytest.raises(Exception):
        CbpModeGame(
            title="T", source_concept_ids=["c1"], interaction_mode="tictactoe",
            instruction="i", why_prompt="w",
            interaction_payload=MemoryMatchPayload(
                pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)]),
        )


def test_memory_match_payload_pair_count() -> None:
    from app.schemas.practice_games import MemoryMatchPayload
    with pytest.raises(ValidationError):
        MemoryMatchPayload(pairs=[dict(left="a", right="b")])


def test_sentence_fill_requires_exactly_one_correct_chip() -> None:
    from app.schemas.practice_games import SentenceFillPayload
    with pytest.raises(ValidationError):
        SentenceFillPayload(sentence="x _____", chips=[
            dict(label="a", is_correct=True), dict(label="b", is_correct=True),
            dict(label="c", is_correct=False)])


def test_tictactoe_requires_nine_cells_and_a_correct() -> None:
    from app.schemas.practice_games import TicTacToePayload
    with pytest.raises(ValidationError):
        TicTacToePayload(cells=[dict(label=f"c{i}", is_correct=False) for i in range(9)])
    with pytest.raises(ValidationError):
        TicTacToePayload(cells=[dict(label=f"c{i}", is_correct=(i == 0)) for i in range(4)])


def test_phase_mode_subclasses_pin_interaction_mode():
    import pytest
    from app.schemas.practice_games import (
        TicTacToeGame, MemoryMatchGame, TicTacToePayload, MemoryMatchPayload,
        MemoryMatchPair, GameChoice,
    )
    ttt_payload = TicTacToePayload(
        cells=[GameChoice(label=f"c{i}", is_correct=(i == 0)) for i in range(9)])
    g = TicTacToeGame(title="T", source_concept_ids=["c1"], interaction_mode="tictactoe",
                      instruction="i", interaction_payload=ttt_payload, why_prompt="w")
    assert g.interaction_mode == "tictactoe"
    with pytest.raises(Exception):  # wrong mode on a pinned subclass
        TicTacToeGame(title="T", source_concept_ids=["c1"], interaction_mode="memory_match",
                      instruction="i", interaction_payload=ttt_payload, why_prompt="w")
    mm_payload = MemoryMatchPayload(
        pairs=[MemoryMatchPair(left=f"L{i}", right=f"R{i}") for i in range(4)])
    with pytest.raises(Exception):  # memory_match payload on tictactoe subclass
        TicTacToeGame(title="T", source_concept_ids=["c1"], interaction_mode="tictactoe",
                      instruction="i", interaction_payload=mm_payload, why_prompt="w")


def test_registry_maps_each_game_phase_to_its_mode_subclass():
    from app.services.agent import STRUCTURED_PHASE_SCHEMAS
    from app.schemas.practice_games import (
        MemoryMatchGame, TicTacToeGame, JigsawGame, SentenceFillGame, CbpModeGame,
    )
    assert STRUCTURED_PHASE_SCHEMAS["practice-memory-match"] is MemoryMatchGame
    assert STRUCTURED_PHASE_SCHEMAS["practice-tictactoe"] is TicTacToeGame
    assert STRUCTURED_PHASE_SCHEMAS["practice-jigsaw"] is JigsawGame
    assert STRUCTURED_PHASE_SCHEMAS["practice-sentence"] is SentenceFillGame
    for cls in (MemoryMatchGame, TicTacToeGame, JigsawGame, SentenceFillGame):
        assert issubclass(cls, CbpModeGame)


def test_memory_match_pair_distinct_sides_and_pairs():
    import pytest
    from app.schemas.practice_games import MemoryMatchPair, MemoryMatchPayload
    with pytest.raises(Exception):
        MemoryMatchPair(left="x", right="x")
    dup = [MemoryMatchPair(left="a", right="b")] * 4
    with pytest.raises(Exception):
        MemoryMatchPayload(pairs=dup)


def test_tictactoe_not_all_correct():
    import pytest
    from app.schemas.practice_games import TicTacToePayload, GameChoice
    with pytest.raises(Exception):
        TicTacToePayload(cells=[GameChoice(label=f"c{i}", is_correct=True) for i in range(9)])


def test_jigsaw_requires_valid_solution():
    import pytest
    from app.schemas.practice_games import JigsawPayload, JigsawPiece
    pieces = [JigsawPiece(id=f"p{i}", content=f"c{i}") for i in range(3)]
    with pytest.raises(Exception):
        JigsawPayload(pieces=pieces, allowed_assembly_types=["t"])
    with pytest.raises(Exception):
        JigsawPayload(pieces=pieces, allowed_assembly_types=["t"], solution=[["p0", "pX"]])
    ok = JigsawPayload(pieces=pieces, allowed_assembly_types=["t"], solution=[["p0", "p1"]])
    assert ok.solution == [["p0", "p1"]]


def test_error_detection_correction_differs_from_broken_block():
    import pytest
    from app.schemas.practice_games import ErrorDetection, ErrorBlock, _WHY_REQUIRED_PATTERNS
    pattern = next(iter(_WHY_REQUIRED_PATTERNS))  # a math/science pattern so why_prompt is required
    blocks = [
        ErrorBlock(id="b1", content="2x = 4", is_error=True),
        ErrorBlock(id="b2", content="x = 2", is_error=False),
        ErrorBlock(id="b3", content="check", is_error=False),
    ]
    with pytest.raises(Exception):
        ErrorDetection(pattern=pattern, concept_ids=["c1"], blocks=blocks,
                       correct_answer_for_error_block="2x = 4", hint="h",
                       correct_feedback="c", wrong_correction_feedback="w",
                       reveal_feedback="r", why_prompt="why because")


