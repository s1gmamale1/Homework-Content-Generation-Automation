"""Acceptance smoke for api-vision-1 — real Vertex multimodal call.

Proves the gemini api transport now accepts a PDF attachment and returns content
over Vertex (no GEMINI_API_KEY, no CLI/OAuth). Minimal tokens (one tiny 1-page
PDF + a one-word prompt) — NOT a homework generation. Run with:

    uv run python -m scripts.smoke_api_vision
"""
import asyncio
import tempfile
from pathlib import Path

from app.config import settings  # noqa: F401 — import triggers load_dotenv(.env)
from app.services import api_transport


def _one_page_pdf_with_word(word: str) -> Path:
    """Render a 1-page PDF whose visible text is `word` (reportlab → pypdf fallback)."""
    fd, name = tempfile.mkstemp(suffix=".pdf", prefix="apivision_smoke_")
    path = Path(name)
    try:
        from reportlab.pdfgen import canvas  # type: ignore
        from reportlab.lib.pagesizes import letter

        c = canvas.Canvas(str(path), pagesize=letter)
        c.setFont("Helvetica", 72)
        c.drawString(150, 400, word)
        c.showPage()
        c.save()
    except Exception:
        # No reportlab: a blank page still proves the multimodal call path; the
        # model just won't have a word to read (we only assert rc==0 + non-empty).
        import pypdf

        w = pypdf.PdfWriter()
        w.add_blank_page(width=300, height=300)
        with open(path, "wb") as f:
            w.write(f)
    return path


async def main() -> None:
    pdf = _one_page_pdf_with_word("BANANA")
    try:
        rc, text, usage, stderr = await api_transport._gemini(
            "gemini-2.5-flash",
            "This PDF page shows a single word. Reply with ONLY that word "
            "(or 'BLANK' if the page is empty).",
            attachments=[pdf],
        )
    finally:
        pdf.unlink(missing_ok=True)
    print(f"rc={rc!r}")
    print(f"text={text[:200]!r}")
    print(f"prompt_tokens={usage.get('prompt_tokens')} output_tokens={usage.get('output_tokens')}")
    print(f"stderr={stderr[:300]!r}")
    assert rc == 0, f"expected rc=0, got rc={rc} stderr={stderr[:300]!r}"
    assert text.strip(), "expected non-empty content from the multimodal Vertex call"
    print("SMOKE PASS: gemini api transport returned content for a PDF attachment over Vertex")


if __name__ == "__main__":
    asyncio.run(main())
