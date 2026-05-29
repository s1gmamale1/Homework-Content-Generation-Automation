"""Infra-spec conformance for Practice Arc games.

Each game must (a) conform to its Infra spec's hard-rules and (b) trace to the
SourceMap (no disconnected drills). This module IS the enforcement of the
deliverable ACC — the schemas themselves are permissive. Every validator raises
GameConformanceError on violation; `validate_game` dispatches by game type.

Specs: docs/Infra_prompts/Gamified Practices/*
"""

from __future__ import annotations

from typing import Union

from app.schemas.practice_games.common import CaseBasedInteraction, MCQCheckpoint
from app.schemas.practice_games.error_detection import ErrorDetectionTask
from app.schemas.practice_games.jigsaw_matching import JigsawMatching
from app.schemas.practice_games.memory_matching import MemoryMatching
from app.schemas.practice_games.sentence_filling import SentenceFilling
from app.schemas.practice_games.tictactoe import TicTacToe
from app.schemas.real_life import RealLifeChallenge
from app.schemas.skills import SkillRegistry
from app.services import beta_export, skill_map

_CBP_CHECKPOINT_ORDER = ("identify", "decide", "justify")
_WHY_REQUIRED_PATTERNS = {"math_equation", "science_diagram"}
_TICTACTOE_CELLS = {"3x3": 9, "2x2": 4}


class GameConformanceError(ValueError):
    """Raised when a game violates its Infra spec or fails its concept trace."""


def _trace(concept_ids: list[str], registry: SkillRegistry) -> None:
    """Concept-trace check, surfaced as GameConformanceError so callers catch
    one exception type for any conformance failure."""
    try:
        skill_map.validate_concept_trace(concept_ids, registry)
    except skill_map.SkillMappingError as exc:
        raise GameConformanceError(str(exc)) from exc


