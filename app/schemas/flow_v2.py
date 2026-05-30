"""Flow v2 canonical content schemas (Pure Content Automation).

These model the *content* contracts of the new flow — independent of any
runtime or platform. Structural rules from the plan/standard are enforced
here so malformed content fails fast at the boundary:

- ``GenerationProfile`` — difficulty as metadata, never a phase switch (§12).
- ``DecisionProcessExplanation`` (DPE) — open-ended; ``options`` is always
  ``None`` so it can never be modeled as an MCQ (§6, CBP standard).
- ``CaseBasedPreview`` — exactly 3 checkpoints, a required DPE, and a final
  simulation that carries both a correct and a wrong path.
- ``SourceMap`` / ``SourceConcept`` — the factual anchor every phase cites by
  concept id (§4/§10). Built by ``agent.extract_source_map`` from the extracted
  lesson context (PR-1) and threaded into every content-phase prompt.
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
    why_wrong_fails: str = Field(min_length=1)


class LearningBlock(BaseModel):
    """Short teaching explanation between checkpoints (CBP standard §5, slots 3 & 5).
    LB1 explains the concept after Checkpoint 1; LB2 shows the method after
    Checkpoint 2. Text-first: ``visual_svg`` is optional and used only when a tiny
    diagram is essential and not already shown in the case."""

    explanation: str = Field(min_length=1)
    title: Optional[str] = None
    visual_svg: Optional[str] = None
    source_concept_id: Optional[str] = None


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
    learning_block_1: LearningBlock
    learning_block_2: LearningBlock
    decision_process_explanation: DecisionProcessExplanation
    final_simulation: CaseSimulation
    feedback_summary: FeedbackSummary
    completion_rules: CompletionRules


# ─────────────────────────────────────────────────────────────────────
# SourceMap (§4/§10) — factual anchor; built by agent.extract_source_map
# ─────────────────────────────────────────────────────────────────────

SourceConceptKind = Literal["concept", "term", "formula", "process", "skill", "fact"]


class SourceConcept(BaseModel):
    id: str
    label: str
    statement: str  # the atomic fact/definition, faithful to the textbook
    kind: SourceConceptKind = "concept"
    source_ref: Optional[str] = None  # page/section pointer back to the book


class SourceMap(BaseModel):
    """The factual anchor every generated phase references by concept id. Built
    by ``agent.extract_source_map`` from the extracted lesson context (PR-1)."""

    subject_family: str
    chapter: str
    section: str
    concepts: list[SourceConcept] = Field(min_length=1)

    def concept_ids(self) -> list[str]:
        return [c.id for c in self.concepts]
