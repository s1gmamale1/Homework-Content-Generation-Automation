"""Practice Arc game schemas (Flow v2).

Six canonical game schemas, each conforming to its Infra spec under
docs/Infra_prompts/Gamified Practices/. Four of them (Memory Matching, Sentence
Filling, Tic-Tac-Toe, Jigsaw Matching) are Case-Based Preview interaction modes
sharing the skeleton in `common.py`; Error Detection and Real-Life Challenge are
distinct shapes (RLC lives in app/schemas/real_life.py and is reused).

Schemas are PERMISSIVE — Infra hard-rules and concept-tracing are enforced by
app/services/game_conformance.py, so SDK parsing stays robust and callers
control when the contract fires.
"""

from app.schemas.practice_games.common import (
    CaseBasedInteraction,
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
from app.schemas.practice_games.memory_matching import MemoryMatching, MemoryCardPair
from app.schemas.practice_games.sentence_filling import ReplacementChip, SentenceFilling
from app.schemas.practice_games.tictactoe import TicTacToe, TicTacToeCell

__all__ = [
    # shared
    "MCQOption",
    "MCQCheckpoint",
    "DecisionProcessExplanation",
    "ConsequencePath",
    "FinalSimulation",
    "FeedbackSummary",
    "CommonMistake",
    "CaseMetadata",
    "CaseBasedInteraction",
    # games
    "MemoryMatching",
    "MemoryCardPair",
    "SentenceFilling",
    "ReplacementChip",
    "TicTacToe",
    "TicTacToeCell",
    "JigsawMatching",
    "JigsawPiece",
    "ErrorDetectionTask",
    "ErrorDetectionBlock",
]
