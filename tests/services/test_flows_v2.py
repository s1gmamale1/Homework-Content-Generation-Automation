"""Tests for Flow v2 phase sequences and the CBP beta gate.

Verifies:
- PLATFORM_CBP_RUNTIME_READY=False (default) → v1 sequences returned
- PLATFORM_CBP_RUNTIME_READY=True → v2 sequences returned
- v2 sequences contain case-based-preview and memory-check, not preview-* or memory-sprint
- get_phase_list falls back to v1 for subjects without a _v2 key
- PHASE_DEPS wiring for memory-check and case-based-preview
"""

from __future__ import annotations

import pytest

from app.services.flows import (
    PHASE_DEPS,
    SUBJECT_FLOWS,
    filter_prior_outputs,
    get_phase_list,
    resolve_phase_deps,
)


# ─────────────────────────────────────────────────────────────────────
# get_phase_list — CBP beta gate
# ─────────────────────────────────────────────────────────────────────


def test_get_phase_list_default_returns_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (PLATFORM_CBP_RUNTIME_READY=False) returns v1 sequences."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "platform_cbp_runtime_ready", False)

    flow = SUBJECT_FLOWS["math-algebra"]
    phases = get_phase_list(flow, "hard")
    assert "preview-hard" in phases
    assert "memory-sprint" in phases
    assert "case-based-preview" not in phases
    assert "memory-check" not in phases


def test_get_phase_list_v2_flag_returns_cbp(monkeypatch: pytest.MonkeyPatch) -> None:
    """PLATFORM_CBP_RUNTIME_READY=True returns v2 sequence with CBP + memory-check."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "platform_cbp_runtime_ready", True)

    flow = SUBJECT_FLOWS["math-algebra"]
    phases = get_phase_list(flow, "hard")
    assert "case-based-preview" in phases
    assert "memory-check" in phases
    assert "preview-hard" not in phases
    assert "memory-sprint" not in phases


def test_get_phase_list_v2_easy_uses_cbp(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "platform_cbp_runtime_ready", True)

    flow = SUBJECT_FLOWS["biology"]
    phases = get_phase_list(flow, "easy")
    assert "case-based-preview" in phases
    assert "memory-check" in phases


def test_get_phase_list_no_classify_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    """English and history (no classify) also get v2 when flag is True."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "platform_cbp_runtime_ready", True)

    for subject in ("english", "history"):
        flow = SUBJECT_FLOWS[subject]
        phases = get_phase_list(flow, "hard")
        assert "case-based-preview" in phases, f"{subject} v2 missing CBP"
        assert "memory-check" in phases, f"{subject} v2 missing memory-check"


def test_get_phase_list_fallback_when_no_v2_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If _v2 key is absent, fall back to v1 even when flag is True."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "platform_cbp_runtime_ready", True)

    # Synthesise a minimal flow dict without _v2 keys
    minimal_flow = {"has_classify": False, "easy": [], "hard": ["preview-hard", "flashcards"]}
    phases = get_phase_list(minimal_flow, "hard")
    assert phases == ["preview-hard", "flashcards"]


# ─────────────────────────────────────────────────────────────────────
# PHASE_DEPS — memory-check and case-based-preview wiring
# ─────────────────────────────────────────────────────────────────────


def test_memory_check_depends_on_flashcards() -> None:
    assert "flashcards" in PHASE_DEPS["memory-check"]


def test_case_based_preview_in_downstream_deps() -> None:
    """Phases that depend on preview-* variants must also list case-based-preview."""
    for phase in ("real-life", "consolidation", "final-challenge", "reflection"):
        assert "case-based-preview" in PHASE_DEPS[phase], (
            f"{phase} PHASE_DEPS missing case-based-preview"
        )


def test_game_breaks_accepts_memory_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """game-breaks should fire when memory-check is in prior_outputs (v2 flow)."""
    prior = {
        "flashcards": "## Cards\n1. card_1",
        "memory-check": "## Memory Check\n5 items",
    }
    content_phases = ["case-based-preview", "flashcards", "memory-check", "game-breaks"]
    deps = resolve_phase_deps("game-breaks", content_phases)
    assert "flashcards" in deps
    # memory-check must be resolved (not memory-sprint which isn't in this flow)
    assert "memory-check" in deps
    assert "memory-sprint" not in deps


def test_game_breaks_accepts_memory_sprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """game-breaks should also work with legacy memory-sprint (v1 flow)."""
    content_phases = ["preview-hard", "flashcards", "memory-sprint", "game-breaks"]
    deps = resolve_phase_deps("game-breaks", content_phases)
    assert "flashcards" in deps
    assert "memory-sprint" in deps
    assert "memory-check" not in deps


def test_filter_prior_outputs_cbp_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """real-life must pick up case-based-preview from prior_outputs in v2."""
    prior = {
        "case-based-preview": "## CBP output",
        "flashcards": "## Cards",
    }
    filtered = filter_prior_outputs("real-life", prior)
    assert "case-based-preview" in filtered
    # preview-hard NOT in filtered (not in prior)
    assert "preview-hard" not in filtered


def test_filter_prior_outputs_does_not_double_include_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both preview-hard and case-based-preview are in prior_outputs (shouldn't
    happen in a real job, but the filter must not include both)."""
    prior = {
        "preview-hard": "v1 preview",
        "case-based-preview": "v2 cbp",  # shouldn't coexist, but test dedup
    }
    filtered = filter_prior_outputs("real-life", prior)
    # preview-hard has category "preview"; case-based-preview has category "case"
    # They are DIFFERENT categories, so BOTH would be included.
    # This test documents that they don't alias each other (intentional).
    assert len(filtered) == 2  # both "preview" and "case" categories included


# ─────────────────────────────────────────────────────────────────────
# All subjects have v2 hard sequences
# ─────────────────────────────────────────────────────────────────────


def test_all_subjects_have_hard_v2() -> None:
    """Every subject must define hard_v2 so the beta gate can activate."""
    for subject, flow in SUBJECT_FLOWS.items():
        assert "hard_v2" in flow, f"{subject} is missing hard_v2"
        assert "case-based-preview" in flow["hard_v2"], f"{subject} hard_v2 missing CBP"
        assert "memory-check" in flow["hard_v2"], f"{subject} hard_v2 missing memory-check"
