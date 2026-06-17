"""Forced-cli VISION extract for scanned PDFs (``summarize_lesson_vision``).

When the whole-book local-text path can't read a PDF (scanned / broken font),
extract falls back to attaching the section's page window as a small PDF and
asking the model to read it visually. Vision is ALWAYS cli (no api PDF path),
so this function has no ``transport`` param and hardcodes ``transport="cli"``.

These tests mock ``agent._spawn`` (no real CLI) and ``agent._record_usage`` (no
real DB) while building a real multi-page PDF so ``_subset_pdf`` produces a real
temp file we can assert is cleaned up.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pypdf = pytest.importorskip("pypdf")

from app.services import agent  # noqa: E402

_FAKE_TEXT = (
    "This lesson covers photosynthesis in detail, including the light reactions "
    "and the Calvin cycle, with enough text to pass any length gate."
)
_FAKE_USAGE = {
    "prompt_tokens": 1234,
    "output_tokens": 567,
    "cached_tokens": 0,
    "total_tokens": 1801,
    "raw": {},
}


def _make_pdf(path: Path, n_pages: int) -> None:
    writer = pypdf.PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture()
def book_pdf(tmp_path: Path) -> Path:
    src = tmp_path / "book.pdf"
    _make_pdf(src, 30)
    return src


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(**kw):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(agent, "_record_usage", _noop)


def _install_spawn(monkeypatch: pytest.MonkeyPatch, rc: int = 0):
    captured: dict = {"called": False}

    async def fake_spawn(**kw):  # noqa: ANN001, ANN202
        captured["called"] = True
        captured.update(kw)
        # Snapshot whether the attachment exists at call time (before cleanup).
        atts = kw.get("attachments") or []
        captured["attachments_exist_at_call"] = [Path(p).exists() for p in atts]
        return (rc, _FAKE_TEXT, _FAKE_USAGE, "")

    monkeypatch.setattr(agent, "_spawn", fake_spawn)
    return captured


@pytest.mark.asyncio
async def test_vision_forces_cli_transport(
    monkeypatch: pytest.MonkeyPatch, book_pdf: Path
) -> None:
    captured = _install_spawn(monkeypatch)
    await agent.summarize_lesson_vision(
        provider="gemini",
        model=None,
        pdf_path=book_pdf,
        section_title="Photosynthesis",
        section_number="3.1",
        page_start=10,
        page_end=12,
        homework_job_id=uuid4(),
        phase_output_id=uuid4(),
    )
    assert captured["transport"] == "cli"


@pytest.mark.asyncio
async def test_vision_attaches_window_pdf(
    monkeypatch: pytest.MonkeyPatch, book_pdf: Path
) -> None:
    captured = _install_spawn(monkeypatch)
    await agent.summarize_lesson_vision(
        provider="gemini",
        model=None,
        pdf_path=book_pdf,
        section_title="Photosynthesis",
        section_number="3.1",
        page_start=10,
        page_end=12,
        homework_job_id=uuid4(),
        phase_output_id=uuid4(),
    )
    atts = captured["attachments"]
    assert isinstance(atts, list) and len(atts) == 1
    window = Path(atts[0])
    assert window.suffix == ".pdf"
    # It existed at call time (the spawn fake snapshotted this) ...
    assert captured["attachments_exist_at_call"] == [True]
    # ... and the window PDF is NOT the source book.
    assert window != book_pdf
    # ... and it was unlinked after the call.
    assert not window.exists()
    # The source book must survive.
    assert book_pdf.exists()


@pytest.mark.asyncio
async def test_vision_fails_loud_without_pages(
    monkeypatch: pytest.MonkeyPatch, book_pdf: Path
) -> None:
    captured = _install_spawn(monkeypatch)
    with pytest.raises(RuntimeError):
        await agent.summarize_lesson_vision(
            provider="gemini",
            model=None,
            pdf_path=book_pdf,
            section_title="Photosynthesis",
            section_number="3.1",
            page_start=None,  # type: ignore[arg-type]
            page_end=12,
            homework_job_id=uuid4(),
            phase_output_id=uuid4(),
        )
    assert captured["called"] is False


@pytest.mark.asyncio
async def test_vision_returns_token_shape(
    monkeypatch: pytest.MonkeyPatch, book_pdf: Path
) -> None:
    _install_spawn(monkeypatch)
    result = await agent.summarize_lesson_vision(
        provider="gemini",
        model=None,
        pdf_path=book_pdf,
        section_title="Photosynthesis",
        section_number="3.1",
        page_start=10,
        page_end=12,
        homework_job_id=uuid4(),
        phase_output_id=uuid4(),
    )
    assert result == (_FAKE_TEXT, 1234, 567)
