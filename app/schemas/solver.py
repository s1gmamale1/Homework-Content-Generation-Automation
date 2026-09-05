# app/schemas/solver.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Discrepancy(BaseModel):
    """One place the independently-solved answer disagrees with the generated key.
    confidence gates action: ONLY `high` triggers a regen (conservative — see the
    validate_toc false-positive lesson). low/medium are advisory."""
    item: str = Field(description="which item/question/block the disagreement is about")
    generated_key: str = Field(description="what the key, option marking, feedback or rubric claims is correct")
    solver_answer: str = Field(description="independent answer, including all defensible options or no answer under the visible wording")
    explanation: str = Field(description="quoted question/option/feedback evidence proving a wrong key, defensible second answer, missing answer or misaligned rubric")
    confidence: Literal["low", "medium", "high"]


class SolveVerdict(BaseModel):
    agrees: bool = Field(description="True iff independently checking every option, key and feedback reveals no discrepancy; accept open tasks with sufficient visible evidence and aligned rubrics")
    discrepancies: list[Discrepancy] = Field(default_factory=list)
