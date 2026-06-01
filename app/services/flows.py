"""Single Flow v2 phase sequence for every subject (MVP — no classify, no
easy/hard). Subject-specific prompts/flows are a future override layer.
New_Flow.md (docs/Infra_prompts/Flow) is the source of truth, NOT flow.md."""

import re

_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)


SUBJECTS: list[str] = [
    "biology", "english", "geometriya-g7-11", "history",
    "kimyo-g7-11", "math-algebra", "physics",
]

# One flow, all subjects. Learning sections -> practice arc (2 light games) ->
# Boss -> Reflection. CBP-mode games are Path B (after CbpModeGame is lightened).
GENERAL_FLOW: list[str] = [
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
    "boss-arena", "reflection",
]


def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return list(GENERAL_FLOW)


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
