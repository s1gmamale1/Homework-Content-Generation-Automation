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
- The english_template check is skipped entirely for en-medium output
  (`output_language == "en"`) — an English packet legitimately contains English,
  so structural leak tokens ("Mode:", "Needs Retry") also go unlinted there;
  en-medium template leaks rely on the judge alone.
- Error-detection marker vocab now recognizes the audited variants beyond
  `N-blok`: label blocks (`N-yorliq`), the `(BU BLOK XATO)` / parenthesised
  `(Broken)` markers, and the `oshkor` reveal header. A narrow `ru_uzbek_leak`
  guard flags Uzbek template tokens ("Hali emas", "Kuchli/Zaif tomonlar") that
  survive into RU-medium output (regression guard for the round-2 localization).
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
# Split on `-` and `/` too, so hyphen/slash-joined bi-script compounds that are
# LEGITIMATE in Russian STEM text ("pH-баланс", "IT-технологии", "Fe/Cu-сplav")
# resolve to two mono-script tokens instead of one false "mixed" word. A real
# splice ("hisoblaniб", "atamа") carries no delimiter, so it stays one token.
_WORD = re.compile(r"[^\s`*_(){}\[\]<>.,:;!?\"'|/\-]+")

# Structural / meta template tokens that are never legitimate student content in
# ANY subject (kept deliberately narrow to avoid L2/English false-positives).
# `Mode:` is pinned to the difficulty-scaffolding form so a statistics answer
# like "Mode: 7" (the statistical mode) never trips it.
_ENGLISH_TEMPLATE = [
    re.compile(r"(?mi)^\s*#{0,6}\s*\**Mode:\s*(hard|easy|medium|normal|difficult)\b"),
    re.compile(r"\bNeeds Retry\b", re.IGNORECASE),
    re.compile(r"\bred herring\b", re.IGNORECASE),
    re.compile(r"this is a direct content generation task", re.IGNORECASE),
    re.compile(r"the brainstorming skill", re.IGNORECASE),
]
_CALQUES = [re.compile(r"\bqizil seld\b", re.IGNORECASE)]

# Uzbek template artifacts that must never survive into RU-medium student text
# (regression guard for the round-2 localization fix; narrow to avoid FPs).
_RU_UZBEK_LEAK = [
    re.compile(r"\bHali emas\b", re.IGNORECASE),
    re.compile(r"\bKuchli tomonlar\b", re.IGNORECASE),
    re.compile(r"\bZaif tomonlar\b", re.IGNORECASE),
]


def _lint_ru_leak(output_md: str, output_language: str) -> list[LintFinding]:
    if (output_language or "").lower() != "ru":
        return []
    out: list[LintFinding] = []
    seen: set[str] = set()
    for rx in _RU_UZBEK_LEAK:
        m = rx.search(output_md)
        if m and m.group(0).lower() not in seen:
            seen.add(m.group(0).lower())
            out.append(LintFinding("ru_uzbek_leak", f"Uzbek template token in RU output: {m.group(0)!r}"))
    return out


