# TOC Classifier Subject Vocabulary (toc-classifier-subject-vocab-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De-math-calibrate the TOC lesson classifier so physics/biology/english/geografiya non-lesson rows (labs, practicals, problem-solving sessions, English reviews/back-matter) stop silently launching as lessons.

**Architecture:** Extend the pure keyword classifier (`app/services/toc_classifier.py`) with a new `practice` class + subject vocabulary + homoglyph-fold normalization; extend the hand-labeled accuracy fixture with all 6 generatable G8 non-math books (293 real rows from edu_copy); mirror the new class in the FE constants. No DB changes, no LLM calls, no migration — `entry_class` is computed at read time and self-heals.

**Tech Stack:** Python (pure module + pytest), React/TS constants only.

**Branch:** `fix/toc-classifier-subject-vocab` (worktree `../HCGA-tocvocab`), commit prefix `tocvocab:`, worklog **0130**.

---

## Approach & key decisions

- **New `practice` class** (user-locked 2026-07-08): lab/practical/problem-solving rows get their own class + FE chip so operators can deliberately re-include them (`include_classes`). Server-side `batch.py` validation follows `CLASSES` automatically; FE needs its two mirrored constants updated (`launcher.tsx:551` `ALL_ENTRY_CLASSES`, `:554` `CLASS_META`) — an unknown class renders via fallback but can never be checkbox-included, so the mirror update is load-bearing.
- **`masalalar yechish` must be whole-title-anchored, NOT substring** (verified against the shipped fixture): g8alg "Kvadrat tenglamalar yordamida masalalar yechish" and g8geo "…masalalar yechishning koordinatalar usuli" are TRUE LESSONS that contain the phrase. Physics's 11 non-lesson rows are bare whole-title «Masalalar yechish». Same reason `amaliy mashg'ulot` stays exact (g8geo lesson "Amaliy mashq va tatbiq" must not match).
- **C1 (GK2 fold-in, blocking): bare «Masalalar yechish» is reclassified lesson→practice on MATH books too, not just physics — intended, and a behavior change from today.** Real §-numbered bare rows exist in G9-Geometriya (`1bc43831` order_index 22/25/32 = §23/26/33, printed-mundarija-verified) and G11 Matematika (P1 `9bc1ad5e` #6 §15-17, #11 §33-36; P2 `bd7b2d6b` #2 §52-56). A standalone problem-solving section is practice, not a new-concept lesson; LESSON-only launches will skip them (G9-geo default launch targets ~52 rows instead of 55 — the 3 move to the opt-in `practice` class). The fixture pins these real rows with a deliberate `practice` label (targeted decision-pin subsets, Task 2) so the flip is a decision, not an accident. **Operator note for the worklog: flag this so the row-count drop isn't read as a regression.**
- **The wishlist over-counted the misses.** Ran the shipped classifier over all 293 stored rows: «…bobni takrorlash uchun test topshiriqlari» (×5), «Umumlashtiruvchi takrorlash», «Ilovalar», «…javoblari» are ALREADY caught by existing keywords. True misses: physics ×23, biology ×7, english ×12, geografiya ×2, kimyo/history 0. Keywords added are only the ones with verified misses (+ RU parity for lab/practice, unambiguous).
- **English culture/life-skills pages are keyword-undetectable in STORED rows** ("Scotland", "British TV Around the World", "ICT Literacy: …", "Social Responsibility: …", "Emotional Skills: …" — the printed TOC's "Life Skills"/"Culture" prefixes were dropped at extraction). No deterministic rule → label them `other` in the fixture and add them to the `accepted_false_inclusions` allowlist (junk→lesson direction: visible in packet list, operator deselects). The wishlist's `life skills`/`culture` keywords are dropped — they can't match anything stored.
- **Homoglyph fold** (wishlist item 2): fold Cyrillic lookalikes (а е о с р х у і ѕ ј → Latin) inside `_normalize`, applied to BOTH titles and keyword tables at import (symmetric fold keeps RU keywords matching RU titles). Note: the geografiya homoglyph claim did NOT reproduce in current stored rows (0 mixed-script titles across all 6 G8 books) — this is cheap defensive hardening against future extractions, not a live bug fix.
- **Fixture truth choices:** physics/geo/history «Kirish» = lesson (readable intro; tolerable direction). Physics «…test topshiriqlari» = `revision` (matches existing takrorlash precedence; zero behavior churn). New book keys `g8phys/g8bio/g8eng/g8geog/g8kim/g8hist` (`g8geo` is already TAKEN by geometriya).
- **Rejected:** folding practice rows into `other` (muddy chips, no selective re-include — user chose new class); substring `masalalar yechish` (false-excludes math lessons — the worst direction, gate (a)); page-span heuristics for English culture pages (overfit).
- **No acceptance smoke needed:** classifier is pure/read-time, no model calls. Proof = accuracy gate over 545 hand-labeled real rows + a read-only re-run over live edu_copy rows + `tsc`/build.

## Verification targets (proven before this plan)

Post-change classifier over the 293 stored rows must newly exclude exactly:
- **physics** (book `e020d51f`): practice #4,7,20,28,31,36,38,42,49,61,67 (Masalalar yechish), #16,21,26,41,65 (Laboratoriya ishi. …), #22,25 (Amaliy mashg'ulot. …); revision #10,33,44,55,69 («N bob yuzasidan muhim xulosalar»).
- **biology** (`68601c99`): practice #14,24,28,33,49,63 («N-laboratoriya mashg'uloti.»); other #0 («Darslikdan foydalanish qoidalari»).
- **english** (`d463c690`): revision #3,10,17 («Review N (Units …)»); other #18,19,20,21 (Extra Activities / Vocabulary List / Grammar Reference and Practice / List of Irregular Verbs). #2,6,9,13,16 stay predicted-lesson (allowlisted).
- **geografiya** (`f249da59`): practice #36,58 («Amaliy mashg'ulot», dars-numbered!).
- **kimyo/history**: no change (all lesson except history has none non-lesson).
- **C1 math flip (intended behavior change)**: G9-Geometriya (`1bc43831`) #22,25,32 (§23/26/33) and G11 Matematika P1 (`9bc1ad5e`) #6,#11 / P2 (`bd7b2d6b`) #2 — bare «Masalalar yechish» rows flip lesson→**practice** (excluded from default LESSON-only launch, re-includable via the practice checkbox). Suffix-form math lessons («…yordamida masalalar yechish» etc.) must NOT flip anywhere.

Everything already-excluded stays excluded; ZERO true-lesson rows flip (gate (a) of the accuracy test).

---

### Task 1: Classifier — `practice` class, subject vocabulary, homoglyph fold

**Files:**
- Modify: `tests/services/test_toc_classifier.py`
- Modify: `app/services/toc_classifier.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/services/test_toc_classifier.py`:

```python
def test_classes_constant_contains_practice():
    assert tc.PRACTICE == "practice"
    assert tc.PRACTICE in tc.CLASSES
    assert len(tc.CLASSES) == 7


