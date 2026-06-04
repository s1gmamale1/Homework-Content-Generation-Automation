import hashlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
GENERAL_DIR = "_general"
# MVP: general prompts serve every subject. Set True later to prefer a
# subject-specific prompt when prompts/<subject>/<phase>.md exists.
USE_SUBJECT_PROMPTS = False

SUBJECT_LABELS = {
    "biology": "Biology (Biologiya)",
    "english": "English",
    "geometriya-g7-11": "Geometry (Geometriya)",
    "history": "History (Tarix)",
    "kimyo-g7-11": "Chemistry (Kimyo)",
    "math-algebra": "Mathematics / Algebra (Matematika / Algebra)",
    "physics": "Physics (Fizika)",
}

_LANG_UZBEK = (
    "All student-facing text in natural, formal Uzbek (\"Siz\", never \"sen\"). "
    "Preserve every term, formula, number, unit, and symbol exactly as in the "
    "source. Modern professional (non-bazaar) contexts."
)

_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in English; everything that "
    "HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- CEFR (A1–B1+): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2, G9→A2+, G10→B1, G11→B1+ (the Uzbek national "
    "curriculum keeps A2 across the G5–9 band; B1 only after G9); otherwise infer "
    "the level from the source's own complexity (default to A2 if truly "
    "indeterminate). CEFR controls sentence length, tenses, and vocabulary range — "
    "never exceed the level (no B1 vocabulary in an A1/G5 lesson)."
)

LANGUAGE_RULES = {"english": _LANG_ENGLISH, "_default": _LANG_UZBEK}

_SUBJECT_FAMILY = {
    "biology": "sciences",
    "kimyo-g7-11": "sciences",
    "physics": "sciences",
    "math-algebra": "math",
    "geometriya-g7-11": "math",
    "english": "languages",
    "history": "humanities",
}

# --- Case-Based Preview family blocks (injected at {{FAMILY_RULES}}) ---------
# Each ~12-25 lines: visual policy + case framing + family forbids. Ported from
# docs/Infra_prompts/Case-Based Preview/*. Humanities has no CBP spec — authored
# by extrapolating the 3 specs + the humanities Flashcards visual policy.

_CBP_SCIENCES = (
    "**Visual policy:** Sciences default to **IMAGE** — real labs, organisms, "
    "equipment, and phenomena read better as photographs than diagrams. Use inline "
    "`<svg>` for the conceptual layer: particle/molecular diagrams, force vectors, "
    "process flowcharts (photosynthesis stages, digestion path), and before/after "
    "state changes. For a multi-step phenomenon, image the overall scene, then break "
    "the mechanism into SVG stages in the learning blocks. If you would otherwise "
    "generate a raster, emit `![placeholder: <short description> — image gen "
    "required](placeholder)` — never fabricate an image or invent an image URL.\n\n"
    "**Case framing:** Physics — phenomenon → prediction → law/logic → consequence "
    "(observer, engineer, technician verifying a circuit/motion/optics setup). "
    "Chemistry — safety/observation → particle/reaction logic → result (lab "
    "assistant or analyst checking labels, classifying a substance, predicting a "
    "reaction). Biology — observation → system/process → mechanism → prediction "
    "(researcher or ecologist predicting an organism's response).\n\n"
    "**Avoid:** fictionalising a real reaction or phenomenon (no unicorn refraction, "
    "no magic-potion pH); oversimplifying chemistry safety; narrowing organism-wide "
    "concepts (photosynthesis, respiration) to human-only examples unless the topic "
    "is explicitly human biology; copying textbook artwork; rendering a multi-step "
    "mechanism as one dense image instead of SVG stages."
)

_CBP_MATH = (
    "**Visual policy:** Math defaults to **SVG** — numbers and structure render "
    "better as diagrams than photos. Use inline `<svg>` for fraction/area bars, "
    "coordinate planes, graphs, geometric figures (triangles, polygons, circles, "
    "angles, constructions), number lines, formula visualisations, and step-by-step "
    "state diagrams. Use an image only for genuine real-world context (a market, a "
    "club meeting, a workshop) before transitioning to the SVG model; a 4-step SVG "
    "diagram beats one dense image. If a context image is unavailable, emit "
    "`![placeholder: <short description> — image gen required](placeholder)`.\n\n"
    "**Case framing:** practical sharing (a helper distributing a quantity — which "
    "operation?); money/measurement (a shopkeeper, builder, or gardener choosing the "
    "right arithmetic step or formula); error detection (a reviewer spotting which "
    "step in someone's work is wrong). Geometry has the strongest visual demand — "
    "every figure, angle, or construction is SVG.\n\n"
    "**Avoid:** changing numbers, variables, formulas, units, or calculation order; "
    "decorative SVGs that don't carry the actual problem; the 'dragon needs algebra' "
    "trap (the math must be load-bearing); copying textbook artwork; SVGs with tiny "
    "unreadable labels; using an image where SVG would carry the math better."
)

