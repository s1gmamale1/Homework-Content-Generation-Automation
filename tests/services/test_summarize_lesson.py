import inspect
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import agent
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


@pytest.mark.asyncio
async def test_summarize_lesson_appends_correction_hint(monkeypatch):
    captured = {}

    async def fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["prompt"] = prompt
        return 0, "OK summary text " * 40, {"prompt_tokens": 1, "output_tokens": 1, "cached_tokens": 0, "total_tokens": 2, "raw": {}}, ""

    monkeypatch.setattr(agent, "_spawn", fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", AsyncMock())
    await agent.summarize_lesson(
        provider="gemini", model="gemini-2.5-flash", book_text="book",
        section_title="T", section_number="1", page_start=1, page_end=2,
        homework_job_id=uuid4(), phase_output_id=uuid4(), transport="api",
        correction_hint="extract says -3/(2a); source has -3/a",
    )
    assert "-3/a" in captured["prompt"] and "correct" in captured["prompt"].lower()