def _skills(target_skill_ids: list[str], registry: SkillRegistry) -> None:
    """Mission→skill mapping check (every mission maps to >=1 SourceMap skill),
    surfaced as GameConformanceError."""
    try:
        skill_map.validate_mission_mapping(target_skill_ids, registry)
    except skill_map.SkillMappingError as exc:
        raise GameConformanceError(str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────
# Shared CBP-mode checks
# ─────────────────────────────────────────────────────────────────────

def _check_mcq(cp: MCQCheckpoint) -> None:
    if len(cp.options) < 2:
        raise GameConformanceError(f"{cp.kind} checkpoint needs >=2 options")
    correct = sum(1 for o in cp.options if o.is_correct)
    if correct != 1:
        raise GameConformanceError(
            f"{cp.kind} checkpoint needs exactly 1 correct option, got {correct}"
        )


def _validate_case_based(game: CaseBasedInteraction, registry: SkillRegistry) -> None:
    # No disconnected drills — must trace to >=1 real concept.
    _trace(game.source_concept_ids, registry)
    # Every mission maps to >=1 target skill in the SourceMap.
    _skills(game.target_skill_ids, registry)

    kinds = tuple(c.kind for c in game.checkpoints)
    if kinds != _CBP_CHECKPOINT_ORDER:
        raise GameConformanceError(
            f"checkpoints must be exactly {list(_CBP_CHECKPOINT_ORDER)} in order, got {list(kinds)}"
        )
    for cp in game.checkpoints:  # C3 (justify) is an MCQCheckpoint by type → never open
        _check_mcq(cp)

    dpe = game.decision_process_explanation
    if len(dpe.expected_components) != 3:
        raise GameConformanceError(
            f"decision_process_explanation needs exactly 3 expected_components, "
            f"got {len(dpe.expected_components)}"
        )

    if game.final_simulation.correct.kind != "correct":
        raise GameConformanceError("final_simulation.correct must be the correct path")
    if game.final_simulation.weak.kind != "weak":
        raise GameConformanceError("final_simulation.weak must be the weak path")
    # provenance is a Literal["source","inferred"] — enum-guaranteed; no check needed.


# ─────────────────────────────────────────────────────────────────────
# Per-game validators
# ─────────────────────────────────────────────────────────────────────

def validate_memory_matching(game: MemoryMatching, registry: SkillRegistry) -> None:
    _validate_case_based(game, registry)
    if not (4 <= len(game.card_pairs) <= 8):
        raise GameConformanceError(
            f"memory matching needs 4-8 card pairs, got {len(game.card_pairs)}"
        )


def validate_sentence_filling(game: SentenceFilling, registry: SkillRegistry) -> None:
    _validate_case_based(game, registry)
    if not game.broken_phrase:
        raise GameConformanceError("sentence filling needs a broken_phrase")
    if len(game.replacement_chips) < 2:
        raise GameConformanceError("sentence filling needs >=2 replacement chips")
    correct = sum(1 for c in game.replacement_chips if c.is_correct)
    if correct != 1:
        raise GameConformanceError(
            f"sentence filling needs exactly 1 correct replacement chip, got {correct}"
        )


def validate_tictactoe(game: TicTacToe, registry: SkillRegistry) -> None:
    _validate_case_based(game, registry)
    expected = _TICTACTOE_CELLS[game.grid_size]
    if len(game.cells) != expected:
        raise GameConformanceError(
            f"{game.grid_size} board needs {expected} cells, got {len(game.cells)}"
        )
    correct = sum(1 for c in game.cells if c.role == "correct")
    if correct != 1:
        raise GameConformanceError(
            f"tic-tac-toe needs exactly 1 correct cell, got {correct}"
        )


def validate_jigsaw_matching(game: JigsawMatching, registry: SkillRegistry) -> None:
    _validate_case_based(game, registry)
    if not (3 <= len(game.pieces) <= 6):
        raise GameConformanceError(
            f"jigsaw needs 3-6 pieces, got {len(game.pieces)}"
        )
    if len(game.allowed_assembly_types) > 3:
        raise GameConformanceError("jigsaw allows max 3 assembly types per round")
    if len(game.correct_pair) != 2:
        raise GameConformanceError("jigsaw correct_pair must name exactly 2 piece ids")
    piece_ids = {p.piece_id for p in game.pieces}
    dangling = [pid for pid in game.correct_pair if pid not in piece_ids]
    if dangling:
        raise GameConformanceError(f"jigsaw correct_pair references unknown pieces: {dangling}")
    if game.allowed_assembly_types and game.correct_assembly_type not in game.allowed_assembly_types:
        raise GameConformanceError(
            "jigsaw correct_assembly_type must be among allowed_assembly_types"
        )


def validate_error_detection(game: ErrorDetectionTask, registry: SkillRegistry) -> None:
    _trace(game.source_concept_ids, registry)
    _skills(game.target_skill_ids, registry)
    error_blocks = [b for b in game.blocks if b.is_error]
    if len(error_blocks) != 1:
        raise GameConformanceError(
            f"error detection needs exactly 1 error block, got {len(error_blocks)}"
        )
    if len(game.blocks) < 3:
        raise GameConformanceError(
            f"error detection needs >=3 blocks, got {len(game.blocks)}"
        )
    if not game.correct_answer_for_error_block:
        raise GameConformanceError("error detection needs a correction for the error block")
    if game.pattern in _WHY_REQUIRED_PATTERNS and not game.why_prompt:
        raise GameConformanceError(
            f"error detection pattern {game.pattern!r} requires a why_prompt"
        )


def validate_real_life(game: RealLifeChallenge, registry: SkillRegistry) -> None:
    _trace(game.source_concept_ids, registry)
    _skills(game.target_skill_ids, registry)
    # Reuse the platform-contract check: must still map to the validated 5-step.
    try:
        beta_export.to_platform_case(game)
    except Exception as exc:  # pydantic ValidationError or adapter error
        raise GameConformanceError(f"RLC fails platform 5-step contract: {exc}") from exc


_VALIDATORS = {
    "memory_matching": validate_memory_matching,
    "sentence_filling": validate_sentence_filling,
    "tictactoe": validate_tictactoe,
    "jigsaw_matching": validate_jigsaw_matching,
    "error_detection": validate_error_detection,
    "real_life": validate_real_life,
}

GameModel = Union[
    MemoryMatching, SentenceFilling, TicTacToe, JigsawMatching,
    ErrorDetectionTask, RealLifeChallenge,
]


def validate_game(game_type: str, game: GameModel, registry: SkillRegistry) -> None:
    """Dispatch to the right Infra-conformance validator. Raises
    GameConformanceError on violation, KeyError on an unknown game_type."""
    validator = _VALIDATORS.get(game_type)
    if validator is None:
        raise GameConformanceError(f"unknown game type: {game_type!r}")
    validator(game, registry)
