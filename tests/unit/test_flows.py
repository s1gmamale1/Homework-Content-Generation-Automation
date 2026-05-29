"""Tests for app/services/flows.py — all pure functions, no DB or network needed."""
import pytest

from app.services.flows import (
    SUBJECT_FLOWS,
    SUPPORTED_SUBJECTS,
    filter_prior_outputs,
    file_needed_phases,
    max_output_tokens_for,
    resolve_phase_deps,
)


# ─── _strip_svgs (via filter_prior_outputs) ──────────────────────────────────

class TestStripSvgs:
    def test_svg_replaced_with_placeholder(self):
        """SVG blocks inside prior outputs are swapped for [diagram omitted]."""
        outputs = {"preview-hard": "intro <svg><circle r='5'/></svg> end"}
        result = filter_prior_outputs("memory-sprint", outputs)
        assert "[diagram omitted]" in result.get("flashcards", result.get("preview-hard", ""))

    def test_no_svg_unchanged(self):
        outputs = {"flashcards": "plain text output"}
        result = filter_prior_outputs("memory-sprint", outputs)
        assert result == {"flashcards": "plain text output"}

    def test_multiline_svg_replaced(self):
        svg = "<svg\n  width='100'>\n  <rect/>\n</svg>"
        outputs = {"flashcards": f"before {svg} after"}
        result = filter_prior_outputs("memory-sprint", outputs)
        assert "<svg" not in list(result.values())[0]
        assert "[diagram omitted]" in list(result.values())[0]


# ─── filter_prior_outputs ────────────────────────────────────────────────────

class TestFilterPriorOutputs:
    def test_undeclared_phase_gets_empty_dict(self):
        """Phases not in PHASE_DEPS receive no prior outputs."""
        outputs = {"preview-hard": "content", "flashcards": "cards"}
        result = filter_prior_outputs("preview-hard", outputs)
        assert result == {}

    def test_memory_sprint_only_sees_flashcards(self):
        outputs = {"flashcards": "cards", "preview-hard": "preview", "other": "noise"}
        result = filter_prior_outputs("memory-sprint", outputs)
        assert set(result.keys()) == {"flashcards"}

    def test_alias_collapse_preview_variants(self):
        """Only one preview variant should appear even if multiple are present."""
        outputs = {"preview-hard": "hard preview", "preview-easy": "easy preview"}
        result = filter_prior_outputs("real-life", outputs)
        preview_keys = {k for k in result if k.startswith("preview")}
        assert len(preview_keys) == 1

    def test_game_breaks_sees_flashcards_and_memory_sprint(self):
        outputs = {"flashcards": "cards", "memory-sprint": "sprint", "reflection": "noise"}
        result = filter_prior_outputs("game-breaks", outputs)
        assert set(result.keys()) == {"flashcards", "memory-sprint"}

    def test_missing_dep_silently_skipped(self):
        """If a declared dependency is not in prior_outputs, it is omitted."""
        result = filter_prior_outputs("memory-sprint", {})
        assert result == {}

    def test_final_challenge_sees_multiple_deps(self):
        outputs = {
            "preview-hard": "p", "flashcards": "f", "memory-sprint": "m",
        }
        result = filter_prior_outputs("final-challenge", outputs)
        assert "flashcards" in result
        assert "memory-sprint" in result
        assert "preview-hard" in result

    def test_svg_stripped_in_returned_values(self):
        outputs = {"flashcards": "text <svg>ignored</svg> rest"}
        result = filter_prior_outputs("memory-sprint", outputs)
        assert "<svg>" not in result["flashcards"]


# ─── resolve_phase_deps ──────────────────────────────────────────────────────

