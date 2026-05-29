"""Infra-conformance tests for the 6 Practice Arc games.

Each game: a valid instance passes its validator; key Infra violations raise.
Pure pydantic — no heavy deps.
"""

import pytest

from app.schemas.practice_games.common import (
    CaseMetadata,
    CommonMistake,
    ConsequencePath,
    DecisionProcessExplanation,
    FeedbackSummary,
    FinalSimulation,
    MCQCheckpoint,
    MCQOption,
)
from app.schemas.practice_games.error_detection import ErrorDetectionBlock, ErrorDetectionTask
from app.schemas.practice_games.jigsaw_matching import JigsawMatching, JigsawPiece
from app.schemas.practice_games.memory_matching import MemoryCardPair, MemoryMatching
from app.schemas.practice_games.sentence_filling import ReplacementChip, SentenceFilling
from app.schemas.practice_games.tictactoe import StateMeter, TicTacToe, TicTacToeCell
from app.schemas.real_life import (
    RealLifeChallenge,
    RLCConceptChip,
    RLCConceptSelectStep,
    RLCDecisionOption,
    RLCDecisionStep,
    RLCReasoningStep,
)
from app.schemas.skills import SkillRegistry, SourceConcept, TargetSkill
from app.services import game_conformance
from app.services.game_conformance import GameConformanceError


def registry() -> SkillRegistry:
    return SkillRegistry(
        concepts=[
            SourceConcept(concept_id="c1", label="C1", statement="fact one"),
            SourceConcept(concept_id="c2", label="C2", statement="fact two"),
        ],
        skills=[
            TargetSkill(skill_id="s1", statement="The student can do c1.", concept_ids=["c1"]),
        ],
    )


# ── shared CBP builders ────────────────────────────────────────────────

def _mcq(kind: str) -> MCQCheckpoint:
    return MCQCheckpoint(
        kind=kind,
        question=f"{kind}?",
        options=[
            MCQOption(label="right", is_correct=True),
            MCQOption(label="wrong", is_correct=False),
        ],
    )


def _cbp_kwargs() -> dict:
    return dict(
        metadata=CaseMetadata(
            subject="english", required_skill="apply c1",
            case_type="memory case", student_role="checker",
        ),
        source_concept_ids=["c1"],
        common_mistake=CommonMistake(text="confuses c1 and c2", provenance="inferred"),
        case_setup="You are a checker. Sort these.",
        checkpoints=[_mcq("identify"), _mcq("decide"), _mcq("justify")],
        learning_blocks=["lb1", "lb2"],
        decision_process_explanation=DecisionProcessExplanation(
            prompt="Walk through your reasoning.",
            expected_components=["concept", "meaning", "mistake"],
        ),
        final_simulation=FinalSimulation(
            correct=ConsequencePath(kind="correct", description="recalled"),
            weak=ConsequencePath(kind="weak", description="position only"),
        ),
        feedback_summary=FeedbackSummary(completion_status="passed"),
    )


def memory_matching() -> MemoryMatching:
    return MemoryMatching(
        **_cbp_kwargs(),
        card_pairs=[
            MemoryCardPair(left=f"L{i}", right=f"R{i}", relationship="term_meaning")
            for i in range(4)
        ],
    )


def sentence_filling() -> SentenceFilling:
    return SentenceFilling(
        **_cbp_kwargs(),
        sentence_type="definition",
        broken_sentence="A noun is an action word.",
        chunks=["A noun", "is", "an action word"],
        broken_phrase="an action word",
        replacement_chips=[
            ReplacementChip(label="a naming word", is_correct=True),
            ReplacementChip(label="a describing word", is_correct=False),
        ],
        correct_meaning="A noun names a person, place, or thing.",
    )


def tictactoe() -> TicTacToe:
    cells = [TicTacToeCell(content=f"a{i}", role="irrelevant") for i in range(9)]
    cells[0] = TicTacToeCell(content="correct action", role="correct")
    return TicTacToe(
        **_cbp_kwargs(), grid_size="3x3", cells=cells,
        decision_condition="the clue", state_meters=[StateMeter(name="Accuracy", correct_value=85, weak_value=40)],
    )


def jigsaw() -> JigsawMatching:
    return JigsawMatching(
        **_cbp_kwargs(),
        pieces=[JigsawPiece(piece_id=f"p{i}", label=f"node {i}") for i in range(3)],
        allowed_assembly_types=["cause_effect", "term_example"],
        correct_pair=["p0", "p1"],
        correct_assembly_type="cause_effect",
    )


def error_detection() -> ErrorDetectionTask:
    return ErrorDetectionTask(
        pattern="math_equation",
        source_concept_ids=["c1"],
        blocks=[
            ErrorDetectionBlock(id="b1", content="3x+5=11", is_error=False),
            ErrorDetectionBlock(id="b2", content="3x=11-5", is_error=False),
            ErrorDetectionBlock(id="b3", content="3x=16", is_error=True),
        ],
        correct_answer_for_error_block="3x=6",
        accepted_variants=["3x = 6"],
        common_mistake_source="11-5 read as 16",
        hint="What is 11-5?",
        why_prompt="Why was it wrong?",
        expected_reasoning_keywords=["arithmetic"],
    )


