# app/services/phase_validator.py
"""Deterministic, warn-only validator for per-phase markdown.

Pure functions, no LLM, no I/O. Effort A ships the framework + a starter set
of common rules (non-empty body, a top-level heading, well-formed visuals).
Effort B authors each phase's full rule list in RULES alongside its prompt
rewrite. Warnings never block generation — they are recorded on the phase row
and surfaced in the operator console.
"""

from __future__ import annotations

import re
from typing import Callable

# A rule takes the phase markdown and returns a warning string, or None.
Rule = Callable[[str], "str | None"]

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
_PLACEHOLDER_TARGET = "placeholder"


def _non_empty(md: str) -> str | None:
    return "empty output" if not md.strip() else None


def _has_top_heading(md: str) -> str | None:
    for line in md.splitlines():
        if line.lstrip().startswith("# "):
            return None
    return "missing top-level heading (`# `)"


def _visuals_resolve(md: str) -> str | None:
    """A markdown image must be an inline http(s) URL or the `placeholder`
    sentinel (raster the model deliberately did not generate). Anything else
    is a real broken link."""
    for target in _IMAGE_RE.findall(md):
        t = target.strip()
        if t == _PLACEHOLDER_TARGET:
            continue
        if t.startswith(("http://", "https://")):
            continue
        return f"non-resolving image target: {target!r} (use an http(s) URL or the `placeholder` sentinel)"
    return None


# Common rules run for every phase. Empty body short-circuits the rest.
_COMMON: list[Rule] = [_has_top_heading, _visuals_resolve]

# Per-phase extra rules — populated in Effort B (e.g. CBP checkpoint/learning-block counts).
RULES: dict[str, list[Rule]] = {}


def validate(phase_name: str, md: str, *, subject: str = "") -> list[str]:
    empty = _non_empty(md)
    if empty:
        return [empty]
    warnings: list[str] = []
    for rule in (*_COMMON, *RULES.get(phase_name, [])):
        w = rule(md)
        if w:
            warnings.append(w)
    return warnings