class TestResolvePhaseDepss:
    def test_no_deps_returns_empty_set(self):
        assert resolve_phase_deps("preview-hard", ["preview-hard"]) == set()

    def test_memory_sprint_waits_on_flashcards(self):
        phases = ["preview-hard", "flashcards", "memory-sprint"]
        result = resolve_phase_deps("memory-sprint", phases)
        assert result == {"flashcards"}

    def test_alias_resolves_to_present_variant(self):
        """flow uses preview-easy, not preview-hard — should resolve to preview-easy."""
        phases = ["preview-easy", "flashcards", "memory-sprint"]
        result = resolve_phase_deps("real-life", phases)
        assert "preview-easy" in result
        assert "preview-hard" not in result

    def test_alias_falls_back_to_plain_preview(self):
        phases = ["preview", "flashcards", "memory-sprint"]
        result = resolve_phase_deps("real-life", phases)
        assert "preview" in result

    def test_missing_dep_not_in_flow_ignored(self):
        """Phase not present in content_phases should not appear in resolved set."""
        phases = ["memory-sprint"]
        result = resolve_phase_deps("memory-sprint", phases)
        assert result == set()

    def test_reflection_depends_on_preview_and_final_challenge(self):
        phases = ["preview-hard", "flashcards", "memory-sprint", "final-challenge", "reflection"]
        result = resolve_phase_deps("reflection", phases)
        assert "preview-hard" in result
        assert "final-challenge" in result


# ─── max_output_tokens_for ───────────────────────────────────────────────────

class TestMaxOutputTokensFor:
    def test_known_phase_returns_int(self):
        result = max_output_tokens_for("preview-hard")
        assert isinstance(result, int)
        assert result == 2500

    def test_preview_easy_lower_than_preview_hard(self):
        assert max_output_tokens_for("preview-easy") < max_output_tokens_for("preview-hard")

    def test_unknown_phase_returns_none(self):
        assert max_output_tokens_for("nonexistent-phase") is None

    def test_flashcards_returns_none(self):
        # Structured phases are intentionally uncapped.
        assert max_output_tokens_for("flashcards") is None

    def test_consolidation_capped(self):
        result = max_output_tokens_for("consolidation")
        assert result is not None
        assert result <= 1500


# ─── file_needed_phases ──────────────────────────────────────────────────────

class TestFileNeededPhases:
    def test_known_subject_no_override_returns_empty_set(self):
        result = file_needed_phases("biology")
        assert isinstance(result, set)

    def test_unknown_subject_returns_empty_set(self):
        assert file_needed_phases("not-a-subject") == set()


# ─── SUBJECT_FLOWS / SUPPORTED_SUBJECTS ──────────────────────────────────────

class TestSubjectFlows:
    def test_all_subjects_in_supported_list(self):
        assert set(SUPPORTED_SUBJECTS) == set(SUBJECT_FLOWS.keys())

    def test_supported_subjects_sorted(self):
        assert SUPPORTED_SUBJECTS == sorted(SUPPORTED_SUBJECTS)

    @pytest.mark.parametrize("subject", ["biology", "english", "history", "math-algebra", "physics"])
    def test_required_subjects_present(self, subject):
        assert subject in SUBJECT_FLOWS

    @pytest.mark.parametrize("subject,has_classify", [
        ("biology", True),
        ("english", False),
        ("history", False),
        ("math-algebra", True),
        ("physics", True),
    ])
    def test_has_classify_flag_correct(self, subject, has_classify):
        assert SUBJECT_FLOWS[subject]["has_classify"] is has_classify

    def test_english_has_no_easy_phases(self):
        assert SUBJECT_FLOWS["english"]["easy"] == []

    def test_history_has_no_easy_phases(self):
        assert SUBJECT_FLOWS["history"]["easy"] == []

    @pytest.mark.parametrize("subject", SUPPORTED_SUBJECTS)
    def test_every_subject_has_hard_phases(self, subject):
        assert len(SUBJECT_FLOWS[subject]["hard"]) > 0

    def test_biology_hard_has_final_challenge(self):
        assert "final-challenge" in SUBJECT_FLOWS["biology"]["hard"]

    def test_biology_hard_has_reflection(self):
        assert "reflection" in SUBJECT_FLOWS["biology"]["hard"]
