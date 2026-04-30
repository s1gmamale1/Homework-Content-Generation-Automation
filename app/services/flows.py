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
