"""extract_toc over transport='api' (Vertex SDK, text-only):
- text-usable book → PDF attachment dropped, transport threaded as 'api'
- scanned/sparse book → still vision + the unconditional cli override holds
(api_transport.generate raises NotImplementedError on any attachment, so an
api text-usable call MUST send attachments=[].)"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from app.services import agent

_SPAWN_OK = (
    0,
    '{"entries": [{"chapter_number": "I", "chapter_title": "C", '
    '"section_number": "1", "section_title": "T", '
    '"page_start": 5, "page_end": 7}]}',
    {"prompt_tokens": 1, "output_tokens": 1, "cached_tokens": 0,
     "total_tokens": 2, "raw": {}},
    "",
)


def _make_pdf(tmp_path: Path, n_pages: int = 40) -> Path:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    out = tmp_path / "book.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def _patch(monkeypatch, captured: dict):
    async def _fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["attachments"] = list(attachments)
        captured["transport"] = transport
        captured["prompt"] = prompt
        return _SPAWN_OK

    async def _fake_record(*args, **kwargs):
        pass

    monkeypatch.setattr(agent, "_spawn", _fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", _fake_record)


@pytest.mark.asyncio
async def test_api_text_usable_drops_pdf_attachment(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    dense = "1-§ Kirish — 5\n2-§ Boshqa mavzu — 9\n" * 250  # not sparse
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: (dense, {"pages_read": 20, "chars": len(dense)}),
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    toc = await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="api",
    )

    assert len(toc.entries) == 1
    # api is text-only: the PDF must NOT be attached (else api_transport raises)
    assert captured["attachments"] == []
    # transport threaded through to the spawn
    assert captured["transport"] == "api"
    # the local TOC text still rides in as lesson_context
    assert "1-§ Kirish — 5" in captured["prompt"]


@pytest.mark.asyncio
async def test_cli_text_usable_still_attaches_pdf(tmp_path, monkeypatch):
    """Regression guard: the cli path is unchanged — gemini keep_pdf still attaches."""
    pdf = _make_pdf(tmp_path)
    dense = "1-§ Kirish — 5\n2-§ Boshqa mavzu — 9\n" * 250
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: (dense, {"pages_read": 20, "chars": len(dense)}),
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="cli",
    )

    assert captured["attachments"] == [pdf]
    assert captured["transport"] == "cli"


@pytest.mark.asyncio
async def test_api_scanned_book_gemini_keeps_api(tmp_path, monkeypatch):
    """A scanned/sparse book launched gemini+api now vision-OCRs over Vertex
    (api-vision-1): the window PDF rides as an attachment through the multimodal
    api path instead of force-downgrading to cli. (Was: forced cli.)"""
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),  # sparse junk
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="api",
    )

    # vision attaches a bounded window AND keeps api for gemini (routes over Vertex)
    assert len(captured["attachments"]) == 1
    assert Path(captured["attachments"][0]).name.startswith("toc_window_")
    assert captured["transport"] == "api"
