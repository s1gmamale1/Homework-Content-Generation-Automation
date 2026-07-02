"""Deterministic, no-LLM content lint (ROADMAP R21.3 + R21.4, cluster CQ-B).

WARN-ONLY: every finding is advisory. Callers fold `findings_to_warnings(...)`
into `phase_outputs.validation_warnings` (the same channel the LLM judge uses).
Never gates a regen, never fails a job. Pure functions — no I/O, no model calls.

Known limitations (warn-only v1 — deliberately conservative, prefers under- to
over-flagging so it never false-positives a good packet):
- `errdet_reveal_mismatch` only fires when BOTH the body marker and the Reveal
  carry a numeric block id (`Blok N`). Unnumbered-marker outputs (e.g. the
  `(This is the broken block)` + `**Xato blok:**` style) get no mismatch check —
  silence here is "not enough signal to prove a mismatch", NOT "verified consistent".
- The misconception provenance check matches `source`/`inferred` anywhere in the
  card body, so an incidental "source" can mask a genuinely missing tag (false
  negative). Acceptable for v1; tighten to a trailing/parenthesised tag form only
  once the emitted format is pinned.
- Semantic answer-key correctness is out of scope by design (no-LLM) — that is
  CQ-C (R21.2). This module only enforces mechanical/format contracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LintFinding", "lint_phase", "findings_to_warnings"]

_MAX_FINDINGS = 12  # cap per phase — a wall of warnings helps nobody


@dataclass(frozen=True)
class LintFinding:
    code: str        # stable machine tag, e.g. "mixed_script"
    message: str     # human-readable, includes the offending snippet


# --- language checks ---------------------------------------------------------

_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[^\s`*_(){}\[\]<>.,:;!?\"'|]+")

# Structural / meta template tokens that are never legitimate student content in
# ANY subject (kept deliberately narrow to avoid L2/English false-positives).
_ENGLISH_TEMPLATE = [
    re.compile(r"(?m)^\s*#{0,6}\s*\**Mode:\s*", re.IGNORECASE),          # "Mode: Hard" label/heading
    re.compile(r"\bNeeds Retry\b", re.IGNORECASE),
    re.compile(r"\bred herring\b", re.IGNORECASE),
    re.compile(r"this is a direct content generation task", re.IGNORECASE),
    re.compile(r"the brainstorming skill", re.IGNORECASE),
]
_CALQUES = [re.compile(r"\bqizil seld\b", re.IGNORECASE)]


def _lint_language(output_md: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    seen_mixed: set[str] = set()
    for w in _WORD.findall(output_md):
        if _LATIN.search(w) and _CYRILLIC.search(w) and w not in seen_mixed:
            seen_mixed.add(w)
            out.append(LintFinding("mixed_script", f"mixed Latin+Cyrillic in one word: {w!r}"))
    for rx in _ENGLISH_TEMPLATE:
        m = rx.search(output_md)
        if m:
            out.append(LintFinding("english_template", f"English template token: {m.group(0).strip()!r}"))
    for rx in _CALQUES:
        m = rx.search(output_md)
        if m:
            out.append(LintFinding("calque", f"calque phrase: {m.group(0)!r}"))
    return out


# --- dispatcher --------------------------------------------------------------

def lint_phase(phase_name: str, output_md: str, *, subject: str, output_language: str) -> list[LintFinding]:
    """Return advisory findings for one phase output. Never raises on bad input."""
    if phase_name == "extract" or not (output_md or "").strip():
        return []
    findings = _lint_language(output_md)
    return findings[:_MAX_FINDINGS]


def findings_to_warnings(findings: list[LintFinding]) -> list[str]:
    return [f"lint:{f.code}: {f.message}" for f in findings]
