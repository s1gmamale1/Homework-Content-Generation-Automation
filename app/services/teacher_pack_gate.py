"""Deterministic coverage/citation gate for the teacher-pack phase.

The teacher pack's QA-WHERE comments carry, per misconception slide, the
homework distractors that slide covers. Two properties are machine-checkable
and have proven too stochastic to leave to the prompt + judge alone
(6 of 8 canary runs failed one of them while every other rule class held):

1. COVERAGE — every distractor the packet declares is cited somewhere.
2. NO BOGUS — no cited option letter/value is actually its item's KEY.

`check()` is pure (strings in, verdict out) and NEVER raises on malformed
content: a phase that cannot be parsed contributes an empty declared set
(fail-open per phase, recorded in `notes`) because the gate must never turn
generation variance into a job failure. The pipeline uses the verdict to
regenerate the pack with the exact miss-list injected, bounded by
``settings.teacher_pack_gate_retries``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LETTERS = ("A", "B", "C", "D")

# Loose phase-name → canonical key mapping for QA-WHERE segments. The deck is
# told to spell phase names out, so match on words, not acronyms — but accept
# the acronyms too (comments are QA vocabulary).
_PHASE_HINTS = (
    ("memory", "memory-check"),
    ("preview", "case-based-preview"),
    ("case", "case-based-preview"),
    ("challenge", "practice-rlc"),
    ("rlc", "practice-rlc"),
    ("detection", "practice-error-detection"),
    ("error", "practice-error-detection"),
    ("sentence", "practice-sentence"),
    ("flashcard", "flashcards"),
    ("vocabulary", "vocabulary"),
)


def _phase_of(segment: str) -> str | None:
    s = segment.lower()
    for hint, canon in _PHASE_HINTS:
        if hint in s:
            return canon
    return None


def _norm_value(v: str) -> str:
    """Normalize a cited/declared VALUE (sentence choices, concept chips)."""
    v = re.sub(r"[*_`\"'()‘’“”]", "", v)
    return re.sub(r"\s+", " ", v).strip().lower()


@dataclass
class GateResult:
    passed: bool
    missing: list[str] = field(default_factory=list)   # declared, never cited
    bogus: list[str] = field(default_factory=list)     # cited, but it is the key
    banned: list[str] = field(default_factory=list)    # analyst words in visible text
    declared_count: int = 0
    cited_count: int = 0
    notes: list[str] = field(default_factory=list)     # parse gaps (fail-open)

    @property
    def feedback(self) -> str:
        lines = [
            "",
            "",
            "## COVERAGE GATE FAILED — fix the QA-WHERE citations and re-emit the FULL deck",
            "",
            "A deterministic check compared your QA-WHERE comments against the",
            "packet's declared distractors. Correct everything below, change",
            "nothing else that already satisfies the contract, and re-emit the",
            "ENTIRE deck (all slides + all QA comments).",
        ]
        if self.missing:
            lines.append("")
            lines.append("MISSING — declared distractors with no QA-WHERE citation; add each to the matching misconception slide's comment:")
            lines.extend(f"- {m}" for m in self.missing)
        if self.bogus:
            lines.append("")
            lines.append("BOGUS — you cited these as wrong, but each is its item's CORRECT answer; remove them from the comments:")
            lines.extend(f"- {b}" for b in self.bogus)
        if self.banned:
            lines.append("")
            lines.append("BANNED WORDS — analyst vocabulary in visible slide text; rewrite each line in the homework's own plain words (the owner's rule: we teach students, not linguists):")
            lines.extend(f"- {b}" for b in self.banned)
        return "\n".join(lines)


# ── declared-distractor extraction ──────────────────────────────────────────

def _declared_letter_items(md: str, *, block_re: str, key_re: str,
                           item_label: str) -> dict[str, tuple[set[str], str]]:
    """{item_number: (wrong_letters, key)} for one lettered-options phase."""
    out: dict[str, tuple[set[str], str]] = {}
    for m in re.finditer(block_re, md, re.S):
        num, body = m.group(1), m.group(2)
        key_m = re.search(key_re, body)
        opts = set(re.findall(r"(?m)^\s*-?\s*([A-D])\)\s", body))
        if not key_m or not opts:
            continue
        key = key_m.group(1)
        out[num] = (opts - {key}, key)
    return out


def _declared_memory_check(md: str) -> dict[str, tuple[set[str], str]]:
    return _declared_letter_items(
        md,
        block_re=r"###[^\n]*?card[ _](\d+)\n(.*?)(?=\n### |\Z)",
        key_re=r"To'g'ri javob:\*\*\s*([A-D])",
        item_label="card",
    )


def _declared_cbp(md: str) -> dict[str, tuple[set[str], str]]:
    return _declared_letter_items(
        md,
        block_re=r"##[^\n]*?Checkpoint (\d)[^\n]*\n(.*?)(?=\n## |\Z)",
        key_re=r"To'g'ri javob:\*\*\s*([A-D])",
        item_label="checkpoint",
    )


# Correct-option marker: english packets write "(Correct)", uz packets
# "(To'g'ri)" with any apostrophe variant. Match both everywhere.
_CORRECT_MARK_RE = re.compile(r"\((?:Correct|To[’'‘ʻ`´]g[’'‘ʻ`´]ri)\)")


def _declared_rlc(md: str) -> tuple[dict[str, tuple[set[str], str]], set[str]]:
    """Lettered steps + the concept-select step's wrong chip values."""
    steps: dict[str, tuple[set[str], str]] = {}
    chips: set[str] = set()
    for m in re.finditer(r"### Step (\d)[^\n]*\n(.*?)(?=\n### |\n## |\Z)", md, re.S):
        num, body = m.group(1), m.group(2)
        letters = re.findall(r"(?m)^\s*-?\s*([A-D])\)", body)
        if letters:
            correct = None
            for lm in re.finditer(r"(?m)^\s*-?\s*([A-D])\)([^\n]*)$", body):
                if _CORRECT_MARK_RE.search(lm.group(2)):
                    correct = lm.group(1)
                    break
            if correct:
                steps[num] = (set(letters) - {correct}, correct)
            continue
        # concept_select: plain "- chip" lines, one correct-marked
        chip_lines = re.findall(r"(?m)^- ([^\n]+)$", body)
        if chip_lines and any(_CORRECT_MARK_RE.search(c) for c in chip_lines):
            for c in chip_lines:
                if not _CORRECT_MARK_RE.search(c):
                    chips.add(_norm_value(c))
    return steps, chips


