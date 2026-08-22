"""Repository-backed prompt-set registry (PR A of selective-regeneration,
2026-08-17 SDD). A "prompt set" is a named, versioned root directory under
`prompts/sets/<id>/` holding a `_general/` phase-contract tree in the exact
shape `app.services.prompts` already knows how to read (markdown phase
contracts, `structured/<phase>.md` JSON-authoring prompts, and
`structured/teacher-deck.fidelity.md`).

`homework-v1` is the FROZEN legacy set: the pre-refactor `prompts/_general/`
tree relocated byte-for-byte under `prompts/sets/homework-v1/_general/`. Every
prompt-set-aware reader defaults to it via `LEGACY_PROMPT_SET_ID`, so every
existing call site (which never passes `prompt_set_id`) keeps resolving the
exact same bytes it always has -- that equivalence is what makes this PR a
pure, behavior-preserving refactor.

The manifest `prompts/prompt-sets.json` is the single source of truth for
which sets exist. It is validated once per process (cached) and never
executes or trusts anything it doesn't declare: unknown ids, duplicate ids,
roots that escape `PROMPTS_DIR`, and missing required contract files all fail
loudly at registry-build time -- before any model call could be attempted
against a broken set.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
MANIFEST_PATH = PROMPTS_DIR / "prompt-sets.json"
MANIFEST_SCHEMA = "hcga-prompt-sets@1"

LEGACY_PROMPT_SET_ID = "homework-v1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Contract files every prompt set's `_general/` tree must contain: the 11
# markdown phase contracts the current single flow (`flows.flow_for`) uses,
# the Pass-1 structured-authoring prompts, and the teacher-deck fidelity
# contract. Kept in sync by hand with `prompts/_general` (this PR does not
# introduce dynamic phase-list discovery -- see task-1 brief Step 3).
REQUIRED_PHASE_FILES: tuple[str, ...] = (
    "case-based-preview.md",
    "flashcards.md",
    "memory-check.md",
    "practice-rlc.md",
    "practice-error-detection.md",
    "practice-memory-match.md",
    "practice-tictactoe.md",
    "practice-jigsaw.md",
    "practice-sentence.md",
    "boss-arena.md",
    "reflection.md",
)
REQUIRED_STRUCTURED_FILES: tuple[str, ...] = (
    "structured/practice-rlc.md",
    "structured/practice-sentence.md",
    "structured/teacher-deck.md",
)
REQUIRED_FIDELITY_FILES: tuple[str, ...] = (
    "structured/teacher-deck.fidelity.md",
)


@dataclass(frozen=True)
class PromptSetSpec:
    id: str
    label: str
    root: Path
    description: str


class PromptSetManifestError(ValueError):
    """The manifest file is malformed, or a declared set fails validation."""


def _validate_set(entry: dict, prompts_dir: Path, seen_ids: set[str]) -> PromptSetSpec:
    set_id = entry.get("id")
    if not isinstance(set_id, str) or not _ID_RE.match(set_id):
        raise PromptSetManifestError(f"invalid prompt set id: {set_id!r}")
    if set_id in seen_ids:
        raise PromptSetManifestError(f"duplicate prompt set id: {set_id!r}")
    seen_ids.add(set_id)

    root_rel = entry.get("root")
    if not isinstance(root_rel, str) or not root_rel:
        raise PromptSetManifestError(f"prompt set {set_id!r} missing root")
    prompts_dir_resolved = prompts_dir.resolve()
    root = (prompts_dir / root_rel).resolve()
    try:
        root.relative_to(prompts_dir_resolved)
    except ValueError:
        raise PromptSetManifestError(
            f"prompt set {set_id!r} root escapes PROMPTS_DIR: {root}"
        ) from None

    general = root / "_general"
    required = (*REQUIRED_PHASE_FILES, *REQUIRED_STRUCTURED_FILES, *REQUIRED_FIDELITY_FILES)
    missing = [f for f in required if not (general / f).is_file()]
    if missing:
        raise PromptSetManifestError(
            f"prompt set {set_id!r} missing required contract files: {missing}"
        )

    label = entry.get("label")
    if not isinstance(label, str) or not label:
        raise PromptSetManifestError(f"prompt set {set_id!r} missing label")
    description = entry.get("description")
    if not isinstance(description, str) or not description:
        raise PromptSetManifestError(f"prompt set {set_id!r} missing description")

    return PromptSetSpec(id=set_id, label=label, root=root, description=description)


def load_manifest(manifest_path: Path) -> tuple[PromptSetSpec, ...]:
    """Parse + validate a manifest file. Pure (no caching) -- lets tests point
    it at a throwaway manifest without touching the process-wide registry."""
    if not manifest_path.is_file():
        raise PromptSetManifestError(f"manifest not found: {manifest_path}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise PromptSetManifestError(f"unsupported manifest schema: {raw.get('schema')!r}")
    entries = raw.get("sets")
    if not isinstance(entries, list) or not entries:
        raise PromptSetManifestError("manifest declares no prompt sets")

    prompts_dir = manifest_path.parent
    seen_ids: set[str] = set()
    specs = tuple(_validate_set(e, prompts_dir, seen_ids) for e in entries)

    default_id = raw.get("default")
    if default_id not in seen_ids:
        raise PromptSetManifestError(
            f"manifest default {default_id!r} is not a declared set id"
        )

    return specs


_registry_cache: Optional[tuple[PromptSetSpec, ...]] = None


def list_prompt_sets() -> tuple[PromptSetSpec, ...]:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = load_manifest(MANIFEST_PATH)
    return _registry_cache


def get_prompt_set(prompt_set_id: str) -> PromptSetSpec:
    by_id = {s.id: s for s in list_prompt_sets()}
    try:
        return by_id[prompt_set_id]
    except KeyError:
        raise KeyError(f"unknown prompt set: {prompt_set_id!r}") from None


def _reset_cache_for_tests() -> None:
    """Test-only: force the next `list_prompt_sets()` call to reload from disk
    (or a monkeypatched `MANIFEST_PATH`) instead of serving the cached tuple."""
    global _registry_cache
    _registry_cache = None
