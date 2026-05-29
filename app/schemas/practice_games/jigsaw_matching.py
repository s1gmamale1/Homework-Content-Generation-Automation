"""Jigsaw Matching — CBP interaction mode.

Relationship-reasoning puzzle: which two source nodes fit, what assembly they
form, and why the wrong combination cannot fit. Spec:
docs/Infra_prompts/Gamified Practices/Jigsaw Matching/JigsawMatching.md
"""

from typing import Literal

from pydantic import BaseModel

from app.schemas.practice_games.common import CaseBasedInteraction

AssemblyType = Literal[
    "concept_definition", "formula_variable", "cause_effect",
    "evidence_claim", "step_result", "term_example",
]


class JigsawPiece(BaseModel):
    piece_id: str
    label: str  # source-supported node


class JigsawMatching(CaseBasedInteraction):
    pieces: list[JigsawPiece] = []  # 3-6 source-supported nodes
    allowed_assembly_types: list[AssemblyType] = []  # max 3 per round
    correct_pair: list[str] = []  # server-only — the two piece_ids that fit
    correct_assembly_type: AssemblyType  # server-only — the relationship label
