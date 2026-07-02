# app/schemas/solver.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Discrepancy(BaseModel):
    """One place the independently-solved answer disagrees with the generated key.
    confidence gates action: ONLY `high` triggers a regen (conservative — see the
    validate_toc false-positive lesson). low/medium are advisory."""
    item: str = Field(description="which item/question/block the disagreement is about")
    generated_key: str = Field(description="what the phase's key claims is correct")
    solver_answer: str = Field(description="what independent solving gives")
    explanation: str = Field(description="why the key is wrong, briefly")
    confidence: Literal["low", "medium", "high"]


class SolveVerdict(BaseModel):
    agrees: bool = Field(description="True iff every item's key is correct")
    discrepancies: list[Discrepancy] = Field(default_factory=list)