_CBP_LANGUAGES = (
    "**Visual policy:** Languages default to **IMAGE** — communication is "
    "contextual, and a real scene establishes who is talking, where, and how formal "
    "(classroom, market, café, office). Use inline `<svg>` for the linguistic layer: "
    "sentence-structure blocks (subject | verb | object), word-order diagrams, "
    "wrong → corrected sentence comparisons, formal/informal register cards, tense "
    "timelines, and conjugation tables. Lead with the scene image, then use SVG in "
    "the learning blocks to show the structure. If a scene image is unavailable, "
    "emit `![placeholder: <short description> — image gen required](placeholder)` — "
    "never invent an image URL.\n\n"
    "**Case framing:** write a message (a student writing a polite note to a teacher "
    "— which tense/register?); fix grammar (an editor choosing the form that fits); "
    "choose register or respond to a situation (a speaker picking formal vs informal "
    "phrasing). The consequence is the **communication outcome** — the message lands, "
    "or it is unclear/rude/ungrammatical and conveys the wrong meaning.\n\n"
    "**Avoid:** grammar above the target CEFR/grade level; authoring a fresh passage "
    "when the textbook has one; magic-mirror/forest-spirit frames where the language "
    "is decorative; MCQ options that leak the answer via length or obvious formality; "
    "Russian/English calques (translate idiomatically, not word-for-word); cliché "
    "cowboy/cricket contexts unless the textbook is itself about them."
)

_CBP_HUMANITIES = (
    "**Visual policy:** Humanities default to **SVG** for the structural layer — "
    "timelines, causal chains (event A → event B → event C), labelled outline maps, "
    "comparison tables, and dynasty/family trees carry historical reasoning best. "
    "Use an image for real figures and objects: portraits the textbook uses, "
    "monuments, artifacts (coins, manuscripts), and photographic maps where detail "
    "matters. If you would otherwise generate such a raster, emit "
    "`![placeholder: <short description> — image gen required](placeholder)` — never "
    "fabricate an image or invent an image URL.\n\n"
    "**Case framing:** historical decision (an advisor weighing a ruler's options); "
    "source/evidence check (a historian judging which source is strongest); "
    "cause/consequence dilemma (a witness tracing why an event followed another). "
    "The student is advisor, witness, historian, or source-checker — never a modern "
    "professional unless the era naturally fits.\n\n"
    "**Avoid:** invented causality — only assert 'A caused B' when the textbook does, "
    "otherwise present sequence without claimed causation; anachronistic state/place "
    "names (no modern country name for a pre-modern entity); one-sided framing of "
    "contested figures (mirror the textbook's stance, or stay neutral); misquoting "
    "primary sources (reproduce exactly or mark as paraphrase); decorative imagery "
    "instead of the specific monument/artifact the lesson is about."
)

_CBP_DEFAULT = (
    "**Visual policy:** Choose the medium that carries the decision. Use inline "
    "`<svg>` for genuine diagrams — figures, processes, charts, timelines, "
    "before/after states. Use an image only for a real-life scene or context, and "
    "when you would otherwise generate a raster emit `![placeholder: <short "
    "description> — image gen required](placeholder)` — never fabricate an image or "
    "invent an image URL.\n\n"
    "**Case framing:** place the student as a decision-maker in a plausible, "
    "source-aligned situation where the lesson concept is the load-bearing reason "
    "the decision succeeds or fails.\n\n"
    "**Avoid:** decorative visuals that don't carry the concept; fantasy frames where "
    "the subject content is detachable; copying textbook artwork; inventing facts the "
    "textbook does not state."
)

# Family-varying prompt blocks, keyed [phase_name][family] with a phase-level
# "_default". Only CBP + flashcards vary by family; authored in Tasks 2-3.
# Resolution never leaks one family's block to another (see get_prompt).
FAMILY_RULES: dict[str, dict[str, str]] = {
    "case-based-preview": {
        "sciences": _CBP_SCIENCES,
        "math": _CBP_MATH,
        "languages": _CBP_LANGUAGES,
        "humanities": _CBP_HUMANITIES,
        "_default": _CBP_DEFAULT,
    },
    "flashcards": {},           # filled in Task 3
}

_cache: dict[str, dict[str, str]] = {}
_hash_cache: dict[str, dict[str, str]] = {}


def _resolve_dir(subject: str, phase_name: str) -> str:
    if USE_SUBJECT_PROMPTS and (PROMPTS_DIR / subject / f"{phase_name}.md").is_file():
        return subject
    return GENERAL_DIR


def _load_dir(dirname: str) -> tuple[dict[str, str], dict[str, str]]:
    d = PROMPTS_DIR / dirname
    if not d.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {d}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in d.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all() -> None:
    dirs = {GENERAL_DIR}
    if USE_SUBJECT_PROMPTS:
        from app.services.flows import SUPPORTED_SUBJECTS
        dirs.update(SUPPORTED_SUBJECTS)
    for dirname in dirs:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes


def _raw(dirname: str, phase_name: str) -> tuple[str, str]:
    if dirname not in _cache:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes
    if phase_name not in _cache[dirname]:
        raise KeyError(f"Prompt {dirname}/{phase_name}.md not found")
    return _cache[dirname][phase_name], _hash_cache[dirname][phase_name]


def get_prompt(subject: str, phase_name: str, provider_suffix: str = "") -> str:
    dirname = _resolve_dir(subject, phase_name)
    body, _h = _raw(dirname, phase_name)
    body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))
    body = body.replace(
        "{{LANGUAGE_RULES}}",
        LANGUAGE_RULES.get(subject, LANGUAGE_RULES["_default"]),
    )
    phase_blocks = FAMILY_RULES.get(phase_name, {})
    family = _SUBJECT_FAMILY.get(subject)
    family_block = phase_blocks.get(family) or phase_blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_prompt_hash(subject: str, phase_name: str) -> str:
    # Provenance only (recorded on agent_usages rows); does NOT drive cross-job
    # reuse — extract uses its own "builtin:extract:v1" hash in pipeline.py.
    dirname = _resolve_dir(subject, phase_name)
    _b, h = _raw(dirname, phase_name)
    return h
