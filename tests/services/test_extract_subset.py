"""Tests for the windowed page-subset helper ``_subset_pdf`` (margin + max_pages).

The default-args call MUST preserve the exact legacy [page_start..page_end]
behavior; the new keyword-only ``margin`` / ``max_pages`` widen and cap the
window respectively.
"""
from pathlib import Path

import pytest

import app.services.agent as agent
from app.services.agent import (
    _subset_pdf,
    extract_text_is_too_sparse,
    pdf_page_count,
    read_page_range_text,
)


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


# --- read_page_range_text (windowed text slice) ---------------------------
#
# No reportlab in this env and pypdf blank pages carry no extractable text, so
# text-bearing PDFs are produced the way the repo's other agent tests do it:
# monkeypatch the ``PdfReader`` the function uses with a fake reader whose pages
# return distinct text via ``extract_text()`` (cf. tests/services/
# test_toc_source_text.py). The image-only case uses a real pypdf blank PDF,
# which genuinely yields no text.

class _FakePage:
    def __init__(self, text: str) -> None:
        self._t = text

    def extract_text(self) -> str:
        return self._t


def _fake_text_reader(n_pages: int):
    """Factory for a PdfReader stand-in: each page stamps a unique marker."""

    class _FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [_FakePage(f"PAGE {i} BODY") for i in range(1, n_pages + 1)]

    return _FakeReader


def test_page_range_text_reads_window(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "PdfReader", _fake_text_reader(14))
    p = tmp_path / "book.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")  # path only; reader is faked
    text = read_page_range_text(p, 10, 12, margin=1)
    # window widened to [9..13]
    for i in (9, 10, 11, 12, 13):
        assert f"PAGE {i} BODY" in text
    # just outside the window must NOT appear
    assert "PAGE 8 BODY" not in text
    assert "PAGE 14 BODY" not in text


def test_page_range_text_clamps(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "PdfReader", _fake_text_reader(14))
    p = tmp_path / "book.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")
    # start = max(1, 1 - 5) = 1 ; no negative index, no exception
    text = read_page_range_text(p, 1, 2, margin=5)
    assert "PAGE 1 BODY" in text


def test_page_range_text_empty_on_imageonly(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    p = tmp_path / "blank.pdf"
    with open(p, "wb") as f:
        writer.write(f)
    assert read_page_range_text(p, 1, 2, margin=1) == ""


# --- per-page density detector + page count -------------------------------


def test_too_sparse_flags_scanned():
    # 17000 chars / 240 pages ≈ 71 chars/page → below the 300 floor
    assert extract_text_is_too_sparse("a" * 17000, 240) is True


def test_too_sparse_passes_normal():
    # 353000 chars / 192 pages ≈ 1838 chars/page → well above the floor
    assert extract_text_is_too_sparse("a" * 353000, 192) is False


def test_too_sparse_zero_pages():
    # Can't judge density with no page count → never fires
    assert extract_text_is_too_sparse("anything", 0) is False


def test_pdf_page_count(tmp_path):
    p = _make_pdf(tmp_path, 7)
    assert pdf_page_count(p) == 7
    assert pdf_page_count(Path("/nope.pdf")) == 0
