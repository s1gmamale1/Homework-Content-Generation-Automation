"""Deterministic script (Cyrillic vs. Latin) detector for a PDF's text — the
language guard for Notion-fetched textbooks (BE-19 task 5).

A live-confirmed case has an Uzbek (Latin) PDF attached to the Russian
"Математика" part page in Notion; once child pages are reachable (Task 4),
naive ingestion would silently generate a whole book of wrong-language
homework. This module is the small, pure classifier the `/books/from-notion`
route calls post-download, pre-ingest, to catch that case deterministically.

Deliberately dependency-light: pypdf + stdlib only. Does NOT import
`app.services.agent` — that module is shaped for the CLI content pipeline
(settings-driven budgets, logging, etc.); this one is a standalone script
sniffer with a single narrow job, safe to call from a route handler.
"""
from __future__ import annotations

import io

from pypdf import PdfReader

# Cyrillic block (covers Russian + Uzbek-Cyrillic) plus the Cyrillic
# Supplement block (extra letters some Slavic/Turkic orthographies use).
_CYRILLIC_RANGES = ((0x0400, 0x04FF), (0x0500, 0x052F))

# A sample is confidently "cyrillic" once at least this fraction of its
# alphabetic characters are Cyrillic. Below this, incidental Cyrillic noise
# (a footnote, a stray brand name) shouldn't flip a Latin-script book.
_CYRILLIC_RATIO_THRESHOLD = 0.3


def _is_cyrillic_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CYRILLIC_RANGES)


def detect_pdf_script(pdf_bytes: bytes, sample_pages: int = 5) -> str:
    """Sample up to `sample_pages` pages of `pdf_bytes` and classify the
    dominant script.

    Returns exactly one of "cyrillic" | "latin" | "unknown":
    - "unknown": encrypted/corrupt PDFs, or no alphabetic text could be
      extracted from any sampled page (e.g. a scanned book with no OCR
      layer). This function never raises.
    - "cyrillic": Cyrillic characters are >= 30% of the sampled alphabetic
      characters.
    - "latin": otherwise (alphabetic text was found, but not Cyrillic-heavy).

    Bounded cost: only the first `sample_pages` pages are ever read, so a
    600-page book is never fully parsed.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n_pages = len(reader.pages)
    except Exception:
        return "unknown"

    alpha_count = 0
    cyrillic_count = 0
    for idx in range(min(n_pages, sample_pages)):
        try:
            text = reader.pages[idx].extract_text() or ""
        except Exception:
            # Per-page corruption (a bad content stream) shouldn't sink the
            # whole sample — skip the page and keep going.
            continue
        for ch in text:
            if ch.isalpha():
                alpha_count += 1
                if _is_cyrillic_char(ch):
                    cyrillic_count += 1

    if alpha_count == 0:
        return "unknown"
    if cyrillic_count / alpha_count >= _CYRILLIC_RATIO_THRESHOLD:
        return "cyrillic"
    return "latin"