# Fixed English contract headers the extract prompt emits (stable across content
# languages). Map header-variant -> canonical key by ANY-substring needle match.
# Lenient: ##/### any level, case-insensitive, '&'/'-'/whitespace tolerated.
# ORDERED specific-first so a broad needle can't shadow a more specific section
# (checked top-to-bottom, first hit wins). Needles are chosen so each is a
# substring of its real headers: "key fact" IS a substring of "key facts".
_CONTRACT_SECTION_NEEDLES = [
    ("worked_example_types", ("worked", "example")),  # "Worked-example types"
    ("rules_theorems", ("rule", "theorem")),          # "Rules & theorems"
    ("key_facts", ("key fact",)),                     # "Key facts"
    ("concepts", ("concept", "term")),                # "Concepts & terms"
    ("formulas", ("formula",)),                       # "Formulas"
]
_HEADER_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*(?P<h>[^\n#].*?)[ \t]*$")
_BULLET_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+(?P<item>\S.*?)[ \t]*$")


def _canonical_section(header: str) -> "str | None":
    h = header.strip().lower()
    for key, needles in _CONTRACT_SECTION_NEEDLES:
        if any(n in h for n in needles):
            return key
    return None


def parse_extract_contract(md: str) -> "dict[str, list[str]]":
    """Parse the enumerated extract contract into {canonical_section: [items]}.
    Only recognized sections with >=1 bullet appear. Lenient on header level/case."""
    text = md or ""
    out: "dict[str, list[str]]" = {}
    headers = list(_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        key = _canonical_section(m.group("h"))
        if not key:
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        items = [b.group("item").strip() for b in _BULLET_RE.finditer(text[m.end():end])]
        items = [it for it in items if it]
        if items:
            out.setdefault(key, []).extend(items)
    return out


def contract_has_items(md: str) -> bool:
    """True iff the text parses to >=1 recognized contract section with an item."""
    return bool(parse_extract_contract(md))

def _lint_language(output_md: str, output_language: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    seen_mixed: set[str] = set()
    for w in _WORD.findall(output_md):
        if _LATIN.search(w) and _CYRILLIC.search(w) and w not in seen_mixed:
            seen_mixed.add(w)
            out.append(LintFinding("mixed_script", f"mixed Latin+Cyrillic in one word: {w!r}"))
    # English template/calque tokens are content — not artifacts — on an English
    # (L2) lesson, where the deliverable IS English text and idioms. Skip them there.
    if (output_language or "").lower() != "en":
        for rx in _ENGLISH_TEMPLATE:
            m = rx.search(output_md)
            if m:
                out.append(LintFinding("english_template", f"English template token: {m.group(0).strip()!r}"))
        for rx in _CALQUES:
            m = rx.search(output_md)
            if m:
                out.append(LintFinding("calque", f"calque phrase: {m.group(0)!r}"))
    return out


# --- misconception provenance tag check (flashcards only) --------------------

_MISCONCEPTION_LINE = re.compile(r"(?im)^\s*\**\s*misconception\s*:\**\s*(?P<body>.+)$")
_PROVENANCE = re.compile(r"\b(source|inferred)\b", re.IGNORECASE)


def _lint_misconception_tags(output_md: str) -> list[LintFinding]:
    # Aggregate into ONE finding per phase (with a count) rather than one per
    # card — the audited prompt-contract gap (flashcards.md:32,95) is systemic, so
    # per-card firing would be noise. This is a true positive (the tag really is
    # absent), not a false one; it stays visible for R20/human-review.
    untagged = sum(
        1 for m in _MISCONCEPTION_LINE.finditer(output_md)
        if not _PROVENANCE.search(m.group("body"))
    )
    if untagged:
        return [LintFinding(
            "misconception_untagged",
            f"{untagged} misconception card(s) missing a source/inferred provenance tag",
        )]
    return []


# --- error-detection format check (EXACTLY-ONE-broken-block) -----------------

_APOS = r"['ʻʼ‘’]"
_NOT = rf"noto{_APOS}?g{_APOS}?ri"
# Block noun: Uzbek "blok"/"block" OR "yorliq" (label) — the audit found blocks
# named "N-yorliq" instead of "N-blok".
_BLK = r"(?:blo(?:k|ck)|yorli(?:q|g'|gʻ))"
# A block id in EITHER Uzbek order: cardinal "Blok 4" or ordinal "4-blok".
_BID = rf"(?:{_BLK}\s*(\d+)|(\d+)\s*-\s*{_BLK})"
# Every broken-block marker. The noun/ordinal-with-NOT forms REQUIRE a digit (so
# digitless prose never matches); each captures the id.
_MARKER = re.compile(
    rf"(?i)"
    rf"{_BLK}\s*(?P<id_pre>\d+)\s+{_NOT}"                    # "Blok 4 noto'g'ri"
    rf"|(?P<id_ord>\d+)\s*-\s*{_BLK}\s+{_NOT}"               # "4-blok noto'g'ri"
    rf"|{_NOT}\s+{_BLK}\s*(?P<id_post>\d+)?"                 # "noto'g'ri blok[ 4]"
    rf"|{_NOT}\s+(?P<id_post_ord>\d+)\s*-\s*{_BLK}"          # "noto'g'ri 4-blok"
    rf"|{_BLK}\s*(?P<id_xato>\d+)?\s+xato"                   # "blok[ 4] xato" / "BU BLOK XATO"
    rf"|(?P<id_xato_ord>\d+)\s*-\s*{_BLK}\s+xato"            # "4-blok xato"
    rf"|xato\s+{_BLK}\s*(?P<id_xatop>\d+)?"                  # "xato blok[ 4]"
    rf"|xato\s+(?P<id_xatop_ord>\d+)\s*-\s*{_BLK}"           # "xato 4-blok"
    rf"|(?P<eng>this is the broken block|broken block)"      # English markers, no id
    rf"|(?P<eng2>\(\s*broken\s*\))"                          # parenthesised "(Broken)"
)
_REVEAL_HDR = re.compile(r"(?im)^[ \t]*#{1,6}[ \t]*(reveal|ochish|oshkor)\b")
_BLOCK_ID = re.compile(rf"(?i)\b{_BID}")


def _line_around(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)]


def _lint_error_detection(output_md: str) -> list[LintFinding]:
    rev = _REVEAL_HDR.search(output_md)
    reveal_off = rev.start() if rev else len(output_md)

    body_ids: set[str] = set()
    body_marker_count = 0
    reveal_id: str | None = None

    for m in _MARKER.finditer(output_md):
        gd = m.groupdict()
        mid = (gd.get("id_pre") or gd.get("id_ord") or gd.get("id_post")
               or gd.get("id_post_ord") or gd.get("id_xato") or gd.get("id_xato_ord")
               or gd.get("id_xatop") or gd.get("id_xatop_ord"))
        # Line-scan recovery is ONLY for the id-less English markers (whose id may
        # sit on the same line). The noto'g'ri/xato prose forms must NOT borrow a
        # nearby id — that produced spurious multiple/mismatch findings.
        if mid is None and (gd.get("eng") or gd.get("eng2")):
            bm = _BLOCK_ID.search(_line_around(output_md, m.start()))
            mid = (bm.group(1) or bm.group(2)) if bm else None
        if m.start() >= reveal_off:
            if reveal_id is None and mid is not None:
                reveal_id = mid
            continue
        body_marker_count += 1
        if mid is not None:
            body_ids.add(mid)

    if reveal_id is None and rev is not None:  # first block id after the reveal header
        bm = _BLOCK_ID.search(output_md, reveal_off)
        reveal_id = (bm.group(1) or bm.group(2)) if bm else None

    out: list[LintFinding] = []
    if body_marker_count == 0 and reveal_id is None:
        out.append(LintFinding("errdet_no_broken_marker",
                               "no broken-block marker found (prompt requires exactly one)"))
    elif len(body_ids) >= 2:
        out.append(LintFinding("errdet_multiple_broken",
                               f"multiple broken blocks marked: blocks {sorted(body_ids)}"))
    elif body_ids and reveal_id and reveal_id not in body_ids:
        out.append(LintFinding("errdet_reveal_mismatch",
                               f"reveal names block {reveal_id} but body marks {sorted(body_ids)}"))
    return out


# --- dispatcher --------------------------------------------------------------

def lint_phase(phase_name: str, output_md: str, *, subject: str, output_language: str) -> list[LintFinding]:
    """Return advisory findings for one phase output. Never raises on bad input."""
    if phase_name == "extract" or not (output_md or "").strip():
        return []
    findings = _lint_language(output_md, output_language)
    findings += _lint_ru_leak(output_md, output_language)
    if phase_name == "flashcards":
        findings += _lint_misconception_tags(output_md)
    if phase_name == "practice-error-detection":
        findings += _lint_error_detection(output_md)
    return findings[:_MAX_FINDINGS]


def findings_to_warnings(findings: list[LintFinding]) -> list[str]:
    return [f"lint:{f.code}: {f.message}" for f in findings]
