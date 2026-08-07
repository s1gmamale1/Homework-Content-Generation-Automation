# tests/services/test_pipeline_extract_coverage.py
"""Extract-completeness check — pipeline side (source scoping + wiring).

Wiring tests drive the REAL ``pipeline._execute_phase`` with DB-free mocks
(same harness idiom as test_pipeline_extract_dispatch.py), so the actual branch
is exercised rather than a copy of it.
"""
import asyncio
from pathlib import Path

import pytest

from app.services import pipeline


def test_lesson_source_returns_page_scoped_text(monkeypatch):
    seen = {}

    def _page_range(path, ps, pe, *, margin=0):
        seen["args"] = (ps, pe, margin)
        return "L" * 4000

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _page_range)
    out = asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"title": "T", "number": "1", "page_start": 51, "page_end": 62}))
    assert out == "L" * 4000
    # ±1 page — the same window the CQ-D verify call uses.
    assert seen["args"] == (51, 62, 1)


def test_lesson_source_is_none_without_a_page_range(monkeypatch):
    called = {"n": 0}

    def _page_range(path, ps, pe, *, margin=0):
        called["n"] += 1
        return "text"

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _page_range)
    for section in ({"page_start": None, "page_end": 62}, {"page_start": 51, "page_end": None}, {}):
        assert asyncio.run(pipeline._lesson_source_or_none(Path("/tmp/x.pdf"), section)) is None
    # NO whole-book fallback: a whole-book source would make the checker report
    # every OTHER lesson's items as omissions.
    assert called["n"] == 0


def test_lesson_source_is_none_when_text_layer_is_unusable(monkeypatch):
    # Scanned / garbled window → Gate A rejects it → skip the check entirely.
    monkeypatch.setattr(pipeline.agent, "read_page_range_text",
                        lambda path, ps, pe, *, margin=0: "x" * 40)
    assert asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"page_start": 1, "page_end": 3})) is None


def test_lesson_source_is_none_on_read_error(monkeypatch):
    def _boom(path, ps, pe, *, margin=0):
        raise OSError("pdf read failed")

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _boom)
    assert asyncio.run(pipeline._lesson_source_or_none(
        Path("/tmp/x.pdf"), {"page_start": 1, "page_end": 3})) is None
