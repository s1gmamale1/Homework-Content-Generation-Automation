"""Per-subject phase sequences. Each subject's flow.md is the source of truth;
sequences below were committed to code during implementation by reading each
flow.md once. Update here when prompts/<subject>/flow.md changes."""

import re

# Strip inline SVGs from prior_outputs before passing them to downstream
# phases. Downstream phases need the *concepts* an upstream taught — they
# don't need the diagrams (and re-paying for ~800 input tokens of <svg> per
# dependent is pure waste). Replaced with a placeholder so the model knows
# a diagram WAS present, just not what it depicted.
_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)

# Flow v2 Practice Arc (PR-3). The single generic ``game-breaks`` plus the
# standalone ``real-life`` and ``consolidation`` phases are replaced by typed,
# source-traced conceptual games drawn from docs/Infra_prompts/Gamified
# Practices. Each subject runs a curated 2-3 game arc (its target-skill fit) —
# NOT all six (New_Flow forbids "random disconnected games" / "tasks that do
# not match target skill"). The arc sits between the learning sections and the
# Boss Arena. ``reflection`` is kept (New_Flow.md keeps Debrief/Marking).
#
# Game phase names and the schema each uses (agent.STRUCTURED_PHASE_SCHEMAS):
#   practice-rlc             -> RealLifeChallenge (standalone; absorbs real-life)
#   practice-error-detection -> ErrorDetection    (standalone)
#   practice-memory-match    -> CbpModeGame (interaction_mode=memory_match)
#   practice-tictactoe       -> CbpModeGame (interaction_mode=tictactoe)
#   practice-jigsaw          -> CbpModeGame (interaction_mode=jigsaw)
#   practice-sentence        -> CbpModeGame (interaction_mode=sentence_fill)
#
# Easy mode keeps the lighter "one strong conceptual practice (no Boss)" shape
# the prior flow used; hard mode runs the full arc into a Boss Arena.
SUBJECT_FLOWS: dict[str, dict] = {
    "biology": {
        "has_classify": True,
        "easy": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "reflection"],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "practice-error-detection", "practice-memory-match",
                 "boss-arena", "reflection"],
    },
    "english": {
        # English is always Hard mode per flow.md — there is no Easy pipeline.
        # Setting has_classify=False makes the orchestrator skip the classify
        # branch entirely and run the hard sequence directly, mirroring history.
        # CEFR level (A1–B2) is handled within the hard prompts themselves
        # rather than via classify branching.
        "has_classify": False,
        "easy": [],
        "hard": ["case-based-preview", "flashcards", "memory-check", "reading",
                 "practice-sentence", "practice-error-detection", "practice-memory-match",
                 "boss-arena", "reflection"],
    },
    "geometriya-g7-11": {
        "has_classify": True,
        "easy": ["case-based-preview", "flashcards", "memory-check",
                 "practice-error-detection", "reflection"],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-error-detection", "practice-jigsaw", "practice-tictactoe",
                 "boss-arena", "reflection"],
    },
    "history": {
        # History is always Hard mode — no Easy pipeline.
        "has_classify": False,
        "easy": [],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "practice-jigsaw", "practice-memory-match",
                 "boss-arena", "reflection"],
    },
    "kimyo-g7-11": {
        "has_classify": True,
        "easy": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "reflection"],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "practice-error-detection", "practice-tictactoe",
                 "boss-arena", "reflection"],
    },
    "math-algebra": {
        "has_classify": True,
        "easy": ["case-based-preview", "flashcards", "memory-check",
                 "practice-error-detection", "reflection"],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-error-detection", "practice-tictactoe", "practice-jigsaw",
                 "boss-arena", "reflection"],
    },
    "physics": {
        "has_classify": True,
        "easy": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "reflection"],
        "hard": ["case-based-preview", "flashcards", "memory-check",
                 "practice-rlc", "practice-error-detection", "practice-tictactoe",
                 "boss-arena", "reflection"],
    },
}


SUPPORTED_SUBJECTS: list[str] = sorted(SUBJECT_FLOWS.keys())


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
    "reading":         ["case-based-preview"],                                 # english only
    "memory-check":    ["flashcards"],
    # Practice Arc games (PR-3). Each requires the learning sections done first
    # — the concept must have appeared upstream before a game tests applying or
    # debugging it (Error Detection in particular needs a correct-form
    # reference earlier in the session).
    "practice-rlc":              ["case-based-preview", "flashcards"],
    "practice-error-detection":  ["case-based-preview", "flashcards", "memory-check"],
    "practice-memory-match":     ["flashcards", "memory-check"],
    "practice-tictactoe":        ["case-based-preview", "flashcards"],
    "practice-jigsaw":           ["case-based-preview", "flashcards"],
    "practice-sentence":         ["case-based-preview", "flashcards"],
    "boss-arena":      ["case-based-preview", "flashcards", "memory-check"],
    "reflection":      ["case-based-preview", "boss-arena"],
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
    "preview-hard":    2500,   # observed ~3.0-3.8K → cap to 2.5K
    "preview-easy":    1800,
    "preview":         2500,   # history alias
    "real-life":       2200,   # observed ~2.0K
    "consolidation":   1200,   # observed ~0.7K
    "reflection":      700,    # observed ~0.5K
    # classify has no cap — it's now schema-constrained (ClassifyDecision)
    # via STRUCTURED_PHASE_SCHEMAS, so the Literal["easy","hard"] enum bounds
    # the output naturally. Capping risks truncating thinking tokens before
    # the model emits the JSON enum value.
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
