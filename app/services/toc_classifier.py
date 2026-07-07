"""Pure classifier for textbook TOC rows.

Tags each TOC row as one of six classes so the batch launcher can default to
LESSON-only launches. Standalone module: no DB, no FastAPI, no model imports.
Input rows are duck-typed (anything exposing ``section_number``,
``section_title``, ``page_start``, ``page_end`` — the real caller passes
SQLAlchemy ``TOCEntry`` rows; tests use lightweight synthetic objects).

Precedence (first match wins):
    1. Keyword match on ``section_title`` (recall / revision / test / other)
    2. Page-containment HEADER (a row that strictly contains >=2 later rows)
    3. ALL-CAPS + single-page residual -> other (a divider)
    4. Default -> lesson
"""

import re

LESSON = "lesson"
HEADER = "header"
RECALL = "recall"
REVISION = "revision"
TEST = "test"
OTHER = "other"

CLASSES = frozenset({LESSON, HEADER, RECALL, REVISION, TEST, OTHER})

_APOSTROPHE_VARIANTS = ("ʼ", "’", "`")


def _normalize(text: str) -> str:
    """Lowercase and collapse apostrophe glyph variants to a plain ASCII `'`."""
    normalized = text.lower()
    for variant in _APOSTROPHE_VARIANTS:
        normalized = normalized.replace(variant, "'")
    return normalized


_RECALL_KEYWORDS = ("eslang",)

_REVISION_KEYWORDS = (
    "takrorlash",
    "bobga doir mashqlar",
    "bobni takrorlash",
    "повторение",
)

_TEST_KEYWORDS = (
    "nazorat",
    "bilimingizni sinab",
    "sinov",
)

_OTHER_KEYWORDS = (
    "tarixiy",
    "javoblar",
    "ответы",
    "ilova",
    "loyiha ishi",
    "atamalar",
    "lug'at",
    "mundarija",
)

_TEST_WORD_RE = re.compile(r"\btest")


def _keyword_class(normalized_title: str) -> str | None:
    if any(kw in normalized_title for kw in _RECALL_KEYWORDS):
        return RECALL
    if any(kw in normalized_title for kw in _REVISION_KEYWORDS):
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


def classify_entries(entries) -> list:
    """Classify a sequence of duck-typed TOC rows.

    Returns a list of class strings aligned to ``entries`` in INPUT order.
    Containment (HEADER) detection needs sibling comparison, so this sorts a
    working copy by page range internally, but always returns results indexed
    back to the original input order.
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

    # Pass 2: page-containment HEADER, only among rows not already classified
    # by keyword and that carry usable page bounds.
    containment_candidates = [
        i for i in remaining_indices if rows[i].page_start is not None and rows[i].page_end is not None
    ]
    for i in containment_candidates:
        row_a = rows[i]
        contained_count = 0
        for j in remaining_indices:
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
