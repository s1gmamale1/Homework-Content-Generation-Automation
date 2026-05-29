"""Error Detection — recognition + construction game.

Show work with exactly ONE error; the student taps the broken block and TYPES
the correction (no auto-reveal). Spec:
docs/Infra_prompts/Gamified Practices/Error Detection/Error_Detection_Specification.md

Server-only: is_error, correct_answer_for_error_block, accepted_variants,
expected_reasoning_keywords (and per-block is_error flags).
"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.skills import SkillMapped

ErrorPattern = Literal["math_equation", "grammar_sentence", "science_diagram"]


class ErrorDetectionBlock(BaseModel):
    id: str
    content: str
    is_error: bool = False  # server-only — exactly one block is True per task


class ErrorDetectionTask(SkillMapped):
    # target_skill_ids inherited from SkillMapped — maps to SourceMap skills.
    pattern: ErrorPattern
    source_concept_ids: list[str] = []  # trace to SourceMap concepts (expects >=1)
    grade_band: Optional[str] = None
    blocks: list[ErrorDetectionBlock] = []  # exactly one is_error=True
    correct_answer_for_error_block: str  # server-only
    accepted_variants: list[str] = []  # server-only — tolerated formattings
    common_mistake_source: str  # the real student error this mirrors
    hint: str  # never reveals the answer
    why_prompt: Optional[str] = None  # mandatory for math/science patterns
    expected_reasoning_keywords: list[str] = []  # server-only
    correct_feedback: Optional[str] = None
    wrong_correction_feedback: Optional[str] = None
    reveal_feedback: Optional[str] = None  # server-only — shown only after 2nd wrong try
    diagram_svg: Optional[str] = None  # only for pattern="science_diagram"
