"""Single Flow v2 phase sequence for every subject (MVP — no classify, no
easy/hard). Subject-specific prompts/flows are a future override layer.
New_Flow.md (docs/Infra_prompts/Flow) is the source of truth, NOT flow.md."""

import re

from app.services import subjects

_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)


# Derived from the single source of truth (app/services/subjects.py). Add a
# subject there, not here.
SUBJECTS: list[str] = subjects.SUBJECT_CODES

# Per-subject RECOMMENDED game (metadata only) — the one mini-game that best
# fits the subject's content type. **Not consumed by the flow:** since every job
# now generates ALL four mini-games (see _GAMES / flow_for), SUBJECT_GAME no
# longer gates generation; it survives only as the "which game fits" hint for
# downstream curation (and one test). Do NOT delete it as "unused".
SUBJECT_GAME: dict[str, str] = {c: d.game for c, d in subjects.REGISTRY.items()}

_BASE_PHASES: list[str] = [
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
]

# English-only head phase (2026-08-25): a Topic Vocabulary glossary that reads
# BEFORE the Case-Based Preview in the packet. List position controls
# phase_order / display / archive order only — it has no PHASE_DEPS entry, so
# the wave scheduler still runs it in wave 1 alongside CBP/flashcards
# (lesson_context is its only input). The one subject-specific deviation from
# the single shared flow.
_ENGLISH_HEAD: list[str] = ["vocabulary"]

# All four interactive mini-games run on EVERY job — the full Gamified Practices
# set (rlc + error-detection + these four + boss-arena = all 7) is generated,
# never skipped. Order is fixed here so phase_order / display order is
# deterministic. All four have PHASE_DEPS entries, so the wave scheduler runs
# them concurrently once their shared deps are met (minimal extra wall-clock).
_GAMES: list[str] = [
    "practice-memory-match", "practice-tictactoe",
    "practice-jigsaw", "practice-sentence",
]


def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    head = _ENGLISH_HEAD if subject == "english" else []
    return [*head, *_BASE_PHASES, *_GAMES, "boss-arena", "reflection"]


def teacher_material_flow_for(subject: str) -> list[str]:
    """Phase sequence for a kind='teacher_material' job: a single teacher-deck
    phase, no games/boss-arena/reflection. `extract` still runs ahead of it as
    the pipeline's pinned head (not part of this content-phase list) and feeds
    the deck via lesson_context, same as every other phase — teacher-deck is
    deliberately absent from PHASE_DEPS below (no prior_outputs dependency)."""
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return ["teacher-deck"]


SUPPORTED_SUBJECTS: list[str] = sorted(SUBJECTS)


# ─────────────────────────────────────────────────────────────────────
# Token-optimisation maps
# ─────────────────────────────────────────────────────────────────────

# Per-phase declaration of which prior phase outputs each phase actually
# consumes. Phases NOT in this map default to an empty dependency list — they
# only see lesson_context (no prior phase prose), which keeps prompts small.
#
# Some entries list aliases (e.g., 'preview-hard' / 'preview-easy' / 'preview')
# because different subjects use different preview phase names; the runtime
# picks whichever exists in the current job's prior_outputs (one per category).
PHASE_DEPS: dict[str, list[str]] = {
    "memory-check":             ["flashcards"],
    "practice-rlc":             ["case-based-preview", "flashcards"],
    "practice-error-detection": ["case-based-preview", "flashcards", "memory-check"],
    "practice-memory-match":    ["flashcards", "memory-check"],
    "practice-tictactoe":       ["case-based-preview", "flashcards"],
    "practice-jigsaw":          ["case-based-preview", "flashcards"],
    "practice-sentence":        ["case-based-preview", "flashcards"],
    "boss-arena":               ["case-based-preview", "flashcards", "memory-check"],
    "reflection":               ["case-based-preview", "boss-arena"],
}


# Per-subject set of phase names that require the original PDF attached (vs.
# working from the extracted lesson_context alone). Default is empty — most
# phases work from lesson_context. Add a phase here only if quality drops on
# tasks that need the actual textbook visuals (e.g., re-rendering SVG diagrams).
PHASE_FILE_NEEDED: dict[str, set[str]] = {
    # Examples (uncomment to opt in):
    # "biology": {"preview-hard", "real-life"},
    # "physics": {"preview-hard"},
}


