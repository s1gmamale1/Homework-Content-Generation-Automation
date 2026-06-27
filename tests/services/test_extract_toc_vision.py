"""Task 2: vision-attach a bounded front+back window when the TOC source text
is unusable (empty OR too sparse — a scanned / watermark book)."""

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
    {
        "prompt_tokens": 1,
        "output_tokens": 1,
        "cached_tokens": 0,
        "total_tokens": 2,
        "raw": {},
    },
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


def _patch_spawn(monkeypatch, captured: dict):
    async def _fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["attachments"] = list(attachments)
        captured["prompt"] = prompt
        captured["attach_exists"] = [Path(a).exists() for a in attachments]
        return _SPAWN_OK

    monkeypatch.setattr(agent, "_spawn", _fake_spawn)


def _patch_record_usage(monkeypatch, captured: dict):
    async def _fake_record(*args, **kwargs):
        if kwargs.get("success"):
            captured["extra_envelope"] = kwargs.get("extra_envelope")

    monkeypatch.setattr(agent, "_record_usage", _fake_record)


@pytest.mark.asyncio
async def test_sparse_text_routes_to_vision_window(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(
        agent,
        "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),
    )
    captured: dict = {}
    _patch_spawn(monkeypatch, captured)
    _patch_record_usage(monkeypatch, {})

    toc = await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
    )

    assert len(toc.entries) == 1
    assert len(captured["attachments"]) == 1
    win = Path(captured["attachments"][0])
    assert win.name.startswith("toc_window_")
    # existed at spawn time...
    assert captured["attach_exists"] == [True]
    # ...and is unlinked after the call (finally cleanup)
    assert not win.exists()
    # the junk text excerpt was dropped, not injected into the prompt
    assert "@WM" not in captured["prompt"]


@pytest.mark.asyncio
async def test_dense_text_keeps_text_path(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    # >300 chars/page over 20 pages so extract_text_is_too_sparse() is False
    dense = "1-§ Kirish — 5\n2-§ Boshqa mavzu — 9\n" * 250
    assert len(dense) / 20 >= 300
    monkeypatch.setattr(
        agent,
        "_extract_toc_source_text",
        lambda p: (dense, {"pages_read": 20, "chars": len(dense)}),
    )
    captured: dict = {}
    _patch_spawn(monkeypatch, captured)
    _patch_record_usage(monkeypatch, {})

    toc = await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
    )

    assert len(toc.entries) == 1
    # text path: the excerpt is in the prompt
    assert "1-§ Kirish — 5" in captured["prompt"]
    # gemini keep_pdf: the real PDF is attached, not a window temp
    assert captured["attachments"] == [pdf]


@pytest.mark.asyncio
async def test_window_none_falls_back_clean(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(
        agent,
        "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),
    )
    monkeypatch.setattr(agent, "_toc_source_pdf", lambda *a, **k: None)
    captured: dict = {}
    _patch_spawn(monkeypatch, captured)
    _patch_record_usage(monkeypatch, {})

    toc = await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
    )

    assert len(toc.entries) == 1
    assert captured["attachments"] == []


@pytest.mark.asyncio
async def test_vision_branch_marks_source(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(
        agent,
        "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),
    )
    _patch_spawn(monkeypatch, {})
    rec: dict = {}
    _patch_record_usage(monkeypatch, rec)

    await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
    )

    assert rec["extra_envelope"]["source"] == "vision_toc"


# ── Task 2: transport routing in the vision branch ────────────────────────────

def _patch_vision_for_transport(monkeypatch, tmp_path: Path, captured: dict):
    """Shared setup: sparse text → vision branch, fake window, capture transport."""
    window = tmp_path / "toc_window_fake.pdf"
    window.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        agent,
        "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),
    )
    monkeypatch.setattr(agent, "_toc_source_pdf", lambda *a, **k: window)

    async def _fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["transport"] = transport
        captured["attachments"] = list(attachments)
        return _SPAWN_OK

    monkeypatch.setattr(agent, "_spawn", _fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", lambda *a, **k: _noop())

    return window


async def _noop():
    return None


@pytest.mark.asyncio
async def test_extract_toc_vision_keeps_api_for_gemini(tmp_path, monkeypatch):
    """gemini + api: vision branch keeps transport='api' so the window routes via Vertex."""
    pdf = _make_pdf(tmp_path)
    captured: dict = {}
    window = _patch_vision_for_transport(monkeypatch, tmp_path, captured)

    await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
        transport="api",
    )

    assert captured["transport"] == "api"
    assert captured["attachments"] == [window]


@pytest.mark.asyncio
async def test_extract_toc_vision_forces_cli_for_claude(tmp_path, monkeypatch):
    """claude + api: vision branch must still force transport='cli' (no multimodal api for claude)."""
    pdf = _make_pdf(tmp_path)
    captured: dict = {}
    _patch_vision_for_transport(monkeypatch, tmp_path, captured)

    await agent.extract_toc(
        provider="claude",
        model="claude-sonnet-4-5",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
        transport="api",
    )

    assert captured["transport"] == "cli"


@pytest.mark.asyncio
async def test_extract_toc_vision_forces_cli_when_cli(tmp_path, monkeypatch):
    """gemini + cli: vision branch keeps transport='cli' (no change; already cli)."""
    pdf = _make_pdf(tmp_path)
    captured: dict = {}
    _patch_vision_for_transport(monkeypatch, tmp_path, captured)

    await agent.extract_toc(
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=pdf,
        subject="math",
        book_id=uuid4(),
        transport="cli",
    )

    assert captured["transport"] == "cli"
