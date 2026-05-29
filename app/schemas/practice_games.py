"""Practice Arc game content schemas (Flow v2, PR-3).

The Practice Arc replaces the single generic ``game-breaks`` phase with typed,
source-traced conceptual games. Six games are authored from the specs in
``docs/Infra_prompts/Gamified Practices``; their *content contracts* collapse to
three schemas:

- ``RealLifeChallenge`` — first-person expert decision game. Standalone
  mechanic (absorbs the legacy ``real-life`` phase). A role + 2-4 decision
  points, each a multiple-choice action with a mandatory Why justification and
  Confidence rating, plus a final reasoning summary.
- ``ErrorDetection`` — spot-the-broken-piece then type-the-correction game.
  Exactly one error per task; the Why prompt is mandatory for math/science
  patterns (optional for mechanical grammar fixes).
- ``CbpModeGame`` — the four games whose own spec files title them
  "Case-Based Preview Interaction Mode" (Memory Matching, Jigsaw Matching,
  Sentence Filling, TicTacToe). Their contract is *identical* to
  ``CaseBasedPreview`` (3 MCQ checkpoints -> Decision Process Explanation ->
  correct/wrong simulation -> feedback summary), so this is that schema plus an
  ``interaction_mode`` discriminator — not a fourth copy.

Every game traces to the lesson's source concepts (``SourceMap`` IDs from PR-1):
``concept_ids`` here, ``source_concept_ids`` on the CBP-derived game (inherited).
This is the "no disconnected drills" rule baked into the schema boundary.

Runtime concerns (XP, adaptation, telemetry, mistake-repair scoring) belong to
the downstream homework-builder platform, not this content factory.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.flow_v2 import CaseBasedPreview

# ─────────────────────────────────────────────────────────────────────
# Real-Life Challenge
# ─────────────────────────────────────────────────────────────────────


class RlcDecision(BaseModel):
    """One decision point inside a Real-Life Challenge scenario. The student
    picks an action (MC), then justifies it (Why) and rates confidence."""

    question: str = Field(min_length=1)
    # 3-4 options per spec; >=2 is the hard floor for a real choice.
    options: list[str] = Field(min_length=2)
    correct_option: int  # index into ``options``
    why_required: bool = True          # mandatory per interactivity standard
    confidence_required: bool = True   # Sure / Maybe / Guess — mandatory
    # Keywords a strong justification should reference (graded downstream).
    expected_reasoning: list[str] = Field(default_factory=list)
    correct_feedback: str
    partial_feedback: str
    wrong_feedback: str

    @model_validator(mode="after")
    def _correct_option_in_range(self) -> "RlcDecision":
        if not (0 <= self.correct_option < len(self.options)):
            raise ValueError(
                f"correct_option {self.correct_option} out of range for "
                f"{len(self.options)} options"
            )
        return self


class RealLifeChallenge(BaseModel):
    """A single first-person expert scenario. The student IS the expert."""

    scenario_id: Optional[str] = None
    # Traces to the lesson's source concepts — the "match target skill" rule.
    concept_ids: list[str] = Field(min_length=1)
    role: str = Field(min_length=1)
    task: str = Field(min_length=1)
    grade_band: Optional[str] = None
    pisa: Optional[str] = None
    context: str = Field(min_length=1)
    prediction_prompt: str = Field(min_length=1)
    # 2-4 decisions for our grade bands (G4+). Each carries Why + Confidence.
    decisions: list[RlcDecision] = Field(min_length=2, max_length=4)
    # Higher-grade variants (G7+ red herring / G10+ incomplete info). Optional.
    red_herring: Optional[str] = None
    final_summary: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────
# Error Detection
# ─────────────────────────────────────────────────────────────────────

ErrorPattern = Literal["math_equation", "grammar_sentence", "science_diagram"]
# Patterns where the Why prompt is mandatory (math/science), per the
# interactivity standard. Grammar/text mechanical fixes may omit it.
_WHY_REQUIRED_PATTERNS = {"math_equation", "science_diagram"}


class ErrorBlock(BaseModel):
    """One block of the work shown to the student (an equation step, a sentence
    clause, or a diagram label). Exactly one block in a task is the error."""

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    is_error: bool = False


class ErrorDetection(BaseModel):
    """One spot-the-error + produce-the-correction task. The student finds the
    broken block, then TYPES the fix (no auto-reveal)."""

    task_id: Optional[str] = None
    pattern: ErrorPattern
    # Traces to the lesson's source concepts.
    concept_ids: list[str] = Field(min_length=1)
    grade_band: Optional[str] = None
    difficulty: Optional[str] = None
    # 3-8 blocks by grade band; >=3 is the floor.
    blocks: list[ErrorBlock] = Field(min_length=3)
    correct_answer_for_error_block: str = Field(min_length=1)
    accepted_variants: list[str] = Field(default_factory=list)
    common_mistake_source: str = ""
    hint: str = Field(min_length=1)  # one probing hint — never the answer
    why_prompt: str = ""             # required for math/science (validated below)
    expected_reasoning_keywords: list[str] = Field(default_factory=list)
    correct_feedback: str
    wrong_correction_feedback: str
    reveal_feedback: str

    @model_validator(mode="after")
    def _exactly_one_error(self) -> "ErrorDetection":
        n_errors = sum(1 for b in self.blocks if b.is_error)
        if n_errors != 1:
            raise ValueError(
                f"Error Detection task must have exactly one error block, got {n_errors}"
            )
        return self

    @model_validator(mode="after")
    def _why_prompt_required_for_math_science(self) -> "ErrorDetection":
        if self.pattern in _WHY_REQUIRED_PATTERNS and not self.why_prompt.strip():
            raise ValueError(
                f"why_prompt is mandatory for pattern {self.pattern!r} "
                "(math/science) per the interactivity standard"
            )
        return self


# ─────────────────────────────────────────────────────────────────────
# CBP-mode games — Memory Matching / Jigsaw / Sentence Filling / TicTacToe
# ─────────────────────────────────────────────────────────────────────

PracticeInteractionMode = Literal[
    "memory_match", "jigsaw", "sentence_fill", "tictactoe"
]


class CbpModeGame(CaseBasedPreview):
    """A Case-Based Preview interaction-mode game. These four games share the
    exact CBP content contract (3 MCQ checkpoints -> DPE -> correct/wrong
    simulation -> feedback) — their own spec files literally call them
    "Case-Based Preview Interaction Mode". The only structural difference is
    the interaction skin, captured by ``interaction_mode``. The flavour
    (cards vs grid vs sentence) lives in the per-mode prompt, not the schema.
    """

    interaction_mode: PracticeInteractionMode
