"""Per-subject phase sequences. Each subject's flow.md is the source of truth;
sequences below were committed to code during implementation by reading each
flow.md once. Update here when prompts/<subject>/flow.md changes.

v1 phases: preview-easy / preview-hard + memory-sprint
v2 phases: case-based-preview + memory-check (gated by PLATFORM_CBP_RUNTIME_READY)

Use ``get_phase_list(flow, difficulty)`` rather than ``flow[difficulty]`` to
honour the CBP beta gate at runtime.
"""

import re

# Strip inline SVGs from prior_outputs before passing them to downstream
# phases. Downstream phases need the *concepts* an upstream taught — they
# don't need the diagrams (and re-paying for ~800 input tokens of <svg> per
# dependent is pure waste). Replaced with a placeholder so the model knows
# a diagram WAS present, just not what it depicted.
_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)

SUBJECT_FLOWS: dict[str, dict] = {
    "biology": {
        "has_classify": True,
        # v1 (default, beta-safe)
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        # v2 (activated when PLATFORM_CBP_RUNTIME_READY=true)
        "easy_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "english": {
        # English is always Hard mode per flow.md — there is no Easy pipeline,
        # and no preview-easy.md exists. Setting has_classify=False makes the
        # orchestrator skip the classify branch entirely and run the hard
        # sequence directly, mirroring history. CEFR level (A1–B2) is handled
        # within the hard prompts themselves rather than via classify branching.
        "has_classify": False,
        "easy": [],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "reading", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "reading",
                    "game-breaks", "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "geometriya-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        "easy_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "history": {
        # History is always Hard mode — no Easy pipeline.
        # Canonical structure: 6 mandatory phases (preview, flashcards, memory-sprint,
        # game-breaks, final-challenge, reflection) + consolidation as a conditional
        # 7th. Per v0 design we run consolidation unconditionally and rely on the
        # prompt to self-emit a skip marker when not applicable.
        "has_classify": False,
        "easy": [],
        "hard": ["preview", "flashcards", "memory-sprint", "game-breaks",
                 "consolidation", "final-challenge", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "consolidation", "final-challenge", "reflection"],
    },
    "kimyo-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        "easy_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "math-algebra": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        "easy_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "physics": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
        "easy_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks", "reflection"],
        "hard_v2": ["case-based-preview", "flashcards", "memory-check", "game-breaks",
                    "real-life", "consolidation", "final-challenge", "reflection"],
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
    "reading":         ["preview-hard", "case-based-preview"],                 # english only
    "memory-sprint":   ["flashcards"],
    # memory-check depends on flashcards (same as memory-sprint).
    # Both share the "memory" split-category so the alias resolver picks
    # whichever variant appears in the live prior_outputs.
    "memory-check":    ["flashcards"],
    # game-breaks needs whichever quick-recall phase ran (sprint or check).
    "game-breaks":     ["flashcards", "memory-sprint", "memory-check"],
    # case-based-preview is the v2 alias for preview-* phases; list it after
    # all three v1 aliases so the resolver finds it in v2 flows.
    "real-life":       ["preview-hard", "preview-easy", "preview", "case-based-preview"],
    "consolidation":   ["preview-hard", "preview-easy", "preview", "case-based-preview",
                        "flashcards"],
    "final-challenge": ["preview-hard", "preview-easy", "preview", "case-based-preview",
                        "flashcards", "memory-sprint", "memory-check"],
    "reflection":      ["preview-hard", "preview-easy", "preview", "case-based-preview",
                        "final-challenge"],
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


def get_phase_list(flow: dict, difficulty: str) -> list[str]:
    """Return the content-phase list for the given difficulty, respecting the
    PLATFORM_CBP_RUNTIME_READY beta gate.

    When the flag is True the v2 sequence (case-based-preview + memory-check)
    is used if defined for this subject/difficulty; otherwise the v1 sequence
    is returned (safe default for beta deployments).
    """
    from app.config import settings  # local import avoids circular at module load

    if settings.platform_cbp_runtime_ready:
        v2_key = f"{difficulty}_v2"
        if v2_key in flow:
            return flow[v2_key]
    return flow.get(difficulty, [])