def test_practice_keywords():
    titles = [
        "Laboratoriya ishi. Elektr zanjirini yigʻish",  # physics prefix form
        "1-laboratoriya mashg'uloti.",  # biology numbered form (trailing dot)
        "Amaliy mashg'ulot",  # geografiya bare form
        "Amaliy mashg'ulot. Reostat yordamida tok kuchini rostlash",  # physics
        "Лабораторная работа",  # RU parity
        "Практическая работа",  # RU parity
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.PRACTICE] * 6


def test_masalalar_yechish_whole_title_only():
    # Bare whole-title "Masalalar yechish" is a physics problem-solving
    # session (practice). The SAME phrase inside a longer title is a real
    # math lesson (g8alg/g8geo fixture rows) and must NOT be excluded.
    rows = [
        _row("Masalalar yechish"),
        _row("Masalalar yechish."),  # trailing punctuation tolerated
        _row("Решение задач"),
        _row("Kvadrat tenglamalar yordamida masalalar yechish"),
        _row("To'g'ri chiziq tenglamasi. Geometrik masalalar yechishning koordinatalar usuli"),
    ]
    result = tc.classify_entries(rows)
    assert result[:3] == [tc.PRACTICE] * 3
    assert result[3] == tc.LESSON
    assert result[4] == tc.LESSON


