"""Sentence Filling (English Fill-in-the-Blanks) — CBP interaction mode.

NOT a cloze. The student finds the broken phrase, picks a source-aligned repair,
and justifies it without changing the textbook meaning. Spec:
docs/Infra_prompts/Gamified Practices/Sentence Filling/SentenceFilling.md
"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.practice_games.common import CaseBasedInteraction

SentenceType = Literal[
    "definition", "explanation", "cause_effect", "grammar_register",
    "evidence_claim", "process_statement",
]


class ReplacementChip(BaseModel):
    label: str
    is_correct: bool = False  # server-only


class MeaningCheck(BaseModel):
    meaning_accurate: bool
    wording_register_clear: bool
    source_concept_preserved: bool


class SentenceFilling(CaseBasedInteraction):
    sentence_type: SentenceType
    broken_sentence: str
    chunks: list[str] = []  # selectable phrase chunks of the broken sentence
    broken_phrase: str  # server-only — the chunk that is wrong
    replacement_chips: list[ReplacementChip] = []  # >=2, exactly 1 correct
    correct_meaning: str  # the source meaning that must be preserved
    meaning_check: Optional[MeaningCheck] = None  # final-sim panel (correct path)
