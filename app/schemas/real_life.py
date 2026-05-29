"""Canonical Real-Life Challenge schema (generator source-of-truth).

Richer than the platform `RealLifeChallengeCase` mirror: it carries the Infra
RLC pedagogy (role / task / context / prediction / confidence / expert feedback)
on top of the five logical steps the platform runtime validates. The five
step objects below map 1:1 onto the platform contract
(decision → info_request → final_decision → concept_select → reasoning), so the
beta adapter in `app/services/beta_export.py` can down-map deterministically.

Source pedagogy: docs/Infra_prompts/Gamified Practices/Real Life Challenge/
Real_Life_Challenge_Specification.md
Platform contract: app/schemas/platform/real_life_challenge.py

`is_correct`, `consequence`, and `acceptable_keywords` are server-only — the
beta adapter strips them before the payload reaches the student browser.
"""

from typing import Literal, Optional

from pydantic import BaseModel

RLCVariant = Literal["expert_case_5_step", "reverse_test_same_story_new_numbers"]


class RLCDecisionOption(BaseModel):
    label: str
    is_correct: bool = False  # server-only
    consequence: Optional[str] = None  # server-only — what happens if chosen


class RLCConceptChip(BaseModel):
    label: str
    is_correct: bool = False  # server-only — the lesson concept that applies


class RLCDecisionStep(BaseModel):
    """One of decision / info_request / final_decision. >=2 options, 1 correct."""

    prompt: str
    options: list[RLCDecisionOption]
    expected_reasoning: list[str] = []  # concept tags a strong "why" would cite


class RLCConceptSelectStep(BaseModel):
    """concept_select — >=3 chips, exactly 1 correct."""

    prompt: str
    concept_chips: list[RLCConceptChip]


class RLCReasoningStep(BaseModel):
    """reasoning — free-text justification, min_chars in [20, 1000]."""

    prompt: str
    min_chars: int = 60
    acceptable_keywords: list[str] = []  # server-only — rubric anchors
    sample_acceptable_answer: Optional[str] = None


class RealLifeChallenge(BaseModel):
    # ── scenario framing (Infra pedagogy) ──────────────────────────────
    scenario_id: Optional[str] = None
    variant: RLCVariant = "expert_case_5_step"
    role: str
    task: str
    context: str
    grade_band: Optional[str] = None
    pisa: Optional[str] = None
    source_concept_ids: list[str] = []
    # Maps this mission to >=1 target skill in the SourceMap (SkillMapped
    # semantics; enforced by game_conformance.validate_real_life).
    target_skill_ids: list[str] = []
    prediction_prompt: str

    # ── five logical steps (map 1:1 onto the platform contract) ────────
    decision: RLCDecisionStep
    info_request: RLCDecisionStep
    final_decision: RLCDecisionStep
    concept_select: RLCConceptSelectStep
    reasoning: RLCReasoningStep

    # ── post-scenario pedagogy (Infra) ─────────────────────────────────
    expert_feedback: Optional[str] = None
    final_summary_template: Optional[str] = None