def test_amaliy_mashq_lesson_not_practice():
    # g8geo true-lesson title: "mashq" != "mashg'ulot" — must stay lesson.
    rows = [_row("Amaliy mashq va tatbiq")]
    assert tc.classify_entries(rows) == [tc.LESSON]


def test_muhim_xulosalar_revision():
    rows = [_row("I bob yuzasidan muhim xulosalar")]
    assert tc.classify_entries(rows) == [tc.REVISION]


def test_english_review_anchored():
    # "Review N" rows (Cambridge Prepare) are revision; "review" mid-title
    # must not match (anchored at title start).
    rows = [
        _row("Review 3 (Units 9–12)"),
        _row("Peer review in science"),
    ]
    result = tc.classify_entries(rows)
    assert result[0] == tc.REVISION
    assert result[1] == tc.LESSON


def test_english_backmatter_other():
    titles = [
        "Extra Activities",
        "Vocabulary List",
        "Grammar Reference and Practice",
        "List of Irregular Verbs",
        "Darslikdan foydalanish qoidalari",
    ]
    rows = [_row(t) for t in titles]
    assert tc.classify_entries(rows) == [tc.OTHER] * 5


def test_homoglyph_fold_latin_word_with_cyrillic_letters():
    # A Latin keyword still matches when OCR/extraction swapped in Cyrillic
    # lookalike letters (а=a, о=o, е=e).
    poisoned = "Lаborаtoriya ishi. Tajriba"  # Cyrillic а twice
    rows = [_row(poisoned)]
    assert tc.classify_entries(rows) == [tc.PRACTICE]


def test_homoglyph_fold_keeps_russian_keywords_matching():
    # The fold is applied to keyword tables too — pure-Cyrillic RU keywords
    # must keep matching pure-Cyrillic RU titles.
    rows = [_row("Повторение курса алгебры"), _row("Ответы")]
    assert tc.classify_entries(rows) == [tc.REVISION, tc.OTHER]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_toc_classifier.py -q`
Expected: FAIL — `AttributeError: ... no attribute 'PRACTICE'` and keyword assertion failures. Pre-existing tests still pass.

- [ ] **Step 3: Implement in `app/services/toc_classifier.py`**

Replace the module docstring precedence list, `_normalize`, the keyword tables, and `_keyword_class` as follows (leave `classify_entries` passes 2–4 untouched):

```python
"""Pure classifier for textbook TOC rows.

Tags each TOC row as one of seven classes so the batch launcher can default to
LESSON-only launches. Standalone module: no DB, no FastAPI, no model imports.
Input rows are duck-typed (anything exposing ``section_number``,
``section_title``, ``page_start``, ``page_end`` — the real caller passes
SQLAlchemy ``TOCEntry`` rows; tests use lightweight synthetic objects).

Precedence (first match wins):
    1. Keyword match on ``section_title`` (recall / practice / revision / test / other)
    2. Page-containment HEADER (a row that strictly contains >=2 other rows)
    3. ALL-CAPS + single-page residual -> other (a divider)
    4. Default -> lesson
"""

import re

LESSON = "lesson"
HEADER = "header"
RECALL = "recall"
PRACTICE = "practice"
REVISION = "revision"
TEST = "test"
OTHER = "other"

CLASSES = frozenset({LESSON, HEADER, RECALL, PRACTICE, REVISION, TEST, OTHER})

_APOSTROPHE_VARIANTS = ("ʼ", "’", "`", "‘", "ʻ")

