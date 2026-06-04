"""Match an app-extracted lesson title to a human-built Notion lesson page by
content words. Conservative: only a UNIQUE superset match adopts a human page;
anything ambiguous, short, or unmatched returns None (the caller then creates an
app-owned page). No stopword list and no stemming in v1 — a near-miss safely
falls back rather than risking a wrong adoption."""

from __future__ import annotations

import re

from .page_creator import _normalize

CONTAINER_TITLE = "Generated Lessons"
_MIN_CONTENT_WORDS = 2

# Structural prefix words to drop, compared AFTER _fold (lowercase + apostrophes
# stripped, so "bo'lim" -> "bolim").
_MARKER_WORDS = frozenset({"mavzu", "bob", "bolim", "paragraf"})

# Runs of letters of any script (Latin incl. Uzbek, Cyrillic). Digits, ASCII dot
# leaders, the U+2026 ellipsis glyph, the section sign, and punctuation are all
# non-letters, so this regex drops every one of them.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Apostrophe variants Uzbek uses inside words (o', g'). Stripping them folds
# "bo'lim" -> "bolim". This intentionally does NOT fold diacritics.
_APOSTROPHES = "'‘’ʻ`"


def _fold(s: str) -> str:
    return s.lower().translate({ord(c): None for c in _APOSTROPHES})


def tokenize(title: str) -> frozenset[str]:
    """Lowercase content-word set: drops numbers, the section sign, punctuation,
    page-number leaders (ASCII dot runs AND U+2026 ellipsis glyphs), and the
    structural marker words."""
    words = _WORD_RE.findall(_fold(title))
    return frozenset(w for w in words if w not in _MARKER_WORDS)


def match_lesson(app_title: str, human_pages: list[dict]) -> str | None:
    """Return the id of the UNIQUE human page whose content words are a superset
    of the app lesson's content words, else None. ``human_pages`` are
    ``{"id", "title"}`` dicts as returned by ``NotionClientWrapper.get_child_pages``.
    The app's own ``Generated Lessons`` container is excluded from candidates."""
    app_words = tokenize(app_title)
    if len(app_words) < _MIN_CONTENT_WORDS:
        return None
    container_norm = _normalize(CONTAINER_TITLE)
    candidates = [
        p["id"]
        for p in human_pages
        if _normalize(p["title"]) != container_norm
        and app_words <= tokenize(p["title"])
    ]
    return candidates[0] if len(candidates) == 1 else None
