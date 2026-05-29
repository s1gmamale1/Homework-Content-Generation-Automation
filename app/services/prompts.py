import hashlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_cache: dict[str, dict[str, str]] = {}
_hash_cache: dict[str, dict[str, str]] = {}


def _load_subject(subject: str) -> tuple[dict[str, str], dict[str, str]]:
    subject_dir = PROMPTS_DIR / subject
    if not subject_dir.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {subject_dir}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in subject_dir.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all() -> None:
    from app.services.flows import SUPPORTED_SUBJECTS

    for subject in SUPPORTED_SUBJECTS:
        bodies, hashes = _load_subject(subject)
        _cache[subject] = bodies
        _hash_cache[subject] = hashes


def get_prompt(subject: str, phase_name: str, provider_suffix: str = "") -> str:
    if subject not in _cache:
        bodies, hashes = _load_subject(subject)
        _cache[subject] = bodies
        _hash_cache[subject] = hashes
    if phase_name not in _cache[subject]:
        raise KeyError(f"Prompt {subject}/{phase_name}.md not found")
    body = _cache[subject][phase_name]
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_prompt_hash(subject: str, phase_name: str) -> str:
    if subject not in _hash_cache:
        get_prompt(subject, phase_name)
    return _hash_cache[subject][phase_name]
