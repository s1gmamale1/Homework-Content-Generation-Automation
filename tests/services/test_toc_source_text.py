"""TOC source-text extraction must scan BOTH ends of the PDF.

Many textbooks (esp. Uzbek ones) place the contents page ("Mundarija") at the
BACK of the book. The original implementation read only the first ~40 pages
until a char budget filled, so a back-of-book TOC was never seen and
`extract_toc` returned 0 entries. These tests pin that the tail pages are read.
"""

from pathlib import Path

import pypdf

from app.services.agent import _extract_toc_source_text


class _FakePage:
    def __init__(self, text: str) -> None:
        self._t = text

    def extract_text(self) -> str:
        return self._t


class _FakeReader:
    """A 60-page book whose TOC lives ONLY on page 58 (near the back)."""

    def __init__(self, _path: str) -> None:
        self.pages = []
        for i in range(1, 61):
            if i == 58:
                self.pages.append(
                    _FakePage("Mundarija\n1.1 Kesma va burchaklar ... 8\n1.2 ... 16")
                )
            else:
                self.pages.append(_FakePage(("body text page %d " % i) * 60))


def test_toc_source_captures_back_of_book_contents(monkeypatch):
    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    text, meta = _extract_toc_source_text(Path("dummy.pdf"))

    assert meta["total_pages"] == 60
    # The back-of-book TOC page must be included.
    assert "Mundarija" in text
    assert "--- PDF page 58 ---" in text
    # Front pages must still be read (we didn't drop the front scan).
    assert "--- PDF page 1 ---" in text
    # Stay within the total char budget.
    assert len(text) <= 60_000


def test_glyph_encoded_text_is_decoded(monkeypatch):
    """Some PDFs (custom-subset fonts, no Unicode map) make pypdf emit glyph
    names like '/G55/G6D' instead of characters. When that pattern dominates a
    page, decode '/G<hex>' (as a cp1252 byte) to recover real text."""

    class _GlyphReader:
        def __init__(self, _path: str) -> None:
            # '/G55/G6D/G75/G6D/G69/G79' == 'Umumiy'; repeat to clear the
            # "glyph tokens dominate" threshold.
            self.pages = [_FakePage("/G55/G6D/G75/G6D/G69/G79 " * 6)]

    monkeypatch.setattr(pypdf, "PdfReader", _GlyphReader)
    text, _meta = _extract_toc_source_text(Path("dummy.pdf"))
    assert "Umumiy" in text
    assert "/G55" not in text  # raw glyph codes must be gone


def test_normal_text_with_stray_slash_g_not_mangled(monkeypatch):
    """A page of ordinary text must pass through untouched even if it contains
    an incidental '/G' — the decoder only fires when glyph tokens dominate."""

    class _NormalReader:
        def __init__(self, _path: str) -> None:
            self.pages = [_FakePage("Chapter 1: Vectors and /G notation in physics")]

    monkeypatch.setattr(pypdf, "PdfReader", _NormalReader)
    text, _meta = _extract_toc_source_text(Path("dummy.pdf"))
    assert "Chapter 1: Vectors and" in text


def test_toc_source_short_book_no_duplicate_pages(monkeypatch):
    """A short book (fewer pages than the front ceiling) must not emit the same
    page twice when the front and tail windows overlap."""

    class _ShortReader:
        def __init__(self, _path: str) -> None:
            self.pages = [_FakePage("Mundarija page %d" % i) for i in range(1, 6)]

    monkeypatch.setattr(pypdf, "PdfReader", _ShortReader)
    text, _meta = _extract_toc_source_text(Path("dummy.pdf"))
    assert text.count("--- PDF page 3 ---") == 1
