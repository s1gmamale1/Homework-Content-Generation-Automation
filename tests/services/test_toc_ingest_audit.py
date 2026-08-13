"""Ingest-time data-quality guards (`app.services.toc_ingest_audit`).

Pins the three decisions the module encodes, against the live evidence that
motivated them (a 254-lesson run; 8 inverted `toc_entries` rows of 9,634, 5 of
them exactly off-by-one; 12 lessons across 5 hosts failing on one scanned book):

  * an exactly-off-by-one inversion (`page_end == page_start - 1`, the shape of
    all three measured `cannot scope page range 35-34` failures) is REPAIRED to
    `page_end = page_start`, and the repair is reported, never silent;
  * a deeper inversion is SURFACED unrepaired;
  * a NULL page range is left completely alone;
  * a sparse text layer is FLAGGED but never blocks on its own.
"""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from app.config import settings
from app.services import toc_ingest_audit as audit
from app.services.agent import extract_text_is_too_sparse


# ── fixtures ────────────────────────────────────────────────────────────────


def _entry(title="Lesson", ps=None, pe=None):
    return SimpleNamespace(section_title=title, page_start=ps, page_end=pe)


def _blank_pdf(tmp_path: Path, n_pages: int = 30) -> Path:
    """An image-only / scanned book: real pages, zero text layer."""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    out = tmp_path / "scanned.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def _text_pdf(tmp_path: Path, n_pages: int = 30, per_page: int = 12) -> Path:
    """A normal book: every page carries a real, extractable text layer.

    Hand-built (no reportlab in the dependency set) — pypdf can only add BLANK
    pages, which is exactly the case we must be able to tell apart.
    """
    line = "Matematika darsligi mundarija sahifa matni. " * per_page
    stream = b"BT /F1 12 Tf 20 700 Td (" + line.encode("latin-1") + b") Tj ET"
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def add(num: int, body: bytes) -> None:
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n".encode())
        buf.write(body)
        buf.write(b"\nendobj\n")

    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n_pages))
    font_num = 3 + n_pages * 2
    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i in range(n_pages):
        page_num = 3 + i * 2
        add(
            page_num,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                f"<< /Font << /F1 {font_num} 0 R >> >> /Contents {page_num + 1} 0 R >>"
            ).encode(),
        )
        add(
            page_num + 1,
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        )
    add(font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref = buf.tell()
    buf.write(f"xref\n0 {font_num + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for num in range(1, font_num + 1):
        buf.write(f"{offsets.get(num, 0):010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {font_num + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    out = tmp_path / "textbook.pdf"
    out.write_bytes(buf.getvalue())
    return out


# ── page ranges: the off-by-one repair ──────────────────────────────────────


def test_off_by_one_inversion_is_repaired_to_single_page():
    """`35-34` — the exact shape of all three measured `cannot scope page
    range` failures — becomes `35-35`, a single-page lesson."""
    entries = [_entry("Chizmachilik 12-dars", 35, 34)]

    issues = audit.audit_page_ranges(entries)

    assert entries[0].page_end == 35, "off-by-one must be repaired to page_end = page_start"
    assert entries[0].page_start == 35, "page_start must never move"
    assert len(issues) == 1
    assert issues[0].kind == audit.OFF_BY_ONE
    assert issues[0].repaired_to == 35


def test_repair_is_never_a_swap():
    """Swapping would claim the lesson also covers page 34 — a page that
    belongs to the PREVIOUS lesson (the ROADMAP R27 failure mode: correct
    looking homework generated from the wrong source pages)."""
    entries = [_entry("L", 50, 49)]

    audit.audit_page_ranges(entries)

    assert (entries[0].page_start, entries[0].page_end) == (50, 50)


def test_off_by_one_repair_is_reported_not_silent():
    entries = [_entry("Ona tili 7-dars", 72, 71)]

    result = audit.audit_book(entries, Path("/nonexistent.pdf"))

    assert result.repairs, "a repair must be reported"
    assert "72-71" in result.detail and "72-72" in result.detail
    assert not result.blocking, "a repaired row must not block the book"


def test_repair_false_leaves_the_row_untouched():
    """The offline audit script reads production rows — it must not mutate."""
    entries = [_entry("L", 35, 34)]

    issues = audit.audit_page_ranges(entries, repair=False)

    assert (entries[0].page_start, entries[0].page_end) == (35, 34)
    assert issues[0].repaired_to is None


# ── page ranges: what is NOT repairable ─────────────────────────────────────


def test_deep_inversion_is_surfaced_not_guessed():
    entries = [_entry("Bob 3", 120, 44)]

    result = audit.audit_book(entries, Path("/nonexistent.pdf"))

    assert (entries[0].page_start, entries[0].page_end) == (120, 44), "must not be repaired"
    assert result.blocking, "an unrepairable inversion must block for review"
    assert "120-44" in result.detail


def test_nonpositive_page_numbers_are_surfaced():
    entries = [_entry("L", 0, 0)]

    issues = audit.audit_page_ranges(entries)

    assert [i.kind for i in issues] == [audit.NONPOSITIVE]
    assert issues[0].repaired_to is None


def test_wellformed_ranges_produce_nothing():
    entries = [_entry("A", 10, 12), _entry("B", 13, 13)]

    result = audit.audit_book(entries, Path("/nonexistent.pdf"))

    assert result.issues == []
    assert not result.has_findings
    assert result.detail is None


# ── NULL page ranges: legitimate, never touched ─────────────────────────────


def test_null_page_range_is_left_alone_and_never_blocks():
    """413 of 9,634 live rows carry a NULL range — chapter umbrellas,
    end-matter and the last entry (no successor to derive an end from). They
    are counted, never repaired, never rejected."""
    entries = [
        _entry("I BOB. GEOMETRIYA", None, None),
        _entry("Javoblar", 200, None),
        _entry("List of Irregular Verbs", 158, None),
        _entry("Normal lesson", 10, 12),
    ]

    result = audit.audit_book(entries, Path("/nonexistent.pdf"))

    assert [e.page_start for e in entries] == [None, 200, 158, 10]
    assert [e.page_end for e in entries] == [None, None, None, 12]
    assert result.issues == [], "a NULL bound is not a page-range defect"
    assert result.no_range_total == 3
    assert not result.blocking
    assert not result.has_findings


def test_null_range_on_a_readable_book_is_not_even_advisory(tmp_path):
    pdf = _text_pdf(tmp_path)
    entries = [_entry("Header", None, None), _entry("Lesson", 3, 4)]

    result = audit.audit_book(entries, pdf)

    assert result.no_range_total == 1
    assert not result.has_findings, "NULL ranges on a text-layer book are unremarkable"


# ── text layer ──────────────────────────────────────────────────────────────


def test_blank_pdf_probes_as_sparse(tmp_path):
    probe = audit.probe_text_layer(_blank_pdf(tmp_path, 30))

    assert probe.probed is True
    assert probe.total_pages == 30
    assert probe.sampled_pages > 0
    assert probe.chars_per_page == 0.0
    assert probe.is_sparse is True


def test_text_pdf_probes_as_dense(tmp_path):
    probe = audit.probe_text_layer(_text_pdf(tmp_path, 30))

    assert probe.probed is True
    assert probe.chars_per_page >= settings.extract_min_chars_per_page
    assert probe.is_sparse is False


def test_probe_is_bounded_on_a_large_book(tmp_path):
    """A 300-page scan costs 24 page reads, not 300 — the generation path's
    whole-book read was measured at ~106 s for a 352-page scan."""
    probe = audit.probe_text_layer(_blank_pdf(tmp_path, 300))

    assert probe.total_pages == 300
    assert probe.sampled_pages <= audit.SAMPLE_MAX_PAGES


def test_probe_samples_the_whole_book_not_just_the_front(tmp_path):
    """A text-bearing front over an image-only body must not read as dense —
    that masking bug is why `agent._toc_pages_scanned` exists."""
    indices = audit._sample_indices(300, audit.SAMPLE_MAX_PAGES)

    assert indices[0] == 0
    assert indices[-1] > 250, "the sample must reach the back of the book"


def test_unreadable_pdf_is_unknown_not_scanned(tmp_path):
    """An ingest guard must never be able to fail an ingestion, and 'cannot
    read' is not evidence of 'scanned'."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")

    probe = audit.probe_text_layer(broken)

    assert probe.probed is False
    assert probe.is_sparse is False


def test_missing_pdf_is_unknown_not_scanned():
    probe = audit.probe_text_layer(Path("/nonexistent.pdf"))

    assert probe.probed is False
    assert probe.is_sparse is False


def test_sparse_verdict_matches_the_generation_gate():
    """Drift guard: this module re-implements the comparison rather than
    importing agent (to stay dependency-light), so pin that the two agree on
    the same numbers — including the `1 chars/page` figure the 12 live
    failures reported."""
    for chars, pages in ((1 * 240, 240), (0, 55), (17_000, 240), (353_000, 192), (300 * 10, 10)):
        probe = audit.TextLayerProbe(
            probed=True, total_pages=pages, sampled_pages=pages, chars=chars
        )
        assert probe.is_sparse is extract_text_is_too_sparse("a" * chars, pages), (
            f"drifted from agent.extract_text_is_too_sparse at {chars} chars / {pages} pages"
        )


# ── the flag / block boundary ───────────────────────────────────────────────


def test_scanned_book_with_page_ranges_is_flagged_but_not_blocked(tmp_path):
    """A scanned book is a SUPPORTED book — the pipeline vision-attaches each
    lesson's page window. Flag it; never reject it."""
    pdf = _blank_pdf(tmp_path, 30)
    entries = [_entry("Dars 1", 5, 7), _entry("Dars 2", 8, 10)]

    result = audit.audit_book(entries, pdf)

    assert result.probe.is_sparse is True
    assert result.advisory, "the scanned book must be surfaced"
    assert not result.blocking, "a scanned book with page ranges must NOT block"
    assert "not rejected" in result.detail


def test_scanned_book_with_a_page_less_lesson_blocks(tmp_path):
    """The measured conjunction: `sparse text layer (1 chars/page) — likely
    scanned and no page range`, which cost 12 lessons across 5 hosts."""
    pdf = _blank_pdf(tmp_path, 30)
    entries = [_entry("Dars 1", 5, 7), _entry("Dars 2", None, None)]

    result = audit.audit_book(entries, pdf)

    assert result.no_range_lessons == 1
    assert result.blocking, "scanned + a page-less lesson cannot succeed anywhere"
    assert "no page range" in result.detail


def test_scanned_book_page_less_end_matter_does_not_block(tmp_path):
    """Only rows the launcher would actually launch count — `batch.py` defaults
    to `include_classes={"lesson"}`, so page-less end-matter is not a defect."""
    pdf = _blank_pdf(tmp_path, 30)
    entries = [
        _entry("Dars 1", 5, 7),
        _entry("Mundarija", None, None),
        _entry("Javoblar", None, None),
        _entry("Тестовые задания", None, None),
    ]

    result = audit.audit_book(entries, pdf)

    assert result.no_range_total == 3
    assert result.no_range_lessons == 0
    assert not result.blocking
    assert result.advisory


def test_detail_names_offending_rows_but_stays_bounded():
    entries = [_entry(f"Lesson {i}", 100 + i, 10) for i in range(40)]

    result = audit.audit_book(entries, Path("/nonexistent.pdf"))

    assert len(result.issues) == 40
    assert "more)" in result.detail, "the detail must summarise the tail, not dump 40 rows"
    assert len(result.detail) <= audit.MAX_DETAIL_CHARS


def test_summary_is_loggable_even_when_clean():
    result = audit.audit_book([_entry("L", 1, 2)], Path("/nonexistent.pdf"))

    assert "entries=1" in result.summary
    assert "no_page_range=0" in result.summary


def test_partial_entry_objects_do_not_crash():
    """The audit accepts anything TOC-shaped — `TOCEntryExtracted`, ORM rows,
    or a namespace with only a title (as the extractor's own tests build)."""
    result = audit.audit_book([SimpleNamespace(section_title="Bare")], Path("/nonexistent.pdf"))

    assert result.entries_total == 1
    assert result.no_range_total == 1
    assert not result.blocking
