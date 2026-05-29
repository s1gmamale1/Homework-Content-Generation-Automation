"""Regression tests for the Practice Arc flow swap (PR-3).

Confirms the legacy generic practice phases (``game-breaks``, the standalone
``real-life``, and ``consolidation``) are gone from every subject sequence and
replaced by typed, source-traced Practice Arc games that sit between the
learning sections and the Boss Arena. Reflection is kept (per New_Flow.md).
"""

from __future__ import annotations

from app.services import agent
from app.services import pipeline
from app.services.flows import PHASE_DEPS, SUBJECT_FLOWS

LEGACY = {"game-breaks", "real-life", "consolidation"}
PRACTICE_PHASES = {
    "practice-rlc",
    "practice-error-detection",
    "practice-memory-match",
    "practice-tictactoe",
    "practice-jigsaw",
    "practice-sentence",
}
LEARNING = {"case-based-preview", "flashcards", "memory-check"}


def _all_sequences():
    for subject, flow in SUBJECT_FLOWS.items():
        for seq_name in ("easy", "hard"):
            seq = flow.get(seq_name, [])
            if seq:
                yield subject, seq_name, seq


def test_no_legacy_practice_phases_remain() -> None:
    for subject, seq_name, seq in _all_sequences():
        assert not (set(seq) & LEGACY), (
            f"{subject}/{seq_name} still has legacy phases: {set(seq) & LEGACY}"
        )


def test_every_hard_flow_has_practice_arc_then_boss() -> None:
    for subject, flow in SUBJECT_FLOWS.items():
        hard = flow.get("hard", [])
        if not hard:
            continue
        games = [p for p in hard if p in PRACTICE_PHASES]
        assert len(games) >= 2, f"{subject}/hard needs >=2 practice games, got {games}"
        assert "boss-arena" in hard, f"{subject}/hard missing boss-arena"
        # Every practice game sits after the learning sections and before Boss.
        boss_idx = hard.index("boss-arena")
        mc_idx = hard.index("memory-check")
        for g in games:
            assert mc_idx < hard.index(g) < boss_idx, (
                f"{subject}/hard: {g} must be between memory-check and boss-arena"
            )
        assert hard[-1] == "reflection", f"{subject}/hard must end with reflection"


def test_practice_phases_used_are_registered_schemas() -> None:
    used: set[str] = set()
    for _subject, _seq_name, seq in _all_sequences():
        used |= {p for p in seq if p in PRACTICE_PHASES}
    # Every practice phase that appears in a flow must have a structured schema
    # and a JSON column setter, or the pipeline can't persist its output.
    for phase in used:
        assert phase in agent.STRUCTURED_PHASE_SCHEMAS, f"{phase} not registered"
        assert phase in pipeline._JSON_COLUMN_SETTERS, f"{phase} has no JSON setter"


def test_all_six_games_are_used_somewhere() -> None:
    used: set[str] = set()
    for _subject, _seq_name, seq in _all_sequences():
        used |= {p for p in seq if p in PRACTICE_PHASES}
    assert used == PRACTICE_PHASES, f"unused games: {PRACTICE_PHASES - used}"


def test_practice_phase_deps_are_learning_sections() -> None:
    for phase in PRACTICE_PHASES:
        deps = set(PHASE_DEPS.get(phase, []))
        assert deps, f"{phase} should declare learning-section deps"
        assert deps.issubset(LEARNING), (
            f"{phase} deps {deps} must be a subset of the learning sections"
        )


def test_legacy_phase_deps_removed() -> None:
    for legacy in LEGACY:
        assert legacy not in PHASE_DEPS, f"stale PHASE_DEPS entry for {legacy}"
