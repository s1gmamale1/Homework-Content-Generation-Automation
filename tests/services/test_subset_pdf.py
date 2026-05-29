"""Subset-PDF extraction helper (harvested from PR #1, safer variant).

Rather than passing the whole textbook PDF to the (gemini) extractor — which
rejects files > 20 MB and then poisons every downstream phase with its refusal
message — extract attaches only the section's page window as a small PDF. This
preserves diagram/visual content (unlike text-only extraction). On any problem
the helper returns None so the caller falls back to the full PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

from app.services.agent import _subset_pdf  # noqa: E402


def _make_pdf(path: Path, n_pages: int) -> None:
    writer = pypdf.PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)


def test_subset_extracts_inclusive_page_window(tmp_path: Path) -> None:
    src = tmp_path / "book.pdf"
    _make_pdf(src, 10)
    out = _subset_pdf(src, 3, 5)  # pages 3,4,5 -> 3 pages
    assert out is not None
    assert pypdf.PdfReader(str(out)).pages.__len__() == 3
    out.unlink()


def test_subset_clamps_end_past_eof(tmp_path: Path) -> None:
    src = tmp_path / "book.pdf"
    _make_pdf(src, 4)
    out = _subset_pdf(src, 3, 99)  # clamp to pages 3,4 -> 2 pages
    assert out is not None
    assert len(pypdf.PdfReader(str(out)).pages) == 2
    out.unlink()


def test_subset_returns_none_for_invalid_range(tmp_path: Path) -> None:
    src = tmp_path / "book.pdf"
    _make_pdf(src, 5)
    assert _subset_pdf(src, 0, 0) is None        # non-positive start
    assert _subset_pdf(src, 4, 2) is None         # end before start
    assert _subset_pdf(src, None, None) is None    # missing pages


def test_subset_returns_none_on_unreadable_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "missing.pdf"
    assert _subset_pdf(bad, 1, 2) is None          # nonexistent -> fall back