# Output-token caps per phase. Two reasons:
#  1. Direct cost: outputs are billed at ~5x input rate.
#  2. Downstream amplification: preview-hard's output becomes prior_outputs
#     input for 4 dependent phases, so trimming 1K output tokens here saves
#     ~4K input tokens downstream.
# Numbers picked from observed run sizes plus ~20% headroom. Phases not in
# the map use the model default (effectively unlimited within the model's
# response window).
#
# IMPORTANT: structured (JSON-schema) phases are NOT capped here. JSON syntax
# adds 30-50% token overhead over equivalent prose, and the schema already
# bounds the shape — capping risks mid-object truncation that leaves
# `response.parsed = None`. Let the schema do the constraining.
MAX_OUTPUT_TOKENS_BY_PHASE: dict[str, int] = {
    "reflection": 700,
}


def max_output_tokens_for(phase_name: str) -> int | None:
    """Look up the per-phase output cap; None means model default."""
    return MAX_OUTPUT_TOKENS_BY_PHASE.get(phase_name)


def file_needed_phases(subject: str) -> set[str]:
    """Phases for `subject` that should attach the original PDF."""
    return PHASE_FILE_NEEDED.get(subject, set())


def filter_prior_outputs(
    phase_name: str, prior_outputs: dict[str, str]
) -> dict[str, str]:
    """Whittle `prior_outputs` down to just what `phase_name` declared as deps.

    Phases without declared deps get an empty dict — they receive only the
    lesson_context, no prior phase prose. Aliases (preview-hard / preview-easy
    / preview) collapse to a single 'preview' category so we never include two
    variants of the same logical dependency.
    """
    deps = PHASE_DEPS.get(phase_name, [])
    if not deps:
        return {}
    chosen: dict[str, str] = {}
    seen_categories: set[str] = set()
    for name in deps:
        category = name.split("-", 1)[0]
        if category in seen_categories:
            continue
        if name in prior_outputs:
            chosen[name] = _strip_svgs(prior_outputs[name])
            seen_categories.add(category)
    return chosen


def resolve_phase_deps(phase_name: str, content_phases: list[str]) -> set[str]:
    """For a given content-phase sequence, return the set of phase names that
    `phase_name` *actually* depends on (alias-resolved against the live flow).

    Used by the DAG-parallel scheduler to know when a phase is ready to launch.
    Aliases like "preview-hard" / "preview-easy" / "preview" collapse so the
    scheduler waits on whichever variant is in this flow.
    """
    declared = PHASE_DEPS.get(phase_name, [])
    if not declared:
        return set()

    in_flow = set(content_phases)
    by_category: dict[str, list[str]] = {}
    for d in declared:
        by_category.setdefault(d.split("-", 1)[0], []).append(d)

    resolved: set[str] = set()
    for aliases in by_category.values():
        for a in aliases:
            if a in in_flow:
                resolved.add(a)
                break
    return resolved


def order_phase_selection(subject: str, selected: list[str]) -> list[str]:
    """Validate a user's phase selection and order it by the subject's flow.

    Runs *exactly* what the user picked — no dependency auto-expansion. A phase
    whose declared deps are not also selected simply runs on the lesson summary
    alone (the scheduler resolves deps against the live sequence, and
    `filter_prior_outputs` only injects deps that are actually present).

    Returns the selection ordered by the subject's canonical flow (deduped).
    Raises ValueError on an empty selection or any phase not in the subject's
    flow. `extract` is never selectable (it is the pinned head).
    """
    if not selected:
        raise ValueError("phase selection is empty — pick at least one phase")
    flow = flow_for(subject)
    flow_set = set(flow)
    unknown = [p for p in selected if p not in flow_set]
    if unknown:
        raise ValueError(f"phases not in {subject} flow: {unknown}")

    chosen = set(selected)
    return [p for p in flow if p in chosen]


def selection_missing_prompts(
    selected_phases: list[str] | None,
    custom_prompts: dict[str, str] | None,
) -> list[str]:
    """Phases in a 'Pick phases' selection that lack an uploaded custom prompt.

    The pick-phases flow requires the user to upload a prompt (md) for every
    phase they pick — without it we will not generate. Returns the selected
    phases that have no non-empty entry in ``custom_prompts`` (in selection
    order). An empty list means every selected phase is covered.

    Full-packet launches (``selected_phases is None``) are exempt and always
    return ``[]`` — they run the built-in prompts, no upload needed.
    """
    if not selected_phases:
        return []
    prompts = custom_prompts or {}
    return [p for p in selected_phases if not (prompts.get(p) or "").strip()]
