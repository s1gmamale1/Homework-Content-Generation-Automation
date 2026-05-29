"""Regression tests for the Learning Sections flow swap (PR-2).

Confirms the swap-in-place (no PLATFORM_CBP_RUNTIME_READY gate, no dual _v2
sequences): every subject sequence now uses `case-based-preview` + `memory-check`
instead of the legacy `preview-*` / `memory-sprint`, the new phases are
registered + persisted, and dependencies are repointed.
"""

from __future__ import annotations

from app.services import agent
from app.services import pipeline
from app.services.flows import PHASE_DEPS, SUBJECT_FLOWS


def test_every_sequence_uses_cbp_and_memory_check_not_legacy() -> None:
    legacy = {"preview-easy", "preview-hard", "preview", "memory-sprint"}
    for subject, flow in SUBJECT_FLOWS.items():
        for seq_name in ("easy", "hard"):
            seq = flow.get(seq_name, [])
            if not seq:
                continue
            assert not (set(seq) & legacy), f"{subject}/{seq_name} still has legacy phases: {seq}"
            # CBP leads the learning sections; memory-check is present.
            assert seq[0] == "case-based-preview", f"{subject}/{seq_name} should start with CBP"
            assert "memory-check" in seq, f"{subject}/{seq_name} missing memory-check"


def test_no_platform_gate_or_v2_sequences() -> None:
    # The PLATFORM_CBP_RUNTIME_READY gate + dual easy_v2/hard_v2 sequences were
    # intentionally dropped (no platform/runtime in scope).
    for flow in SUBJECT_FLOWS.values():
        assert "easy_v2" not in flow and "hard_v2" not in flow


def test_learning_phases_registered_and_persisted() -> None:
    assert "case-based-preview" in agent.STRUCTURED_PHASE_SCHEMAS
    assert "memory-check" in agent.STRUCTURED_PHASE_SCHEMAS
    assert "case-based-preview" in pipeline._JSON_COLUMN_SETTERS
    assert "memory-check" in pipeline._JSON_COLUMN_SETTERS


def test_memory_check_depends_on_flashcards() -> None:
    assert PHASE_DEPS["memory-check"] == ["flashcards"]
    # Downstream phases now wait on case-based-preview, not preview-*.
    assert "case-based-preview" in PHASE_DEPS["boss-arena"]
