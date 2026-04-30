"""Per-subject phase sequences. Each subject's flow.md is the source of truth;
sequences below were committed to code during implementation by reading each
flow.md once. Update here when prompts/<subject>/flow.md changes."""

SUBJECT_FLOWS: dict[str, dict] = {
    "biology": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
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
    },
    "geometriya-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
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
    },
    "kimyo-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "math-algebra": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "physics": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
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
    "reading":         ["preview-hard"],                                       # english only
    "memory-sprint":   ["flashcards"],
    "game-breaks":     ["flashcards", "memory-sprint"],
    "real-life":       ["preview-hard", "preview-easy", "preview"],
    "consolidation":   ["preview-hard", "preview-easy", "preview", "flashcards"],
    "final-challenge": ["preview-hard", "preview-easy", "preview",
                        "flashcards", "memory-sprint"],
    "reflection":      ["preview-hard", "preview-easy", "preview", "final-challenge"],
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
            chosen[name] = prior_outputs[name]
            seen_categories.add(category)
    return chosen
