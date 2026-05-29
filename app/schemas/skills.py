"""Skill registry — the lesson's target skills and the concepts they rest on.

Two layers:
- `SourceConcept`: an atomic fact / definition / term the lesson section teaches.
- `TargetSkill`: a "the student can DO X" objective that references >=1 concept.

The registry is the source-of-truth every Practice Arc mission maps to. A
mission that maps to no skill — or to a skill_id not in this registry — is
rejected by `app.services.skill_map` (the implementation of the flow rule
"Practice tasks must match a target skill").

These models are deliberately PERMISSIVE — no Pydantic validators. They are
parsed straight from Gemini structured output, and coupling SDK parsing to
contract enforcement would turn a single under-filled field into a hard
extraction failure. Contract checks (dangling refs, empty mappings) live in
`app.services.skill_map` so the pipeline controls exactly when they fire.
"""

from typing import Optional

from pydantic import BaseModel


class SourceConcept(BaseModel):
    concept_id: str  # stable slug, e.g. "present_perfect_unfinished_time"
    label: str  # short human name
    statement: str  # the atomic fact/definition, faithful to the textbook
    section_ref: Optional[str] = None  # page/section anchor in the lesson


class TargetSkill(BaseModel):
    skill_id: str  # stable slug, e.g. "use_present_perfect_in_report"
    statement: str  # "The student can ..." — a can-do objective
    bloom_level: Optional[str] = None
    pisa_level: Optional[str] = None
    concept_ids: list[str] = []  # references SourceConcept.concept_id (expects >=1)


class SkillRegistry(BaseModel):
    concepts: list[SourceConcept] = []
    skills: list[TargetSkill] = []


class SkillMapped(BaseModel):
    """Mixin for any mission/activity that must bind to target skills.

    Foundation-only: defined here so future missions inherit it. Not yet
    attached to existing missions. Validate `target_skill_ids` against a live
    `SkillRegistry` via `app.services.skill_map.validate_mission_mapping`.
    """

    target_skill_ids: list[str] = []  # expects >=1, each a real SkillRegistry.skill_id
