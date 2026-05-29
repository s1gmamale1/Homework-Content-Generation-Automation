"""Tests for the Flow v2 SourceMap schema, digest renderer, and the master-prompt
threading that grounds downstream phases. Pure logic, no DB/network."""

from app.schemas.flow_v2 import SourceConcept, SourceMap
from app.services import agent
from app.services.source_map import render_source_map_digest


def _sample_map() -> SourceMap:
    return SourceMap(
        subject="history",
        grade=5,
        language="uz",
        section_number="1",
        section_title="Tarix",
        core_concepts=[
            SourceConcept(id="c1", name="Tarix", summary="study of the past"),
            SourceConcept(id="c2", name="Manba", summary="a source"),
        ],
        key_terms=["arxiv", "manba"],
        main_rules_or_formulas=[],
        required_skills=["classify sources"],
    )


class TestSourceMapSchema:
    def test_concepts_have_stable_ids(self):
        sm = _sample_map()
        assert [c.id for c in sm.core_concepts] == ["c1", "c2"]

    def test_defaults(self):
        sm = SourceMap()
        assert sm.core_concepts == []
        assert sm.language == "uz"
        assert sm.grade is None


class TestDigest:
    def test_contains_concept_ids_terms_skills(self):
        d = render_source_map_digest(_sample_map())
        assert "[c1] Tarix" in d
        assert "[c2] Manba" in d
        assert "arxiv" in d
        assert "classify sources" in d

    def test_empty_map_renders_empty(self):
        assert render_source_map_digest(SourceMap()) == ""


class TestMasterPromptThreading:
    def test_digest_injected_with_cite_guard(self):
        d = render_source_map_digest(_sample_map())
        mp = agent._build_master_prompt(
            phase_prompt="P",
            phase_name="flashcards",
            lesson_context="L",
            prior_outputs=None,
            difficulty="easy",
            schema=None,
            provider_suffix="",
            source_map_digest=d,
        )
        assert "SOURCE MAP" in mp
        assert "[c1] Tarix" in mp
        # the "don't print ids in student-facing text" guard must be present
        assert "student-facing" in mp

    def test_absent_when_no_digest(self):
        mp = agent._build_master_prompt(
            phase_prompt="P",
            phase_name="flashcards",
            lesson_context="L",
            prior_outputs=None,
            difficulty="easy",
            schema=None,
            provider_suffix="",
            source_map_digest=None,
        )
        assert "SOURCE MAP" not in mp
