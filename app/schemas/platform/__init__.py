"""Pinned mirrors of the Homeworks platform runtime contracts.

These are NOT generator-authored schemas. They mirror the exact shapes the
beta `Homeworks` runtime validates against, so the generator can prove its
beta export is platform-safe before emitting it. Keep them in sync with the
platform repo — swapping in the upstream schema later should be a one-file
change here.
"""

from app.schemas.platform.real_life_challenge import (
    ConceptChip,
    DecisionOption,
    RealLifeChallengeCase,
    RLCStep,
)

__all__ = [
    "RealLifeChallengeCase",
    "RLCStep",
    "DecisionOption",
    "ConceptChip",
]
