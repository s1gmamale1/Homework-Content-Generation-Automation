"""Tic-Tac-Toe Decision Grid — CBP interaction mode.

A 3x3 (or 2x2) board of candidate actions. The grid must be solvable through
the lesson concept, never general intuition. Spec:
docs/Infra_prompts/Gamified Practices/TicTacToe/TicTacToe.md
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.practice_games.common import CaseBasedInteraction

CellRole = Literal[
    "correct", "plausible_incomplete", "fast_unsafe", "surface_clue",
    "irrelevant", "overly_broad", "wrong_order", "common_mistake",
]


class TicTacToeCell(BaseModel):
    content: str  # the candidate action
    role: CellRole  # server-only — why this cell is right/wrong


class StateMeter(BaseModel):
    name: str  # e.g. Accuracy, Evidence, Risk, Safety
    correct_value: int  # value on the correct path
    weak_value: int  # value on the weak path


class TicTacToe(CaseBasedInteraction):
    grid_size: Literal["3x3", "2x2"] = "3x3"
    cells: list[TicTacToeCell] = []  # 9 (3x3) or 4 (2x2); exactly 1 role="correct"
    decision_condition: str  # the clue that determines the correct action
    state_meters: list[StateMeter] = []
