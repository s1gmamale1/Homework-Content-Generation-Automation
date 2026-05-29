"""Flow v2 canonical content schemas (Pure Content Automation).

These model the *content* contracts of the new flow — independent of any
runtime or platform. Structural rules from the plan/standard are enforced
here so malformed content fails fast at the boundary:

- ``GenerationProfile`` — difficulty as metadata, never a phase switch (§12).
- ``DecisionProcessExplanation`` (DPE) — open-ended; ``options`` is always
  ``None`` so it can never be modeled as an MCQ (§6, CBP standard).
- ``CaseBasedPreview`` — exactly 3 checkpoints, a required DPE, and a final
  simulation that carries both a correct and a wrong path.
- ``SourceMap`` / ``SourceConcept`` — the factual anchor; defined here as the
  shared mock contract that lets phase generators start in parallel (§3). The
  real builder lands in Phase 2.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# GenerationProfile (§12) — difficulty is metadata, not branch-skipping
# ─────────────────────────────────────────────────────────────────────


class GenerationProfile(BaseModel):
    """Per-job generation metadata. Difficulty changes the depth/complexity of
    generated items; it never skips proving learning or branches the phase
    list (the old easy/hard switch is gone)."""

    subject_family: str
    difficulty: str = "standard"
    grade_band: Optional[str] = None
    target_skills: list[str] = Field(default_factory=list)
    language: str = "uz"


# ─────────────────────────────────────────────────────────────────────
# Decision Process Explanation (§6) — the load-bearing reasoning slot
# ─────────────────────────────────────────────────────────────────────

DPEComponent = Literal["concept", "method", "mistake"]


class DecisionProcessExplanation(BaseModel):
    """Open-ended production-reasoning prompt. NEVER an MCQ — ``options`` is
    pinned to ``None`` so a generator cannot downgrade it to a 4th checkpoint."""

    prompt: str
    expected_components: list[DPEComponent]
    rubric: dict
    sample_acceptable_answer: str
    eval_mode: Literal["ai", "rubric_ai"] = "ai"
    min_chars: int = 60
    options: None = None  # never an MCQ


# ─────────────────────────────────────────────────────────────────────
# Case-Based Preview (§6 + CBP generation standard §5)
# ─────────────────────────────────────────────────────────────────────

CheckpointIntent = Literal["identify", "decide", "justify_or_avoid_mistake"]
CheckpointForm = Literal["mcq", "choice", "short_select", "true_false"]


class CaseSetup(BaseModel):
    narrative: str
    student_role: str
    task: str


class CaseCheckpoint(BaseModel):
    """Low-friction recognition / decision check. The production reasoning
    lives in the DPE, not here."""

    intent: CheckpointIntent
    form: CheckpointForm
    question: str
    options: list[str] = Field(default_factory=list)
    correct_index: Optional[int] = None
    feedback: str


class CaseSimulation(BaseModel):
    """Final consequence. Must show both the correct and a common wrong path."""

    correct_path: str
    wrong_path: str
    why_wrong_fails: str = ""


class FeedbackSummary(BaseModel):
    understood: str
    mistake: str
    review: str


class CompletionRules(BaseModel):
    pass_condition: str
    retry_condition: str


class CaseBasedPreview(BaseModel):
    title: str
    student_role: str
    case_type: str
    source_concept_ids: list[str] = Field(min_length=1)
    case_setup: CaseSetup
    checkpoints: list[CaseCheckpoint] = Field(min_length=3, max_length=3)
    decision_process_explanation: DecisionProcessExplanation
    final_simulation: CaseSimulation
    feedback_summary: FeedbackSummary
    completion_rules: CompletionRules


# ─────────────────────────────────────────────────────────────────────
# SourceMap mock contract (§3) — factual anchor for parallel generation
# ─────────────────────────────────────────────────────────────────────

SourceConceptKind = Literal["concept", "term", "formula", "process", "skill", "fact"]


class SourceConcept(BaseModel):
    id: str
    label: str
    kind: SourceConceptKind = "concept"
    source_ref: Optional[str] = None  # page/section pointer back to the book


class SourceMap(BaseModel):
    """The factual anchor every generated phase references. Defined now as the
    shared mock contract; the real extraction-driven builder is Phase 2."""

    subject_family: str
    chapter: str
    section: str
    concepts: list[SourceConcept] = Field(min_length=1)

    def concept_ids(self) -> list[str]:
        return [c.id for c in self.concepts]