def _declared_fill_keys(md: str) -> dict[str, set[str]]:
    """{card_number: normalized expected+alternate answers} for fill_blanks.

    Fill-blanks declare no distractors (nothing to cover), but citing their
    KEY as if it were a wrong option is a bogus citation — track the keys."""
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"###[^\n]*?card[ _](\d+)\n(.*?)(?=\n### |\Z)", md, re.S):
        num, body = m.group(1), m.group(2)
        exp = re.search(r"Kutilayotgan javob:\*\*\s*([^\n]+)", body)
        if not exp:
            continue
        keys = {_norm_value(exp.group(1))}
        alt = re.search(r"Muqobil javoblar:\*\*\s*([^\n]+)", body)
        if alt:
            keys.update(_norm_value(a) for a in alt.group(1).split(","))
        out[num] = {k for k in keys if k}
    return out


def _declared_sentence(md: str) -> tuple[set[str], str | None]:
    sec = re.search(r"## Choices\n(.*?)(?=\n## |\Z)", md, re.S)
    if not sec:
        return set(), None
    wrongs: set[str] = set()
    key = None
    for line in re.findall(r"(?m)^- ([^\n]+)$", sec.group(1)):
        if _CORRECT_MARK_RE.search(line):
            key = _norm_value(_CORRECT_MARK_RE.sub("", line))
        else:
            wrongs.add(_norm_value(line))
    return wrongs, key


def _declared_ed_block(md: str) -> str | None:
    m = re.search(r"Blo[ck]+ (\d+)\D{0,30}(?:broken|buzilgan|is broken)", md, re.I)
    if not m:
        m = re.search(r"(?:broken|buzilgan)[^\n]{0,30}Blo[ck]+ (\d+)", md, re.I)
    return m.group(1) if m else None


# ── cited extraction ────────────────────────────────────────────────────────

def _cited(deck_md: str):
    """(letter_cites, value_tokens) from every QA-WHERE comment.

    letter_cites: {(phase, item_number): set(letters)}
    value_tokens: set of normalized non-letter tokens (sentence values, chips,
                  block references like ('practice-error-detection', '4')).
    """
    letter_cites: dict[tuple[str, str], set[str]] = {}
    value_tokens: set[str] = set()
    block_cites: set[tuple[str, str]] = set()
    item_values: dict[tuple[str, str], set[str]] = {}
    for cm in re.finditer(r"<!--\s*QA-WHERE:(.*?)-->", deck_md, re.S):
        for seg in cm.group(1).split(";"):
            phase = _phase_of(seg)
            if phase is None:
                continue
            item_m = re.search(r"(?:card|checkpoint|step|block)\s*_?(\d+)", seg, re.I)
            item = item_m.group(1) if item_m else None
            tail = seg.split(":", 1)[1] if ":" in seg else seg
            letters = set(re.findall(r"\b([A-D])\b", tail))
            if phase == "practice-error-detection" and item:
                block_cites.add((phase, item))
                continue
            if item and letters:
                letter_cites.setdefault((phase, item), set()).update(letters)
            # value tokens (sentence choices, chips) — comma-separated tail
            for tok in tail.split(","):
                t = _norm_value(tok)
                if t and t not in _LETTERS and len(t) > 1:
                    value_tokens.add(t)
                    if item:
                        item_values.setdefault((phase, item), set()).add(t)
    return letter_cites, value_tokens, block_cites, item_values


def _value_covered(declared: str, cited_values: set[str]) -> bool:
    d = _norm_value(declared)
    if not d:
        return True
    head = " ".join(d.split()[:3])
    return any(d == c or c == head or c.startswith(head) or head.startswith(c)
               for c in cited_values if c)


