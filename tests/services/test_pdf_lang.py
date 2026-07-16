"""Tests for `detect_pdf_script` — the deterministic Cyrillic/Latin script
guard (BE-19 task 5). A live-confirmed Notion case has an Uzbek (Latin) PDF
attached to the Russian "Математика" part page; once child pages are
reachable (Task 4), naive ingestion would silently generate a whole book of
wrong-language homework. This module is the pure classifier the route calls
post-download, pre-ingest.

Text-bearing PDFs are faked the way the repo's other agent/pdf tests do it
(cf. tests/services/test_extract_subset.py): monkeypatch the `PdfReader` the
module uses with a fake reader whose pages return canned text via
`extract_text()`. The blank-PDF / no-alphabetic-text case uses a REAL pypdf
blank PDF, which genuinely yields no text.
"""
import io

import pytest

import app.services.pdf_lang as pdf_lang
from app.services.pdf_lang import _MIN_ALPHA_EVIDENCE, detect_pdf_script


class _FakePage:
    def __init__(self, text: str | None = "", *, raises: bool = False) -> None:
        self._text = text
        self._raises = raises

    def extract_text(self) -> str:
        if self._raises:
            raise ValueError("corrupt page content stream")
        return self._text


class _FakeReader:
    """Stand-in for pypdf.PdfReader: constructed from BytesIO (ignored — the
    fake just serves canned pages), tracks which page indices were accessed
    so tests can assert the sample_pages bound is honored."""

    def __init__(self, pages: list[_FakePage], *, raise_on_init: bool = False) -> None:
        if raise_on_init:
            raise ValueError("encrypted: could not decrypt with an empty password")
        self._pages = pages
        self.accessed: list[int] = []

    @property
    def pages(self):
        return _TrackingPages(self._pages, self.accessed)


class _TrackingPages:
    def __init__(self, pages: list[_FakePage], accessed: list[int]) -> None:
        self._pages = pages
        self._accessed = accessed

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, idx: int) -> _FakePage:
        self._accessed.append(idx)
        return self._pages[idx]


def _patch_reader(monkeypatch, factory):
    """factory(bytes_io) -> _FakeReader instance; PdfReader(bytes_io) is what
    detect_pdf_script actually calls, so the patched constructor takes and
    ignores the BytesIO arg to match the real call shape."""
    monkeypatch.setattr(pdf_lang, "PdfReader", lambda _bio: factory())


# ─── Cyrillic sample -> "cyrillic" ───────────────────────────────────────────

