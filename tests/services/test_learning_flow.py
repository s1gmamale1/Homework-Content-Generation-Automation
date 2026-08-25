"""Regression tests for the Learning Sections flow (PR-2).

Confirms the single general flow uses `case-based-preview` + `memory-check`
instead of the legacy `preview-*` / `memory-sprint`, and dependencies are
repointed.
"""

from __future__ import annotations

from app.services.flows import PHASE_DEPS, SUBJECTS, flow_for


def test_general_flow_uses_cbp_and_memory_check_not_legacy() -> None:
    legacy = {"preview-easy", "preview-hard", "preview", "memory-sprint"}
    for subject in SUBJECTS:
        seq = flow_for(subject)
        assert not (set(seq) & legacy), f"{subject} still has legacy phases: {seq}"
        # CBP leads the learning sections (english's Topic Vocabulary glossary
        # reads before it); memory-check is present.
        if subject == "english":
            assert seq[:2] == ["vocabulary", "case-based-preview"], (
                f"english should lead vocabulary → CBP: {seq}")
        else:
            assert seq[0] == "case-based-preview", f"{subject} should start with CBP"
        assert "memory-check" in seq, f"{subject} missing memory-check"


def test_learning_phases_present_in_general_flow() -> None:
    for phase in ("case-based-preview", "flashcards", "memory-check"):
        assert phase in flow_for("physics")


def test_memory_check_depends_on_flashcards() -> None:
    assert PHASE_DEPS["memory-check"] == ["flashcards"]
    # Downstream phases now wait on case-based-preview, not preview-*.
    assert "case-based-preview" in PHASE_DEPS["boss-arena"]
