from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.services.agent import _toc_source_pdf


def _make_pdf(tmp_path: Path, n_pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    out = tmp_path / f"src_{n_pages}.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def test_toc_window_front_plus_back(tmp_path):
    src = _make_pdf(tmp_path, 40)
    out = _toc_source_pdf(src, 12, 20)
    assert out is not None
    try:
        reader = PdfReader(str(out))
        assert len(reader.pages) == 32  # 12 front + 20 back, no overlap
    finally:
        out.unlink()


def test_toc_window_dedupes_on_small_pdf(tmp_path):
    src = _make_pdf(tmp_path, 10)
    out = _toc_source_pdf(src, 12, 20)
    assert out is not None
    try:
        reader = PdfReader(str(out))
        assert len(reader.pages) == 10  # front+back cover everything, deduped
    finally:
        out.unlink()


def test_toc_window_none_on_zero(tmp_path):
    src = _make_pdf(tmp_path, 10)
    assert _toc_source_pdf(src, 0, 0) is None
