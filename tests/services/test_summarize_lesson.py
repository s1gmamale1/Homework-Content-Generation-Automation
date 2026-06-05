import inspect

from app.services.agent import summarize_lesson


def test_summarize_lesson_shape():
    sig = inspect.signature(summarize_lesson)
    for p in ("provider", "model", "book_text", "section_title", "section_number",
              "page_start", "page_end", "homework_job_id", "phase_output_id"):
        assert p in sig.parameters, p
    src = inspect.getsource(summarize_lesson)
    assert "attachments=[]" in src            # NO PDF attached — text is injected
    assert "book_text" in src                 # injects the local text
    assert "locate" in src.lower() or "find" in src.lower()  # locate-by-title prompt
