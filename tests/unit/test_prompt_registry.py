"""Tests for app/services/prompt_registry.py — runs against the real Infra files
and the committed pinned manifest. Pure logic, no DB/network."""

import pytest

from app.services import prompt_registry as pr
from app.services.flow_division_mapper import build_division_plan
from app.services.flows import SUPPORTED_SUBJECTS


class TestResolve:
    def test_preview_resolves_to_infra_cbp(self):
        e = pr.resolve("preview", "math-algebra")
        assert e.kind == "infra"
        assert "Case-Based Preview" in e.path
        assert e.exists

    def test_preview_alias_hard_resolves(self):
        assert pr.resolve("preview-hard", "physics").kind == "infra"

    def test_flashcards_humanities_family(self):
        e = pr.resolve("flashcards", "history")
        assert e.kind == "infra"
        assert "humanities" in e.path

    def test_final_challenge_infra_boss_arena(self):
        e = pr.resolve("final-challenge", "history")
        assert e.kind == "infra"
        assert "Boss Arena" in e.path

    def test_game_memory_match_infra(self):
        e = pr.resolve("game:memory_match", "history")
        assert e.kind == "infra"
        assert e.exists

    def test_builtin_phase_fallback(self):
        e = pr.resolve("memory-sprint", "history")
        assert e.kind == "builtin"
        assert e.path == "prompts/history/memory-sprint.md"

    def test_unknown_game_is_missing(self):
        assert pr.resolve("game:does_not_exist", "history").kind == "missing"


class TestCoverage:
    @pytest.mark.parametrize("subject", SUPPORTED_SUBJECTS)
    def test_every_subject_hard_plan_is_covered(self, subject):
        plan = build_division_plan(subject=subject, difficulty="hard")
        cov = pr.resolve_plan_coverage(plan)
        assert cov["missing"] == [], f"{subject}: {cov['missing']}"
        assert cov["covered"] is True

    def test_coverage_items_carry_provenance(self):
        cov = pr.resolve_plan_coverage(
            build_division_plan(subject="history", difficulty=None)
        )
        for it in cov["items"]:
            assert it["path"]
            assert it["kind"] in {"infra", "builtin"}


class TestManifest:
    def test_build_manifest_all_files_exist(self):
        m = pr.build_manifest()
        assert m["entry_count"] > 0
        for key, meta in m["entries"].items():
            assert meta["exists"], f"missing infra file for {key}"
            assert meta["sha256"]

    def test_integrity_ok_against_committed_pin(self):
        rep = pr.integrity_report()
        assert rep["pinned_present"] is True
        assert rep["drifted"] == []


class TestGate:
    def test_assert_covered_raises_on_missing(self):
        with pytest.raises(pr.CoverageError):
            pr.assert_covered({"missing": ["game:x"]})

    def test_assert_covered_passes_when_empty(self):
        pr.assert_covered({"missing": []})  # must not raise
