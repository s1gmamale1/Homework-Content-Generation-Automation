"""Tests for the windowed page-subset helper ``_subset_pdf`` (margin + max_pages).

The default-args call MUST preserve the exact legacy [page_start..page_end]
behavior; the new keyword-only ``margin`` / ``max_pages`` widen and cap the
window respectively.
"""
from pathlib import Path

import pytest

from app.services.agent import _subset_pdf


def _make_pdf(tmp_path: Path, n_pages: int) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    p = tmp_path / f"book_{n_pages}.pdf"
    with open(p, "wb") as f:
        writer.write(f)
    return p


def _page_count(out: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(out)).pages)


def test_subset_window_adds_margin(tmp_path):
    p = _make_pdf(tmp_path, 40)
    out = _subset_pdf(p, 20, 22, margin=5)
    assert out is not None
    try:
        # widened to [15..27] -> 13 pages
        assert _page_count(out) == 13
    finally:
        Path(out).unlink()


def test_subset_window_clamps_to_bounds(tmp_path):
    p = _make_pdf(tmp_path, 6)
    out = _subset_pdf(p, 2, 3, margin=5)
    assert out is not None
    try:
        # widened to [-3..8] -> clamped to [1..6] -> 6 pages
        assert _page_count(out) == 6
    finally:
        Path(out).unlink()


def test_subset_window_respects_max_pages(tmp_path):
    p = _make_pdf(tmp_path, 40)
    out = _subset_pdf(p, 20, 22, margin=20, max_pages=11)
    assert out is not None
    try:
        # widened to [1..40] then capped to 11 centered on 20..22 -> [16..26]
        assert _page_count(out) == 11
    finally:
        Path(out).unlink()


def test_subset_legacy_callsite_unchanged(tmp_path):
    p = _make_pdf(tmp_path, 40)
    out = _subset_pdf(p, 10, 12)
    assert out is not None
    try:
        assert _page_count(out) == 3
    finally:
        Path(out).unlink()