def test_cyrillic_sample_detected_as_cyrillic(monkeypatch):
    # Repeated to clear _MIN_ALPHA_EVIDENCE (a single sentence's ~34 alphabetic
    # chars is below the floor and would legitimately classify as "unknown" —
    # bulking up the fixture keeps this test about the Cyrillic-ratio logic,
    # not the evidence floor, which has its own tests below).
    reader = _FakeReader([_FakePage("Алгебра. Учебник для 8 класса. Глава первая. " * 6)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "cyrillic"


# ─── Latin sample -> "latin" ─────────────────────────────────────────────────

def test_latin_sample_detected_as_latin(monkeypatch):
    reader = _FakeReader([_FakePage("Algebra. 8-sinf uchun darslik. Birinchi bob. " * 6)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "latin"


# ─── Mixed script below the 0.3 Cyrillic-ratio threshold -> "latin" ─────────

def test_mostly_latin_with_minor_cyrillic_noise_is_latin(monkeypatch):
    # A handful of Cyrillic footnote glyphs shouldn't flip an otherwise-Latin
    # book: ~5/60 alphabetic chars per repetition are Cyrillic, well under the
    # 0.3 floor (repeating preserves the ratio exactly while clearing the
    # evidence floor).
    reader = _FakeReader([_FakePage("This is an English biology textbook page эх " * 6)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "latin"


# ─── Mixed script at/above the 0.3 Cyrillic-ratio threshold -> "cyrillic" ───

def test_mostly_cyrillic_with_minor_latin_noise_is_cyrillic(monkeypatch):
    reader = _FakeReader([_FakePage("Физика 9 класс NASA ABC " * 12)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "cyrillic"


# ─── Empty/no extractable text (scanned PDF) -> "unknown" ───────────────────

def test_blank_real_pdf_yields_unknown():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    assert detect_pdf_script(buf.getvalue()) == "unknown"


def test_none_extract_text_result_yields_unknown(monkeypatch):
    reader = _FakeReader([_FakePage(None)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "unknown"


# ─── Minimum alpha-evidence floor: below it, "unknown" even with alpha_count > 0
# (merge-gate fix 2 — a single stray Latin letter in a scanned RU book's noisy
# text layer used to be enough to confidently misclassify as "latin"). ───────

def test_single_letter_sample_is_unknown_not_latin(monkeypatch):
    # RED pre-fix: alpha_count=1 (> 0) skipped the "unknown" branch entirely
    # and fell through to "latin" (no Cyrillic present) — a single stray
    # letter is noise, not a confident script verdict.
    reader = _FakeReader([_FakePage("A")])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "unknown"


def test_low_alpha_evidence_below_floor_is_unknown(monkeypatch):
    # A short, otherwise-unambiguous Latin sentence still under the floor.
    reader = _FakeReader([_FakePage("Hi there.")])
    _patch_reader(monkeypatch, lambda: reader)
    assert sum(ch.isalpha() for ch in "Hi there.") < _MIN_ALPHA_EVIDENCE
    assert detect_pdf_script(b"%PDF-1.4 x") == "unknown"


def test_alpha_evidence_exactly_at_floor_classifies(monkeypatch):
    # Pinned to the constant itself (not a magic number): exactly
    # _MIN_ALPHA_EVIDENCE alphabetic characters is enough evidence to trust a
    # verdict — the floor excludes samples strictly BELOW it, not at it.
    reader = _FakeReader([_FakePage("a" * _MIN_ALPHA_EVIDENCE)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "latin"


def test_alpha_evidence_one_below_floor_is_unknown(monkeypatch):
    # One character short of the floor -> "unknown", not "latin".
    reader = _FakeReader([_FakePage("a" * (_MIN_ALPHA_EVIDENCE - 1))])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "unknown"


def test_rich_sample_still_classifies_above_floor(monkeypatch):
    # Control: a realistically long sample (well above the floor) still
    # classifies confidently — the floor only suppresses thin/noisy samples,
    # it doesn't make classification harder in general.
    reader = _FakeReader([_FakePage(
        "Algebra darsligi. Bu kitobda tenglamalar, funksiyalar va grafiklar "
        "haqida batafsil ma'lumot beriladi. " * 10
    )])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "latin"


# ─── Per-page extraction exceptions are skipped, not raised ─────────────────

def test_page_extract_exception_is_skipped_not_raised(monkeypatch):
    reader = _FakeReader([
        _FakePage("", raises=True),
        _FakePage("Алгебра дарслиги учебник " * 10),
    ])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "cyrillic"


def test_all_pages_raise_yields_unknown(monkeypatch):
    reader = _FakeReader([_FakePage("", raises=True), _FakePage("", raises=True)])
    _patch_reader(monkeypatch, lambda: reader)
    assert detect_pdf_script(b"%PDF-1.4 x") == "unknown"


# ─── Encrypted/corrupt PDF: constructing the reader itself raises -> "unknown" ──

def test_encrypted_or_corrupt_pdf_never_raises(monkeypatch):
    def _factory():
        return _FakeReader([], raise_on_init=True)

    monkeypatch.setattr(pdf_lang, "PdfReader", lambda _bio: _factory())
    assert detect_pdf_script(b"not actually a pdf") == "unknown"


# ─── Bounded cost: only up to sample_pages pages are ever accessed ──────────

def test_bounded_to_sample_pages_on_a_large_book(monkeypatch):
    # A 600-page book must not be fully parsed — only the first
    # `sample_pages` (default 5) pages may ever be touched.
    pages = [_FakePage(f"Page {i} placeholder text") for i in range(600)]
    reader = _FakeReader(pages)
    _patch_reader(monkeypatch, lambda: reader)
    detect_pdf_script(b"%PDF-1.4 x")
    assert len(reader.accessed) <= 5
    assert max(reader.accessed) <= 4


def test_sample_pages_override_is_honored(monkeypatch):
    pages = [_FakePage(f"Page {i} placeholder text") for i in range(600)]
    reader = _FakeReader(pages)
    _patch_reader(monkeypatch, lambda: reader)
    detect_pdf_script(b"%PDF-1.4 x", sample_pages=2)
    assert len(reader.accessed) <= 2