# Cyrillic letters visually identical to Latin ones. Extractions occasionally
# emit them INSIDE Latin-script words (OCR/copy artifacts), which would make a
# clean Latin keyword silently miss. The fold is applied symmetrically to both
# titles and keyword tables (see _norm_keywords), so pure-Cyrillic Russian
# keywords keep matching pure-Cyrillic Russian titles.
_HOMOGLYPHS = str.maketrans({
    "а": "a",  # а
    "е": "e",  # е
    "о": "o",  # о
    "с": "c",  # с
    "р": "p",  # р
    "х": "x",  # х
    "у": "y",  # у
    "і": "i",  # і
    "ѕ": "s",  # ѕ
    "ј": "j",  # ј
})


def _normalize(text: str) -> str:
    """Lowercase, collapse apostrophe variants to `'`, fold Cyrillic homoglyphs."""
    normalized = text.lower()
    for variant in _APOSTROPHE_VARIANTS:
        normalized = normalized.replace(variant, "'")
    return normalized.translate(_HOMOGLYPHS)


def _norm_keywords(keywords: tuple[str, ...]) -> tuple[str, ...]:
    """Run keyword tables through the SAME normalization as titles at import."""
    return tuple(_normalize(kw) for kw in keywords)


_RECALL_KEYWORDS = _norm_keywords(("eslang",))

# Lab / practical / problem-solving sessions (physics, biology, geografiya).
# These rows are frequently section-/dars-NUMBERED in non-math books, so
# numbering is no lesson signal — only vocabulary is.
_PRACTICE_KEYWORDS = _norm_keywords((
    "laboratoriya ishi",       # physics: "Laboratoriya ishi. <topic>"
    "laboratoriya mashg'ulot", # biology: "N-laboratoriya mashg'uloti."
    "amaliy mashg'ulot",       # physics/geografiya (NOT "amaliy mashq…" — a real
                               # g8 geometry lesson title, see fixture g8geo)
    "лабораторная работа",
    "практическая работа",
))

# Whole-title only: bare "Masalalar yechish" is a physics problem-solving
# session, but the SAME phrase embedded in a longer title is a real math
# lesson ("Kvadrat tenglamalar yordamida masalalar yechish") — a substring
# match would false-EXCLUDE lessons, the worst failure direction. Built from
# _normalize()d strings so the pattern can never drift from the homoglyph fold.
_PRACTICE_FULLTITLE_RE = re.compile(
    r"^\s*("
    + "|".join(re.escape(_normalize(t)) for t in ("Masalalar yechish", "Решение задач"))
    + r")\W*$"
)

_REVISION_KEYWORDS = _norm_keywords((
    "takrorlash",
    "bobga doir mashqlar",
    "bobni takrorlash",
    "muhim xulosalar",         # physics: "N bob yuzasidan muhim xulosalar"
    "повторение",
    "упражнения к главе",
    "упражнения для повторения",
))

# English textbooks (Cambridge Prepare): "Review N (Units …)" rows. Anchored
# at title start so a lesson merely mentioning "review" stays a lesson.
_REVIEW_EN_RE = re.compile(r"^review\b")

_TEST_KEYWORDS = _norm_keywords((
    "nazorat",
    "bilimingizni sinab",
    "sinov",
    "тестовые задания",
))

_OTHER_KEYWORDS = _norm_keywords((
    "tarixiy",
    "javoblar",
    "ответы",
    "ilova",
    "loyiha ishi",
    "atamalar",
    "lug'at",
    "mundarija",
    "o'ylab ko'ring",
    "qo'shimcha topshiriq",
    "baholash dasturiga oid",
    "mantiqiy topshiriq",
    "darslikdan foydalanish",  # biology: "Darslikdan foydalanish qoidalari"
    "исторические",
    "межпредметные",
    # English back-matter (Cambridge Prepare):
    "extra activities",
    "vocabulary list",
    "grammar reference",
    "irregular verbs",
))

