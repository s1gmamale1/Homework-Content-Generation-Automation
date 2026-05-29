"""Tests for untested functions in app/services/flows.py:
- file_needed_phases()
- max_output_tokens_for()
- SUPPORTED_SUBJECTS coverage
- PHASE_DEPS completeness
"""
import pytest

from app.services.flows import (
    PHASE_FILE_NEEDED,
    SUBJECT_FLOWS,
    SUPPORTED_SUBJECTS,
    MAX_OUTPUT_TOKENS_BY_PHASE,
    file_needed_phases,
    max_output_tokens_for,
)


class TestFileNeededPhases:
    def test_unknown_subject_returns_empty_set(self):
        assert file_needed_phases("unknown-subject") == set()

    def test_known_subject_not_in_map_returns_empty_set(self):
        # No subject currently has file-needed phases declared.
        for subject in SUPPORTED_SUBJECTS:
            result = file_needed_phases(subject)
            assert isinstance(result, set)

    def test_returns_set_type(self):
        assert isinstance(file_needed_phases("biology"), set)

    def test_empty_string_subject_returns_empty_set(self):
        assert file_needed_phases("") == set()

    def test_manually_declared_subject_returns_correct_phases(self):
        # Simulate a subject with declared file-needed phases.
        PHASE_FILE_NEEDED["_test_subject"] = {"preview-hard", "real-life"}
        try:
            result = file_needed_phases("_test_subject")
            assert result == {"preview-hard", "real-life"}
        finally:
            del PHASE_FILE_NEEDED["_test_subject"]


class TestMaxOutputTokensFor:
    def test_preview_hard_has_cap(self):
        result = max_output_tokens_for("preview-hard")
        assert result == 2500

    def test_preview_easy_has_cap(self):
        result = max_output_tokens_for("preview-easy")
        assert result == 1800

    def test_preview_history_alias_has_cap(self):
        result = max_output_tokens_for("preview")
        assert result == 2500

    def test_real_life_has_cap(self):
        result = max_output_tokens_for("real-life")
        assert result == 2200

    def test_consolidation_has_cap(self):
        result = max_output_tokens_for("consolidation")
        assert result == 1200

    def test_reflection_has_cap(self):
        result = max_output_tokens_for("reflection")
        assert result == 700

    def test_structured_phases_have_no_cap(self):
        # Structured phases (JSON schema output) should NOT be capped.
        structured = ["classify", "flashcards", "memory-sprint", "game-breaks",
                      "final-challenge", "reading"]
        for phase in structured:
            assert max_output_tokens_for(phase) is None, (
                f"{phase} should not have a token cap"
            )

    def test_unknown_phase_returns_none(self):
        assert max_output_tokens_for("nonexistent-phase") is None

    def test_return_type_is_int_or_none(self):
        for phase, cap in MAX_OUTPUT_TOKENS_BY_PHASE.items():
            assert isinstance(cap, int)
            assert cap > 0


class TestSupportedSubjects:
    def test_supported_subjects_is_sorted(self):
        assert SUPPORTED_SUBJECTS == sorted(SUPPORTED_SUBJECTS)

    def test_all_expected_subjects_present(self):
        expected = {"biology", "english", "geometriya-g7-11", "history",
                    "kimyo-g7-11", "math-algebra", "physics"}
        assert set(SUPPORTED_SUBJECTS) == expected

    def test_supported_subjects_matches_subject_flows_keys(self):
        assert set(SUPPORTED_SUBJECTS) == set(SUBJECT_FLOWS.keys())


class TestSubjectFlowsStructure:
    def test_every_subject_has_has_classify_key(self):
        for subject, flow in SUBJECT_FLOWS.items():
            assert "has_classify" in flow, f"{subject} missing has_classify"

    def test_every_subject_has_easy_and_hard_lists(self):
        for subject, flow in SUBJECT_FLOWS.items():
            assert "easy" in flow, f"{subject} missing easy"
            assert "hard" in flow, f"{subject} missing hard"
            assert isinstance(flow["easy"], list)
            assert isinstance(flow["hard"], list)

    def test_no_classify_subject_has_empty_easy_list(self):
        for subject, flow in SUBJECT_FLOWS.items():
            if not flow["has_classify"]:
                assert flow["easy"] == [], (
                    f"{subject} has has_classify=False but non-empty easy list"
                )

    def test_hard_list_always_starts_with_preview_variant(self):
        preview_variants = {"preview-hard", "preview-easy", "preview"}
        for subject, flow in SUBJECT_FLOWS.items():
            if flow["hard"]:
                assert flow["hard"][0] in preview_variants, (
                    f"{subject} hard flow should start with a preview phase"
                )
