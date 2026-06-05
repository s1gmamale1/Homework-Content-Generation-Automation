import inspect

from app.config import settings
from app.services.agent import read_whole_book_text, extract_text_is_oversize


def test_read_whole_book_signature_and_budget_param():
    sig = inspect.signature(read_whole_book_text)
    assert "pdf_path" in sig.parameters
    src = inspect.getsource(read_whole_book_text)
    # reads a MARGIN past the budget so overflow is detectable (the blocker fix)
    assert "_EXTRACT_OVERSIZE_MARGIN" in src
    assert "extract_max_text_chars" in src
    assert "_read_pdf_pages" in src        # reuses the proven page reader
    assert "PdfReader" in src              # pypdf, not pdfplumber


def test_extract_text_is_oversize():
    # over-budget text → terminal "too large" path; normal text → proceeds
    assert extract_text_is_oversize("x" * (settings.extract_max_text_chars + 1)) is True
    assert extract_text_is_oversize("a normal short lesson text") is False
