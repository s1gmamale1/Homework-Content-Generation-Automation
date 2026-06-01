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
    "- CEFR (A1–B2): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2+, G9→B1, G10→B1+, G11→B2; otherwise infer the "
    "level from the source's own complexity (default to A2 if truly indeterminate). "
    "CEFR controls sentence length, tenses, and vocabulary range — never "
    "exceed the level (no B2 vocabulary in an A1/G5 lesson)."
)

LANGUAGE_RULES = {"english": _LANG_ENGLISH, "_default": _LANG_UZBEK}

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
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_prompt_hash(subject: str, phase_name: str) -> str:
    # Provenance only (recorded on agent_usages rows); does NOT drive cross-job
    # reuse — extract uses its own "builtin:extract:v1" hash in pipeline.py.
    dirname = _resolve_dir(subject, phase_name)
    _b, h = _raw(dirname, phase_name)
    return h
