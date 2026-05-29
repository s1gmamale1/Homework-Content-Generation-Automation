"""Skill-mapping contract enforcement.

This module IS the implementation of the flow rule "Practice tasks must match a
target skill" — there is no rule constant in the repo; the rule is these
raise-on-violation checks. Two surfaces:

- `validate_registry`: the extracted SkillRegistry is internally consistent
  (every skill maps to >=1 real concept; no duplicate ids).
- `validate_mission_mapping`: a mission binds to >=1 skill, and every skill_id
  it names exists in the registry.

Kept out of the Pydantic schemas on purpose (see app/schemas/skills.py): SDK
structured-output parsing stays robust, and callers decide when enforcement
fires (stop-the-line at generation vs. advisory in tests).
"""

from __future__ import annotations

from app.schemas.skills import SkillRegistry, TargetSkill


class SkillMappingError(ValueError):
    """Raised when a registry is inconsistent or a mission maps to no/unknown skill."""


def validate_registry(registry: SkillRegistry) -> None:
    """Assert the registry is internally consistent. Raises SkillMappingError."""
    concept_ids = [c.concept_id for c in registry.concepts]
    known_concepts = set(concept_ids)
    if len(known_concepts) != len(concept_ids):
        dupes = sorted({c for c in concept_ids if concept_ids.count(c) > 1})
        raise SkillMappingError(f"duplicate concept_id(s): {dupes}")

    skill_ids = [s.skill_id for s in registry.skills]
    if len(set(skill_ids)) != len(skill_ids):
        dupes = sorted({s for s in skill_ids if skill_ids.count(s) > 1})
        raise SkillMappingError(f"duplicate skill_id(s): {dupes}")

    for s in registry.skills:
        if not s.concept_ids:
            raise SkillMappingError(f"skill {s.skill_id!r} maps to no concept")
        dangling = [c for c in s.concept_ids if c not in known_concepts]
        if dangling:
            raise SkillMappingError(
                f"skill {s.skill_id!r} references unknown concept_id(s): {dangling}"
            )


def validate_mission_mapping(
    target_skill_ids: list[str], registry: SkillRegistry
) -> None:
    """Assert a mission binds to >=1 skill, all present in the registry.

    Empty mapping is the flow violation "Practice task does not match a target
    skill". Raises SkillMappingError on empty or dangling ids.
    """
    if not target_skill_ids:
        raise SkillMappingError(
            "mission maps to no target skill (a practice task must match a skill)"
        )
    known = {s.skill_id for s in registry.skills}
    dangling = [sid for sid in target_skill_ids if sid not in known]
    if dangling:
        raise SkillMappingError(
            f"mission references unknown skill_id(s): {dangling}"
        )


def validate_concept_trace(
    concept_ids: list[str], registry: SkillRegistry
) -> None:
    """Assert a game/mission traces to >=1 real SourceMap concept.

    Empty is the "disconnected drill" violation — a practice game that traces
    to no lesson concept fails the Strip Test. Dangling ids (not in the
    SourceMap) are also rejected. Raises SkillMappingError.
    """
    if not concept_ids:
        raise SkillMappingError(
            "game traces to no concept (a practice game must trace to the lesson)"
        )
    known = {c.concept_id for c in registry.concepts}
    dangling = [cid for cid in concept_ids if cid not in known]
    if dangling:
        raise SkillMappingError(
            f"game references unknown concept_id(s): {dangling}"
        )


def resolve_skills(
    target_skill_ids: list[str], registry: SkillRegistry
) -> list[TargetSkill]:
    """Return the TargetSkill objects a mission maps to, in id order.

    Validates first, so callers get either the full resolved list or a raise —
    never a silently-partial result.
    """
    validate_mission_mapping(target_skill_ids, registry)
    by_id = {s.skill_id: s for s in registry.skills}
    return [by_id[sid] for sid in target_skill_ids]
