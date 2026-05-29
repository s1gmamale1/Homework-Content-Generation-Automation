"""Flow v2 Infra prompt registry + resolver (Phase 2 D).

The Infra prompts in ``docs/Infra_prompts/`` are the authoritative v2 prompt
source. This module:

- pins them (relative path + version + content sha256) — `build_manifest`;
- maps each homework phase / practice game to its Infra prompt, per family —
  `resolve`;
- reports coverage for a division plan — `resolve_plan_coverage` (consumed by
  the Phase 2 E.2 coverage gate).

Phases that have no Infra prompt fall back to the repo's builtin per-subject
prompts (`app/services/prompts.py`); the legacy loader stays (D.7) so the
existing app keeps generating. An enabled item that resolves to neither is
reported ``missing``.

CBP NOTE: only the CBP generation standard **v1.0**
(`nets_case_based_preview_generation_standard_v1.md`) ships. CBP **v1.1** (with
the decision_process_explanation slot) is not yet authored — the registry pins
what exists and flags v1.1 as pending in `standards`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from app.services import prompts
from app.services.flow_division_mapper import family_for_subject

INFRA_ROOT = Path(__file__).resolve().parent.parent.parent / "docs" / "Infra_prompts"

# ── Case-Based Preview (preview phases). No humanities-specific CBP prompt
#    ships, so humanities falls back to the family-agnostic standard. ──
_CBP_BY_FAMILY: dict[str, str] = {
    "math_family": "Case-Based Preview/nets_cbp_prompt_math_family.md",
    "sciences": "Case-Based Preview/nets_cbp_prompt_sciences.md",
    "languages": "Case-Based Preview/nets_cbp_prompt_languages.md",
}
_CBP_STANDARD = "Case-Based Preview/nets_case_based_preview_generation_standard_v1.md"

_FLASHCARDS_BY_FAMILY: dict[str, str] = {
    "math_family": "Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_math_family.md",
    "sciences": "Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_sciences.md",
    "languages": "Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_languages.md",
    "humanities": "Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_humanities.md",
}

# Phase -> Infra prompt (family-agnostic) where one exists.
_PHASE_INFRA: dict[str, str] = {
    "real-life": "Gamified Practices/Real Life Challenge/Real_Life_Challenge_Specification.md",
    "final-challenge": "Gamified Practices/Boss Arena/Boss_Arena_Specification.md",
}

# Practice game type -> Infra prompt.
_GAME_INFRA: dict[str, str] = {
    "memory_match": "Gamified Practices/Memory Matching/MemoryMatching.md",
    "sentence_fill": "Gamified Practices/Sentence Filling/SentenceFilling.md",
    "adaptive_quiz": "Flashcards/Quzilet Learning/Multiple Choice/Multiple_Choice_Specification.md",
    "tile_match": "Gamified Practices/Jigsaw Matching/JigsawMatching.md",
}

# Pinned standards (D.4). cbp_standard is v1.0; v1.1 is pending authoring.
_STANDARDS: dict[str, str] = {
    "cbp_standard": _CBP_STANDARD,
    "flow_v2": "Flow/New_Flow.md",
    "uzbek_review": "Uzbek Specification/NETS_Uzbek_Language_Foundation_Review.md",
}


@dataclass
class RegistryEntry:
    item: str  # the enabled item (phase name or 'game:<type>')
    kind: str  # "infra" | "builtin" | "missing"
    key: str = ""
    path: Optional[str] = None  # relative to repo root
    version: str = ""
    sha256: str = ""
    exists: bool = False


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def _version_from_name(name: str) -> str:
    m = re.search(r"_v(\d+(?:_\d+)?)", name)
    return m.group(1).replace("_", ".") if m else "1.0"


def _infra_entry(item: str, rel: str, key: str) -> RegistryEntry:
    p = INFRA_ROOT / rel
    return RegistryEntry(
        item=item,
        kind="infra" if p.is_file() else "missing",
        key=key,
        path=f"docs/Infra_prompts/{rel}",
        version=_version_from_name(Path(rel).name),
        sha256=_sha256(p),
        exists=p.is_file(),
    )


def _builtin_entry(item: str, subject: str, phase_name: Optional[str]) -> RegistryEntry:
    if phase_name is None:
        return RegistryEntry(item=item, kind="missing", key="")
    p = prompts.PROMPTS_DIR / subject / f"{phase_name}.md"
    rel = f"prompts/{subject}/{phase_name}.md"
    if p.is_file():
        return RegistryEntry(
            item=item,
            kind="builtin",
            key=f"builtin:{subject}/{phase_name}",
            path=rel,
            version="builtin",
            sha256=_sha256(p),
            exists=True,
        )
    return RegistryEntry(
        item=item, kind="missing", key=f"builtin:{subject}/{phase_name}", path=rel
    )


def resolve(item: str, subject: str) -> RegistryEntry:
    """Resolve one enabled item (phase name or 'game:<type>') to its prompt.

    Infra prompt first (authoritative); builtin per-subject prompt as fallback;
    ``missing`` if neither exists.
    """
    family = family_for_subject(subject)

    if item.startswith("game:"):
        gtype = item.split(":", 1)[1]
        rel = _GAME_INFRA.get(gtype)
        if rel:
            return _infra_entry(item, rel, f"game:{gtype}")
        return _builtin_entry(item, subject, None)

    base = "preview" if item.startswith("preview") else item

    if base == "preview":
        rel = _CBP_BY_FAMILY.get(family, _CBP_STANDARD)
        return _infra_entry(item, rel, f"cbp:{family}")
    if base == "flashcards":
        rel = _FLASHCARDS_BY_FAMILY.get(family, _FLASHCARDS_BY_FAMILY["humanities"])
        return _infra_entry(item, rel, f"flashcards:{family}")
    if base in _PHASE_INFRA:
        return _infra_entry(item, _PHASE_INFRA[base], f"phase:{base}")

    # No Infra prompt for this phase — fall back to the builtin per-subject one
    # (game-breaks, memory-sprint, consolidation, reflection, reading, ...).
    return _builtin_entry(item, subject, item)


def resolve_plan_coverage(plan) -> dict:
    """Resolve every enabled item in a FlowDivisionPlan. Returns a dict suitable
    for storing in the flow manifest and for the Phase 2 E.2 coverage gate."""
    entries = [resolve(it, plan.subject) for it in plan.enabled_items()]
    missing = [e.item for e in entries if e.kind == "missing"]
    return {
        "subject": plan.subject,
        "family": plan.family,
        "items": [asdict(e) for e in entries],
        "infra_count": sum(1 for e in entries if e.kind == "infra"),
        "builtin_count": sum(1 for e in entries if e.kind == "builtin"),
        "missing": missing,
        "covered": not missing,
    }


def _file_meta(rel: str) -> dict:
    p = INFRA_ROOT / rel
    return {
        "path": f"docs/Infra_prompts/{rel}",
        "version": _version_from_name(Path(rel).name),
        "sha256": _sha256(p),
        "exists": p.is_file(),
        "size": p.stat().st_size if p.is_file() else 0,
    }


def build_manifest() -> dict:
    """Pin every registered Infra prompt + standard (path/version/hash/size).

    This is the `infra_prompt_registry_pinned/manifest.json` content
    (written by scripts/sync_infra_prompt_registry.py)."""
    entries: dict[str, dict] = {"cbp:standard": _file_meta(_CBP_STANDARD)}
    for fam, rel in _CBP_BY_FAMILY.items():
        entries[f"cbp:{fam}"] = _file_meta(rel)
    for fam, rel in _FLASHCARDS_BY_FAMILY.items():
        entries[f"flashcards:{fam}"] = _file_meta(rel)
    for name, rel in _PHASE_INFRA.items():
        entries[f"phase:{name}"] = _file_meta(rel)
    for name, rel in _GAME_INFRA.items():
        entries[f"game:{name}"] = _file_meta(rel)
    for name, rel in _STANDARDS.items():
        entries[f"standard:{name}"] = _file_meta(rel)

    return {
        "infra_root": "docs/Infra_prompts",
        "entry_count": len(entries),
        "entries": entries,
        "pending": {
            # CBP v1.1 (with decision_process_explanation slot) not yet authored.
            "cbp_v1_1": "not_present — only v1.0 ships; author for DPE slot 7",
        },
    }