# Prefix-at-word-boundary (not whole-word): deliberately matches Uzbek
# plural/case forms like "Testlar"/"Testga", which are real test sections.
# Known cross-subject edge: any word STARTING with "test" also matches,
# e.g. biology's "Testosteron" would be misclassified as `test`. The
# 545-row cross-subject fixture (math + physics/biology/english/geografiya/
# kimyo/history) arbitrates if such titles ever appear in practice.
_TEST_WORD_RE = re.compile(r"\btest")


def _keyword_class(normalized_title: str) -> str | None:
    if any(kw in normalized_title for kw in _RECALL_KEYWORDS):
        return RECALL
    if (
        any(kw in normalized_title for kw in _PRACTICE_KEYWORDS)
        or _PRACTICE_FULLTITLE_RE.match(normalized_title)
    ):
        return PRACTICE
    if (
        any(kw in normalized_title for kw in _REVISION_KEYWORDS)
        or _REVIEW_EN_RE.match(normalized_title)
    ):
        return REVISION
    if any(kw in normalized_title for kw in _TEST_KEYWORDS) or _TEST_WORD_RE.search(normalized_title):
        return TEST
    if any(kw in normalized_title for kw in _OTHER_KEYWORDS):
        return OTHER
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_toc_classifier.py -q`
Expected: ALL PASS (old + new).

- [ ] **Step 5: Run the accuracy gate — must still pass (math fixture untouched by new vocab)**

Run: `uv run python -m pytest tests/services/test_toc_classifier_accuracy.py -q -s`
Expected: PASS, accuracy printed ≥ 0.99 on 252 rows.

- [ ] **Step 6: Commit**

```bash
git add tests/services/test_toc_classifier.py app/services/toc_classifier.py
git commit -m "tocvocab: practice class + subject vocabulary + homoglyph fold in toc_classifier"
```

---

### Task 2: Extend the accuracy fixture with the 6 G8 non-math books (293 hand-labeled rows)

**Files:**
- Modify: `tests/services/fixtures/toc_classifier_labels.json` (generated by the script below, then committed)
- Modify: `tests/services/test_toc_classifier_accuracy.py` (docstring row-count only)

- [ ] **Step 1: Generate the extended fixture** — write this script to `/private/tmp/claude-501/-Users-macmini5-Documents-Homework-Content-Generation-Automation/7b660674-d35e-4508-8a93-445c3bfebf61/scratchpad/extend_fixture.py` and run it FROM THE WORKTREE ROOT with `uv run python <path>`. It reads the live rows from edu_copy (READ-ONLY) and merges the hand-label table below:

```python
"""Extend toc_classifier_labels.json with 6 G8 non-math books from edu_copy (read-only)."""
import json
import subprocess
from pathlib import Path

FIXTURE = Path("tests/services/fixtures/toc_classifier_labels.json")

BOOKS = [
    ("g8phys", "e020d51f-f0ee-4076-b236-64885307f8e7", "G8 Fizika (uz)"),
    ("g8bio", "68601c99-04c6-4b0c-bd1f-d80f44f0d796", "G8 Biologiya (uz)"),
    ("g8eng", "d463c690-08ce-4fd1-ba27-fa51f39961b5", "G8 English (Prepare, Part 2: Units 11-20)"),
    ("g8geog", "f249da59-51f3-4e87-858a-a1fedf9d263e", "G8 Geografiya (uz)"),
    ("g8kim", "d87e4f5c-6ffa-4ede-9ca4-68b77b251749", "G8 Kimyo (uz)"),
    ("g8hist", "5e295cbc-c2a9-4552-924d-d1a3a5ee28bb", "G8 Tarix (uz)"),
]

