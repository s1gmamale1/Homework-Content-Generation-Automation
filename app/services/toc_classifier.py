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
    "а": "a",
    "е": "e",
    "о": "o",
    "с": "c",
    "р": "p",
    "х": "x",
    "у": "y",
    "і": "i",
    "ѕ": "s",
    "ј": "j",
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
    "laboratoriya ishi",        # physics: "Laboratoriya ishi. <topic>"
    "laboratoriya mashg'ulot",  # biology: "N-laboratoriya mashg'uloti."
    "amaliy mashg'ulot",        # physics/geografiya (NOT "amaliy mashq…" — a real
                                # g8 geometry lesson title, see fixture g8geo)
    "лабораторная работа",
    "практическая работа",
))

# Whole-title only: bare "Masalalar yechish" is a problem-solving session
# (physics AND math books — an intended lesson→practice reclassification on
# G9-geo/G11, pinned in the accuracy fixture), but the SAME phrase embedded
# in a longer title is a real math lesson ("Kvadrat tenglamalar yordamida
# masalalar yechish") — a substring match would false-EXCLUDE lessons, the
# worst failure direction. Built from _normalize()d strings so the pattern
# can never drift from the homoglyph fold.
_PRACTICE_FULLTITLE_RE = re.compile(
    r"^\s*("
    + "|".join(re.escape(_normalize(t)) for t in ("Masalalar yechish", "Решение задач"))
    + r")\W*$"
)

_REVISION_KEYWORDS = _norm_keywords((
    "takrorlash",
    "bobga doir mashqlar",
    "bobni takrorlash",
    "muhim xulosalar",  # physics: "N bob yuzasidan muhim xulosalar"
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
    # English back-matter (Cambridge Prepare):
    "extra activities",
    "vocabulary list",
    "grammar reference",
    "irregular verbs",
    "исторические",
    "межпредметные",
))

# Prefix-at-word-boundary (not whole-word): deliberately matches Uzbek
# plural/case forms like "Testlar"/"Testga", which are real test sections.
# Known cross-subject edge: any word STARTING with "test" also matches,
# e.g. biology's "Testosteron" would be misclassified as `test`. The 551-row
# cross-subject fixture (math + physics/biology/english/geografiya/kimyo/
# history) arbitrates if such titles ever appear in practice.
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


def _is_all_caps(title: str) -> bool:
    letters = [ch for ch in title if ch.isalpha()]
    if not letters:
        return False
    return title == title.upper()


def _is_single_page(page_start, page_end) -> bool:
    return page_start is not None and page_end is not None and page_end == page_start


def classify_entries(entries) -> list[str]:
    """Classify a sequence of duck-typed TOC rows.

    Returns a list of class strings aligned to ``entries`` in INPUT order.
    Containment (HEADER) detection does a pairwise scan over the original
    row indices (each candidate row against every other row) rather than
    sorting by page range; the input is never reordered, so results are
    already aligned to the original input order.
    """
    rows = list(entries)
    n = len(rows)
    results: list = [None] * n

    # Pass 1: keyword classes take precedence over everything else.
    remaining_indices = []
    for i, row in enumerate(rows):
        normalized_title = _normalize(row.section_title)
        keyword_result = _keyword_class(normalized_title)
        if keyword_result is not None:
            results[i] = keyword_result
        else:
            remaining_indices.append(i)

    # Pass 2: page-containment HEADER. A row is only ELIGIBLE to BE a header
    # if it wasn't itself keyword-classified in Pass 1 (keyword precedence
    # holds) and carries usable page bounds. But the CHILDREN counted toward
    # the >=2 threshold are drawn from ALL other rows, including ones that
    # already got a keyword class (e.g. a "recall"/"revision"/"test" child
    # still counts as a contained child of its chapter umbrella) -- undercounting
    # by restricting to keyword-unclassified rows was the original bug.
    # A candidate must span MORE THAN ONE page: a chapter umbrella always
    # does. Without this, several single-page rows sharing an identical
    # [p, p] range mutually satisfy the <=/>= containment check against each
    # other, and a real lesson could false-flip to `header` (the worst
    # outcome — silent exclusion from generation).
    containment_candidates = [
        i
        for i in remaining_indices
        if rows[i].page_start is not None
        and rows[i].page_end is not None
        and rows[i].page_end > rows[i].page_start
    ]
    for i in containment_candidates:
        row_a = rows[i]
        contained_count = 0
        for j in range(n):
            if j == i:
                continue
            row_b = rows[j]
            if row_b.page_start is None or row_b.page_end is None:
                continue
            if row_a.page_start <= row_b.page_start and row_b.page_end <= row_a.page_end:
                contained_count += 1
                if contained_count >= 2:
                    break
        if contained_count >= 2:
            results[i] = HEADER

    # Pass 3: ALL-CAPS + single-page residual -> other.
    for i in remaining_indices:
        if results[i] is not None:
            continue
        row = rows[i]
        if _is_all_caps(row.section_title) and _is_single_page(row.page_start, row.page_end):
            results[i] = OTHER

    # Pass 4: default -> lesson.
    for i in remaining_indices:
        if results[i] is None:
            results[i] = LESSON

    return results