def real_life() -> RealLifeChallenge:
    def step(p):
        return RLCDecisionStep(
            prompt=p,
            options=[RLCDecisionOption(label="ok", is_correct=True), RLCDecisionOption(label="no")],
        )
    return RealLifeChallenge(
        role="reporter", task="report", context="ctx",
        source_concept_ids=["c1"], prediction_prompt="predict?",
        decision=step("d"), info_request=step("i"), final_decision=step("f"),
        concept_select=RLCConceptSelectStep(
            prompt="which concept?",
            concept_chips=[
                RLCConceptChip(label="c1", is_correct=True),
                RLCConceptChip(label="c2"),
                RLCConceptChip(label="c3"),
            ],
        ),
        reasoning=RLCReasoningStep(prompt="why?", min_chars=60),
    )


# ── valid path: every game passes ──────────────────────────────────────

class TestValidGames:
    @pytest.mark.parametrize("game_type,factory", [
        ("memory_matching", memory_matching),
        ("sentence_filling", sentence_filling),
        ("tictactoe", tictactoe),
        ("jigsaw_matching", jigsaw),
        ("error_detection", error_detection),
        ("real_life", real_life),
    ])
    def test_valid_game_passes(self, game_type, factory):
        game_conformance.validate_game(game_type, factory(), registry())

    def test_unknown_game_type_raises(self):
        with pytest.raises(GameConformanceError, match="unknown game type"):
            game_conformance.validate_game("nope", memory_matching(), registry())


# ── concept trace (no disconnected drills) ──────────────────────────────

class TestConceptTrace:
    def test_empty_trace_rejected(self):
        g = memory_matching()
        g.source_concept_ids = []
        with pytest.raises(GameConformanceError):
            game_conformance.validate_game("memory_matching", g, registry())

    def test_dangling_concept_rejected(self):
        g = error_detection()
        g.source_concept_ids = ["ghost"]
        with pytest.raises(GameConformanceError):
            game_conformance.validate_game("error_detection", g, registry())


# ── CBP hard-rule violations ────────────────────────────────────────────

class TestCbpRules:
    def test_wrong_checkpoint_count_rejected(self):
        g = memory_matching()
        g.checkpoints = g.checkpoints[:2]
        with pytest.raises(GameConformanceError, match="checkpoints must be"):
            game_conformance.validate_game("memory_matching", g, registry())

    def test_checkpoint_order_enforced(self):
        g = tictactoe()
        g.checkpoints = [g.checkpoints[1], g.checkpoints[0], g.checkpoints[2]]
        with pytest.raises(GameConformanceError, match="in order"):
            game_conformance.validate_game("tictactoe", g, registry())

    def test_checkpoint_without_correct_option_rejected(self):
        g = jigsaw()
        for o in g.checkpoints[2].options:
            o.is_correct = False
        with pytest.raises(GameConformanceError, match="exactly 1 correct"):
            game_conformance.validate_game("jigsaw_matching", g, registry())

    def test_dpe_needs_three_components(self):
        g = sentence_filling()
        g.decision_process_explanation.expected_components = ["concept", "mistake"]
        with pytest.raises(GameConformanceError, match="3 expected_components"):
            game_conformance.validate_game("sentence_filling", g, registry())


# ── per-game specifics ──────────────────────────────────────────────────

class TestPerGame:
    def test_memory_matching_needs_4_to_8_pairs(self):
        g = memory_matching()
        g.card_pairs = g.card_pairs[:2]
        with pytest.raises(GameConformanceError, match="4-8 card pairs"):
            game_conformance.validate_game("memory_matching", g, registry())

    def test_tictactoe_cell_count_matches_grid(self):
        g = tictactoe()
        g.cells = g.cells[:8]
        with pytest.raises(GameConformanceError, match="needs 9 cells"):
            game_conformance.validate_game("tictactoe", g, registry())

    def test_sentence_filling_one_correct_chip(self):
        g = sentence_filling()
        g.replacement_chips[1].is_correct = True
        with pytest.raises(GameConformanceError, match="1 correct replacement"):
            game_conformance.validate_game("sentence_filling", g, registry())

    def test_jigsaw_correct_type_must_be_allowed(self):
        g = jigsaw()
        g.correct_assembly_type = "step_result"  # not in allowed list
        with pytest.raises(GameConformanceError, match="allowed_assembly_types"):
            game_conformance.validate_game("jigsaw_matching", g, registry())

    def test_error_detection_exactly_one_error(self):
        g = error_detection()
        g.blocks[0].is_error = True  # now two errors
        with pytest.raises(GameConformanceError, match="exactly 1 error block"):
            game_conformance.validate_game("error_detection", g, registry())

    def test_error_detection_math_requires_why(self):
        g = error_detection()
        g.why_prompt = None
        with pytest.raises(GameConformanceError, match="requires a why_prompt"):
            game_conformance.validate_game("error_detection", g, registry())

    def test_error_detection_grammar_why_optional(self):
        g = error_detection()
        g.pattern = "grammar_sentence"
        g.why_prompt = None
        game_conformance.validate_game("error_detection", g, registry())  # no raise

    def test_real_life_broken_5step_rejected(self):
        g = real_life()
        for o in g.decision.options:
            o.is_correct = False  # breaks the platform contract (no correct option)
        with pytest.raises(GameConformanceError, match="platform 5-step"):
            game_conformance.validate_game("real_life", g, registry())