# C1 (GK2 fold-in): targeted decision-pin SUBSETS — only the bare
# «Masalalar yechish» rows from real MATH books, deliberately labeled
# `practice` so the lesson→practice flip on math is a pinned decision,
# not an accident. NOT full books (row lists are partial by design).
SUBSET_BOOKS = [
    ("g9geo_my", "1bc43831-12a6-48c8-bd8d-8290d64ff000",
     "G9 Geometriya (uz) — TARGETED SUBSET: bare «Masalalar yechish» decision pin (C1)",
     (22, 25, 32), "practice"),
    ("g11mat_my_p1", "9bc1ad5e-b7c9-4eb4-896b-081329bd5287",
     "G11 Matematika P1 (uz) — TARGETED SUBSET: bare «Masalalar yechish» decision pin (C1)",
     (6, 11), "practice"),
    ("g11mat_my_p2", "bd7b2d6b-87bf-42f8-8be6-5b628c90c190",
     "G11 Matematika P2 (uz) — TARGETED SUBSET: bare «Masalalar yechish» decision pin (C1)",
     (2,), "practice"),
]

# Hand labels (2026-07-08, verified against printed TOCs + stored rows).
# key -> {order_index: true_class}; every other row is "lesson".
OVERRIDES = {
    "g8phys": {
        # Masalalar yechish (bare whole-title, section-NUMBERED — numbering != lesson)
        **{i: "practice" for i in (4, 7, 20, 28, 31, 36, 38, 42, 49, 61, 67)},
        # Laboratoriya ishi. <topic>
        **{i: "practice" for i in (16, 21, 26, 41, 65)},
        # Amaliy mashg'ulot. <topic>
        **{i: "practice" for i in (22, 25)},
        # N bob yuzasidan muhim xulosalar
        **{i: "revision" for i in (10, 33, 44, 55, 69)},
        # N bobni takrorlash uchun test topshiriqlari (already caught via takrorlash)
        **{i: "revision" for i in (9, 32, 43, 54, 68)},
        70: "other",  # Mashqlarning javoblari
    },
    "g8bio": {
        # N-laboratoriya mashg'uloti.
        **{i: "practice" for i in (14, 24, 28, 33, 49, 63)},
        0: "other",   # Darslikdan foydalanish qoidalari
        68: "other",  # Topshiriqlarning javoblari.
    },
    "g8eng": {
        3: "revision", 10: "revision", 17: "revision",  # Review 3/4/5
        18: "other",  # Extra Activities
        19: "other",  # Vocabulary List
        20: "other",  # Grammar Reference and Practice
        21: "other",  # List of Irregular Verbs
        # Keyword-UNDETECTABLE culture / life-skills pages (printed-TOC
        # "Culture"/"Life Skills" prefixes were dropped at extraction) —
        # true non-lessons, allowlisted as accepted false-inclusions:
        2: "other",   # ICT Literacy: Writing a Blog
        6: "other",   # Scotland
        9: "other",   # Social Responsibility: Protecting Animals
        13: "other",  # British TV Around the World
        16: "other",  # Emotional Skills: Being a Good Friend
    },
    "g8geog": {
        36: "practice", 58: "practice",  # Amaliy mashg'ulot (dars-NUMBERED)
        59: "revision",  # Umumlashtiruvchi takrorlash (already caught)
        60: "other",     # Ilovalar (already caught)
    },
    "g8kim": {},
    "g8hist": {},
}

NEW_ALLOWLIST = [
    {"book": "g8eng", "order_index": i,
     "reason": "Cambridge Prepare culture/life-skills page; stored title lost the "
               "printed 'Culture'/'Life Skills' prefix so no deterministic keyword "
               "exists. Tolerable direction (junk->lesson): visible in packet list, "
               "operator can deselect."}
    for i in (2, 6, 9, 13, 16)
]


def fetch_rows(book_id):
    out = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "macmini5", "-d", "edu_copy",
         "-tA", "-F", "\t", "-c",
         "select order_index, coalesce(section_number,''), section_title, "
         "coalesce(page_start::text,''), coalesce(page_end::text,'') "
         f"from toc_entries where book_id='{book_id}' order by order_index"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        oi, sn, title, ps, pe = line.split("\t")
        rows.append({
            "order_index": int(oi),
            "section_number": sn or None,
            "section_title": title,
            "page_start": int(ps) if ps else None,
            "page_end": int(pe) if pe else None,
        })
    return rows


