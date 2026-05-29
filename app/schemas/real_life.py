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


class RLCAnswerKey(BaseModel):
    """The reveal for the reverse-test variant. Labeled distinctly so a
    downstream consumer (and `beta_export`) can split it from student-visible
    text — this is the ONLY place the governing formula is named (spec §11/§12).

    Server-only by construction: `to_platform_case` never maps it into the
    student payload, so the inferred formula cannot reach the browser.
    """

    inferred_formula: str  # the named formula/relationship the student infers
    derivation: str  # how it is reconstructed from the scenario data
    concept_tags: list[str] = []


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

    # ── reverse-test extras (variant="reverse_test_same_story_new_numbers") ──
    # red_herring: mandatory G7+ irrelevant datum (spec §6). missing_info_pool:
    # G10+ incomplete-info variant (§6). answer_key: the reveal — the named
    # formula lives ONLY here; required for the reverse-test variant and enforced
    # by game_conformance.validate_real_life (kept Optional so the
    # expert_case_5_step variant stays valid without it).
    red_herring: Optional[str] = None
    missing_info_pool: list[str] = []
    answer_key: Optional[RLCAnswerKey] = None

    # ── post-scenario pedagogy (Infra) ─────────────────────────────────
    expert_feedback: Optional[str] = None
    final_summary_template: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Reverse-test Strip-Test helpers (spec §11/§12)
#
# The reverse-test rule: the governing formula/method is NEVER named in the
# student-visible body — the student must INFER it. The named formula lives
# only in `answer_key`. These helpers verify the formula did not leak into any
# student-visible field, adapted to the host's fixed 5-step structure.
# ─────────────────────────────────────────────────────────────────────


def student_visible_text(rlc: "RealLifeChallenge") -> str:
    """Concatenate every field a student reads BEFORE the reveal — the text the
    inferred formula must NOT appear in. Excludes `answer_key` (the reveal) and
    server-only fields (consequences/keywords are stripped before the student
    sees them anyway)."""
    parts: list[str] = [rlc.role, rlc.task, rlc.context, rlc.prediction_prompt]
    if rlc.red_herring:
        parts.append(rlc.red_herring)
    parts.extend(rlc.missing_info_pool)
    for step in (rlc.decision, rlc.info_request, rlc.final_decision):
        parts.append(step.prompt)
        parts.extend(o.label for o in step.options)
    parts.append(rlc.concept_select.prompt)
    parts.extend(c.label for c in rlc.concept_select.concept_chips)
    parts.append(rlc.reasoning.prompt)
    return "\n".join(p for p in parts if p)


def unnamed_formula_violations(rlc: "RealLifeChallenge") -> list[str]:
    """Return reasons the formula leaked into the student-visible body. Empty
    list == the unnamed-formula rule holds. Case-insensitive substring match on
    the answer-key formula. No-op when there is no answer_key."""
    if rlc.answer_key is None:
        return []
    needle = rlc.answer_key.inferred_formula.strip().lower()
    if needle and needle in student_visible_text(rlc).lower():
        return [
            f"inferred_formula {rlc.answer_key.inferred_formula!r} appears verbatim "
            f"in the student-visible body (reverse-test Strip Test, spec §11)"
        ]
    return []


def reverse_test_conformance_errors(rlc: "RealLifeChallenge") -> list[str]:
    """Reverse-test invariants (spec §11/§12). Empty list == conformant. Only
    meaningful for the reverse_test variant; callers gate on `rlc.variant`."""
    errors: list[str] = []
    if rlc.answer_key is None or not rlc.answer_key.inferred_formula.strip():
        errors.append("reverse_test variant requires a non-empty answer_key.inferred_formula")
    errors.extend(unnamed_formula_violations(rlc))
    return errors
