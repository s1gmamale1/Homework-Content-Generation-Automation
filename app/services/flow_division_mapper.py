"""Flow v2 division mapper (Phase 2 C).

Given a job's subject + resolved difficulty (mode), organize the *active* phase
sequence into the four Flow v2 divisions and emit the **enabled set** of phases
and practice games. The registry-coverage gate (Phase 2 E.2) checks that every
enabled item has a mapped prompt in the Infra registry.

This reads the canonical phase sequences from ``flows.SUBJECT_FLOWS`` — it does
NOT invent a parallel flow definition. It groups those phases into divisions and
attaches the family's practice-game picks (v1 active games only, per the
framework freeze).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import flows

# Subject -> family. Also the canonical source for the prompt-registry resolver
# (Phase 2 D.2). Mirrors the framework's 5 locked families.
SUBJECT_FAMILY: dict[str, str] = {
    "math-algebra": "math_family",
    "geometriya-g7-11": "math_family",
    "physics": "sciences",
    "kimyo-g7-11": "sciences",
    "biology": "sciences",
    "english": "languages",
    "history": "humanities",
}

# Which homework phases belong to each Flow v2 division. A phase may be absent
# from a given subject's flow; the mapper only reports phases actually present.
# Preview aliases (preview / preview-easy / preview-hard) all live in Learning.
_DIVISION_PHASES: dict[str, tuple[str, ...]] = {
    "learning": (
        "preview",
        "preview-easy",
        "preview-hard",
        "flashcards",
        "memory-sprint",
        "reading",
    ),
    "practice": ("game-breaks", "real-life"),
    "boss": ("final-challenge",),
    "reflection": ("consolidation", "reflection"),
}

# v1 active games (all others frozen for v2 per the framework).
V1_ACTIVE_GAMES: tuple[str, ...] = (
    "adaptive_quiz",
    "tile_match",
    "memory_match",
    "sentence_fill",
)

# Practice games per family. game-breaks composes from these; the mapper records
# which are enabled so the coverage gate can require a registry prompt for each.
_FAMILY_GAMES: dict[str, tuple[str, ...]] = {
    "math_family": ("adaptive_quiz", "tile_match", "memory_match"),
    "sciences": ("adaptive_quiz", "tile_match", "memory_match"),
    "languages": ("sentence_fill", "memory_match", "adaptive_quiz"),
    "humanities": ("adaptive_quiz", "memory_match", "tile_match"),
}


@dataclass
class FlowDivisionPlan:
    subject: str
    family: str
    difficulty: str  # "easy" | "hard"
    phases: list[str]  # full active content sequence
    divisions: dict[str, list[str]]  # division -> enabled phases present in flow
    practice_games: list[str]  # enabled practice game types (family picks)

    def enabled_items(self) -> list[str]:
        """Flat list of every enabled phase + game, for the coverage gate.

        Games are namespaced ``game:<type>`` so they don't collide with phase
        names when the registry resolver checks coverage.
        """
        return list(self.phases) + [f"game:{g}" for g in self.practice_games]

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "family": self.family,
            "difficulty": self.difficulty,
            "phases": self.phases,
            "divisions": self.divisions,
            "practice_games": self.practice_games,
        }


def family_for_subject(subject: str) -> str:
    """Subject -> family label. Unknown subjects fall back to 'humanities'."""
    return SUBJECT_FAMILY.get(subject, "humanities")


def build_division_plan(*, subject: str, difficulty: str | None) -> FlowDivisionPlan:
    """Build the division plan for a (subject, difficulty) pair.

    ``difficulty`` may be None for always-Hard subjects (english, history) whose
    flow has no easy pipeline — we fall back to the hard sequence.
    """
    flow = flows.SUBJECT_FLOWS.get(subject)
    if flow is None:
        raise KeyError(f"unknown subject: {subject!r}")

    seq = (flow.get(difficulty) if difficulty else None) or flow.get("hard") or []
    phases = list(seq)
    present = set(phases)
    resolved_difficulty = difficulty if (difficulty and flow.get(difficulty)) else "hard"

    divisions = {
        div: [p for p in members if p in present]
        for div, members in _DIVISION_PHASES.items()
    }

    family = family_for_subject(subject)
    games: list[str] = []
    if "game-breaks" in present:
        games = [
            g
            for g in _FAMILY_GAMES.get(family, V1_ACTIVE_GAMES)
            if g in V1_ACTIVE_GAMES
        ]

    return FlowDivisionPlan(
        subject=subject,
        family=family,
        difficulty=resolved_difficulty,
        phases=phases,
        divisions=divisions,
        practice_games=games,
    )