def main():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    existing_keys = {b["key"] for b in data["books"]}

    for key, book_id, label in BOOKS:
        assert key not in existing_keys, f"duplicate book key {key}"
        rows = fetch_rows(book_id)
        assert rows, f"no rows for {key} ({book_id})"
        overrides = OVERRIDES[key]
        unknown = set(overrides) - {r["order_index"] for r in rows}
        assert not unknown, f"{key}: override indices not in book: {unknown}"
        for r in rows:
            r["true_class"] = overrides.get(r["order_index"], "lesson")
        data["books"].append({"key": key, "label": label, "rows": rows})

    for key, book_id, label, indices, true_class in SUBSET_BOOKS:
        assert key not in existing_keys, f"duplicate book key {key}"
        rows = [r for r in fetch_rows(book_id) if r["order_index"] in indices]
        assert len(rows) == len(indices), f"{key}: expected {indices}, got {[r['order_index'] for r in rows]}"
        for r in rows:
            # These MUST all be bare whole-title «Masalalar yechish» rows —
            # guard against TOC re-extraction having shifted order_index.
            assert r["section_title"].strip().lower().startswith("masalalar yechish"), (
                f"{key} #{r['order_index']}: unexpected title {r['section_title']!r}"
            )
            r["true_class"] = true_class
        data["books"].append({"key": key, "label": label, "rows": rows})

    data["_meta"]["accepted_false_inclusions"].extend(NEW_ALLOWLIST)
    data["_meta"]["source"] += (
        "; extended 2026-07-08 with 6 G8 non-math books + 3 targeted math subsets "
        "pinning bare-«Masalalar yechish»→practice on G9-geo/G11 (C1) "
        "(edu_copy real toc_entries, hand-labeled — toc-classifier-subject-vocab-1)"
    )
    FIXTURE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    total = sum(len(b["rows"]) for b in data["books"])
    print(f"books={len(data['books'])} total rows={total}")


main()
```

Expected stdout: `books=14 total rows=551`

- [ ] **Step 2: Eyeball the diff** — `git diff --stat tests/services/fixtures/toc_classifier_labels.json` shows only additions; spot-check that `g8phys` #4 is `practice`, `g8eng` #6 (`Scotland`) is `other`, `g8kim`/`g8hist` rows are all `lesson`, and the three C1 subset books (`g9geo_my`, `g11mat_my_p1`, `g11mat_my_p2`) carry exactly 3+2+1 bare «Masalalar yechish» rows labeled `practice`.

- [ ] **Step 3: Run the accuracy gate**

Run: `uv run python -m pytest tests/services/test_toc_classifier_accuracy.py -q -s`
Expected: PASS. Printed accuracy = 545/551 ≈ 0.989 (6 accepted false-inclusions: g7alg#0 + 5×g8eng). ZERO false-exclusions — in particular the 6 C1 math subset rows predict `practice` and are labeled `practice`, so they pass exact-match, not via any allowlist. g8geo (geometriya) count assertion unaffected.

- [ ] **Step 4: Update the accuracy test docstring** — in `tests/services/test_toc_classifier_accuracy.py`, replace:

```python
"""Accuracy gate against hand-labeled real-data TOC rows.

Loads tests/services/fixtures/toc_classifier_labels.json (252 rows, 5 real
Uzbek/Russian math textbooks) and asserts the classifier's predictions
against the hand-labeled ground truth. See the fixture's ``_meta`` for the
documented, accepted false-inclusion allowlist.
"""
```

with:

```python
"""Accuracy gate against hand-labeled real-data TOC rows.

Loads tests/services/fixtures/toc_classifier_labels.json (551 rows: 5 real
Uzbek/Russian math textbooks + the 6 generatable G8 non-math books —
physics/biology/english/geografiya/kimyo/history — + 3 targeted math
subsets pinning bare-«Masalalar yechish»→practice on G9-geo/G11) and
asserts the classifier's predictions against the hand-labeled ground
truth. See the fixture's ``_meta`` for the documented, accepted
false-inclusion allowlist.
"""
```

- [ ] **Step 5: Re-run both classifier test files, then commit**

Run: `uv run python -m pytest tests/services/test_toc_classifier.py tests/services/test_toc_classifier_accuracy.py -q`
Expected: ALL PASS.

```bash
git add tests/services/fixtures/toc_classifier_labels.json tests/services/test_toc_classifier_accuracy.py
git commit -m "tocvocab: extend accuracy fixture to 545 rows — 6 G8 non-math books hand-labeled"
```

---

### Task 3: FE mirror + schema comment for the `practice` class

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` (~line 551)
- Modify: `web/src/lib/types.ts` (~line 92)
- Modify: `app/schemas/toc.py` (~line 24)