# ── banned-lexeme scan ──────────────────────────────────────────────────────

# Owner rule (POLISH-round2 §1): analyst vocabulary never appears in visible
# deck text. "bank" is banned in TITLES only (it can be legitimate content).
_ANALYST_RE = re.compile(
    r"(?i)\b(modal auxiliar\w*|auxiliar\w*|interrogativ\w*|bare infinitiv\w*|"
    r"infinitiv\w*|evidential\w*|clauses?|indicators?|syntax|procedures?|"
    r"formulat\w*|classificat\w*)\b"
)
_TITLE_BANK_RE = re.compile(r"(?im)^## \d+\.[^\n]*\bbank\b[^\n]*$")


def _visible_text(deck_md: str) -> str:
    vis = re.sub(r"<!--.*?-->", "", deck_md, flags=re.S)
    return re.sub(r"```.*?```", "", vis, flags=re.S)


def _banned_hits(deck_md: str) -> list[str]:
    vis = _visible_text(deck_md)
    hits: list[str] = []
    seen: set[str] = set()
    for line in vis.splitlines():
        m = _ANALYST_RE.search(line)
        if m:
            key = (m.group(1).lower(), line.strip()[:60])
            if key not in seen:
                seen.add(key)
                hits.append(f"'{m.group(1)}' in: {line.strip()[:80]}")
    for m in _TITLE_BANK_RE.finditer(vis):
        hits.append(f"'bank' in a title: {m.group(0)[:80]}")
    return hits


# ── the gate ────────────────────────────────────────────────────────────────

def check(deck_md: str, prior_outputs: dict[str, str]) -> GateResult:
    r = GateResult(passed=True)
    try:
        letter_cites, value_tokens, block_cites, item_values = _cited(deck_md)
    except Exception as exc:  # noqa: BLE001 — never raise
        return GateResult(passed=True, notes=[f"cited-parse-failed: {exc!r}"])

    if not letter_cites and not value_tokens and not block_cites:
        # No QA-WHERE comments at all — that IS a failure worth one regen.
        r.passed = False
        r.missing.append("EVERY declared distractor — the deck contains no QA-WHERE comments at all")
        return r

    def _check_letter_phase(phase: str, label: str,
                            items: dict[str, tuple[set[str], str]]):
        for num, (wrongs, key) in items.items():
            cited = letter_cites.get((phase, num), set())
            r.declared_count += len(wrongs)
            r.cited_count += len(cited & wrongs)
            for w in sorted(wrongs - cited):
                r.missing.append(f"{label} {num}: option {w}")
            if key in cited:
                r.bogus.append(f"{label} {num}: option {key} is the CORRECT answer")

    try:
        _check_letter_phase("memory-check", "Memory Check card",
                            _declared_memory_check(prior_outputs.get("memory-check", "")))
        # A fill-blank has no distractors — citing its KEY as one is bogus.
        for num, keys in _declared_fill_keys(prior_outputs.get("memory-check", "")).items():
            for v in item_values.get(("memory-check", num), set()):
                if any(v == k or v.startswith(k) or k.startswith(v) for k in keys):
                    r.bogus.append(
                        f"Memory Check card {num}: '{v[:50]}' is the fill-blank's "
                        f"CORRECT expected answer"
                    )
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"memory-check parse: {exc!r}")
    try:
        _check_letter_phase("case-based-preview", "Case Preview checkpoint",
                            _declared_cbp(prior_outputs.get("case-based-preview", "")))
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"cbp parse: {exc!r}")
    try:
        steps, chips = _declared_rlc(prior_outputs.get("practice-rlc", ""))
        _check_letter_phase("practice-rlc", "Real-Life Challenge step", steps)
        for chip in sorted(chips):
            r.declared_count += 1
            if _value_covered(chip, value_tokens):
                r.cited_count += 1
            else:
                r.missing.append(f"Real-Life Challenge concept step: wrong chip '{chip[:50]}'")
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"rlc parse: {exc!r}")
    try:
        wrongs, key = _declared_sentence(prior_outputs.get("practice-sentence", ""))
        for w in sorted(wrongs):
            r.declared_count += 1
            if _value_covered(w, value_tokens):
                r.cited_count += 1
            else:
                r.missing.append(f"Sentence Practice: wrong choice '{w}'")
        if key and key in value_tokens:
            r.bogus.append(f"Sentence Practice: choice '{key}' is the CORRECT answer")
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"sentence parse: {exc!r}")
    try:
        blk = _declared_ed_block(prior_outputs.get("practice-error-detection", ""))
        if blk is not None:
            r.declared_count += 1
            if ("practice-error-detection", blk) in block_cites or \
                    _value_covered(f"block {blk}", value_tokens):
                r.cited_count += 1
            else:
                r.missing.append(f"Error Detection: broken block {blk}")
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"ed parse: {exc!r}")

    try:
        r.banned = _banned_hits(deck_md)
    except Exception as exc:  # noqa: BLE001
        r.notes.append(f"banned-scan: {exc!r}")

    if r.missing or r.bogus or r.banned:
        r.passed = False
    return r
