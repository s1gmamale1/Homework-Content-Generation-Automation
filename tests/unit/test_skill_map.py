"""Skill registry + mission-mapping contract tests.

Pure pydantic / pure-python — no heavy deps, runs anywhere. Covers schema
construction and the skill_map validators (the implementation of the flow rule
"a practice task must match a target skill").
"""

import pytest

from app.schemas.skills import (
    SkillMapped,
    SkillRegistry,
    SourceConcept,
    TargetSkill,
)
from app.services import skill_map
from app.services.skill_map import SkillMappingError


def make_registry() -> SkillRegistry:
    return SkillRegistry(
        concepts=[
            SourceConcept(
                concept_id="present_perfect_unfinished_time",
                label="Present perfect",
                statement="Used for actions connected to now.",
            ),
            SourceConcept(
                concept_id="past_simple_finished_time",
                label="Past simple",
                statement="Used for finished actions at a named time.",
            ),
        ],
        skills=[
            TargetSkill(
                skill_id="use_present_perfect_in_report",
                statement="The student can report recent events using present perfect.",
                bloom_level="L3",
                pisa_level="L2",
                concept_ids=["present_perfect_unfinished_time"],
            ),
            TargetSkill(
                skill_id="choose_tense_by_time_marker",
                statement="The student can pick past simple vs present perfect by the time marker.",
                concept_ids=[
                    "present_perfect_unfinished_time",
                    "past_simple_finished_time",
                ],
            ),
        ],
    )


class TestSchemaConstruction:
    def test_registry_round_trips(self):
        reg = make_registry()
        dumped = reg.model_dump(mode="json")
        assert SkillRegistry.model_validate(dumped) == reg

    def test_skill_mapped_defaults_empty(self):
        assert SkillMapped().target_skill_ids == []

    def test_schema_is_permissive_no_validator(self):
        # A skill with a dangling concept ref must still PARSE — enforcement is
        # the skill_map layer's job, not the schema's.
        reg = SkillRegistry(
            concepts=[],
            skills=[TargetSkill(skill_id="x", statement="...", concept_ids=["ghost"])],
        )
        assert reg.skills[0].concept_ids == ["ghost"]


class TestValidateRegistry:
    def test_valid_registry_passes(self):
        skill_map.validate_registry(make_registry())

    def test_dangling_concept_ref_rejected(self):
        reg = make_registry()
        reg.skills[0].concept_ids = ["does_not_exist"]
        with pytest.raises(SkillMappingError, match="unknown concept_id"):
            skill_map.validate_registry(reg)

    def test_skill_with_no_concepts_rejected(self):
        reg = make_registry()
        reg.skills[0].concept_ids = []
        with pytest.raises(SkillMappingError, match="maps to no concept"):
            skill_map.validate_registry(reg)

    def test_duplicate_skill_id_rejected(self):
        reg = make_registry()
        reg.skills[1].skill_id = reg.skills[0].skill_id
        with pytest.raises(SkillMappingError, match="duplicate skill_id"):
            skill_map.validate_registry(reg)

    def test_duplicate_concept_id_rejected(self):
        reg = make_registry()
        reg.concepts[1].concept_id = reg.concepts[0].concept_id
        with pytest.raises(SkillMappingError, match="duplicate concept_id"):
            skill_map.validate_registry(reg)


class TestValidateMissionMapping:
    def test_valid_mapping_passes(self):
        skill_map.validate_mission_mapping(["use_present_perfect_in_report"], make_registry())

    def test_empty_mapping_rejected(self):
        with pytest.raises(SkillMappingError, match="no target skill"):
            skill_map.validate_mission_mapping([], make_registry())

    def test_dangling_skill_id_rejected(self):
        with pytest.raises(SkillMappingError, match="unknown skill_id"):
            skill_map.validate_mission_mapping(["nope"], make_registry())


class TestResolveSkills:
    def test_resolves_in_order(self):
        ids = ["choose_tense_by_time_marker", "use_present_perfect_in_report"]
        resolved = skill_map.resolve_skills(ids, make_registry())
        assert [s.skill_id for s in resolved] == ids

    def test_resolve_validates_first(self):
        with pytest.raises(SkillMappingError):
            skill_map.resolve_skills(["ghost"], make_registry())