- [ ] **Step 1: `launcher.tsx`** — replace:

```ts
const ALL_ENTRY_CLASSES = ["lesson", "header", "recall", "revision", "test", "other"] as const;
```

with:

```ts
const ALL_ENTRY_CLASSES = ["lesson", "header", "recall", "practice", "revision", "test", "other"] as const;
```

and in `CLASS_META`, insert after the `recall` entry:

```ts
  practice: { label: "practice", chipCls: "bg-amber-400/10 text-amber-300/80" },
```

- [ ] **Step 2: `types.ts`** — replace the `entry_class` doc comment:

```ts
  /** Server-computed TOC row classification: "lesson" | "header" | "recall" |
   *  "revision" | "test" | "other". The FE displays this — never re-derives it. */
```

with:

```ts
  /** Server-computed TOC row classification: "lesson" | "header" | "recall" |
   *  "practice" | "revision" | "test" | "other". The FE displays this — never
   *  re-derives it. */
```

- [ ] **Step 3: `app/schemas/toc.py`** — replace:

```python
    # Row class (lesson/header/recall/revision/test/other), computed on-the-fly
```

with:

```python
    # Row class (lesson/header/recall/practice/revision/test/other), computed on-the-fly
```

- [ ] **Step 4: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: both clean. (If `node_modules` missing in the worktree: `npm install` first.)

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/launcher.tsx web/src/lib/types.ts app/schemas/toc.py
git commit -m "tocvocab: FE mirror + schema comment for practice class"
```

---

### Task 4: Finish (controller-driven)

- [ ] Full suite: `uv run python -m pytest tests/ -q` — green (modulo pre-existing known reds, verify they match `cli-failover-tests-red-1` only... note: 0124 fixed those; expect ZERO reds).
- [ ] **Live read-only proof** (controller runs from worktree): re-run the classifier over the 6 stored G8 books PLUS G9-geo (`1bc43831`) and G11 P1/P2 (`9bc1ad5e`/`bd7b2d6b`) and confirm the "Verification targets" table above exactly — newly excluded rows match, the ONLY math flips are the 6 pinned bare-«Masalalar yechish» rows, no suffix-form math lesson flips.
- [ ] Worklog **0130** in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md` — **must flag the C1 operator note**: G9-Geometriya default LESSON-only launch drops ~55→~52 rows (3 problem-solving sections now opt-in `practice`) — intended, not a regression; same for G11 (2+1 rows) and physics-family books.
- [ ] Close `toc-classifier-subject-vocab-1` in `docs/memory/WISHLIST.md` (note the over-count correction + dropped `life skills`/`culture` keywords + english allowlist + C1 math flip).
- [ ] De-stale live-system docs: `docs/CODE_MAP.md` + `docs/HOW_IT_WORKS.md` wherever the class list (6 classes) or "math-calibrated" wording appears (grep `toc_classifier` / `entry_class` / class lists).
- [ ] `git mv docs/superpowers/plans/2026-07-08-toc-classifier-subject-vocab.md docs/superpowers/plans/shipped/`
- [ ] Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — rebase + re-run suite if base moved.
- [ ] Push, open PR to `Nggaev-v2`, hand to GK2 (no self-merge).
