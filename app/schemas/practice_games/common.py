"""Shared building blocks for the Case-Based Preview interaction games.

Memory Matching, Sentence Filling, Tic-Tac-Toe, and Jigsaw Matching all share
this skeleton (per their Infra specs):

    Case Setup
    → Checkpoint 1: Identify   (MCQ)
    → Learning Block 1
    → Checkpoint 2: Decide     (MCQ)
    → Learning Block 2
    → Checkpoint 3: Justify    (MCQ — never open-ended)
    → Decision Process Explanation  (open; after C3, before the consequence)
    → Final Simulation  (correct path AND weak path)
    → AI Feedback Summary

These models are PERMISSIVE. The hard-rules (exactly 3 MCQ checkpoints, C3 is
MCQ, DPE has 3 components, both consequence paths present, provenance enum,
concept trace) are enforced in app/services/game_conformance.py.

`is_correct` on options is server-only — stripped before the student payload.
"""

from typing import Literal, Optional

from pydantic import BaseModel

CheckpointKind = Literal["identify", "decide", "justify"]
MistakeProvenance = Literal["source", "inferred"]
CompletionStatus = Literal["passed", "needs_retry"]
ConsequenceKind = Literal["correct", "weak"]
ExplanationComponent = Literal["concept", "method", "mistake", "relationship", "action", "meaning"]


class MCQOption(BaseModel):
    label: str
    is_correct: bool = False  # server-only
    feedback: Optional[str] = None


class MCQCheckpoint(BaseModel):
    kind: CheckpointKind
    question: str
    options: list[MCQOption] = []  # expects >=2, exactly 1 correct


class DecisionProcessExplanation(BaseModel):
    """Open-ended reasoning slot — appears AFTER checkpoint 3, BEFORE the
    consequence (commit-before-consequence). Never has options."""

    prompt: str
    expected_components: list[str] = []  # expects 3 (e.g. concept · method · mistake)
    pass_condition: Optional[str] = None
    sample_acceptable_answer: Optional[str] = None
    rubric_full: Optional[str] = None  # server-only
    rubric_partial: Optional[str] = None  # server-only
    rubric_retry: Optional[str] = None  # server-only


class ConsequencePath(BaseModel):
    kind: ConsequenceKind
    description: str


class FinalSimulation(BaseModel):
    correct: ConsequencePath
    weak: ConsequencePath


class FeedbackSummary(BaseModel):
    understood: Optional[str] = None
    mistake: Optional[str] = None
    review: Optional[str] = None
    completion_status: CompletionStatus = "needs_retry"


class CommonMistake(BaseModel):
    text: str
    provenance: MistakeProvenance


class CaseMetadata(BaseModel):
    subject: str
    grade: Optional[str] = None
    topic: Optional[str] = None
    source_concept: Optional[str] = None
    required_skill: str
    case_type: str
    student_role: str


class CaseBasedInteraction(BaseModel):
    """Base for the four CBP interaction-mode games. Game-specific content is
    added by each subclass (cards, chips, board, pieces)."""

    metadata: CaseMetadata
    source_concept_ids: list[str] = []  # trace to SourceMap concepts (expects >=1)
    common_mistake: CommonMistake
    case_setup: str
    checkpoints: list[MCQCheckpoint] = []  # expects exactly 3: identify, decide, justify
    learning_blocks: list[str] = []
    decision_process_explanation: DecisionProcessExplanation
    final_simulation: FinalSimulation
    feedback_summary: FeedbackSummary
