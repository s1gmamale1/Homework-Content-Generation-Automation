"""Task 3 — scanned-PDF vision dispatch in ``pipeline._execute_phase`` (extract).

When the whole-book text fails Gate A (``validate_extract_text`` — a scanned /
no-text-layer PDF), the extract branch must route to
``agent.summarize_lesson_vision`` instead of raising, scoping the lesson by its
page window. The normal whole-text path (Gate A passes) must be unchanged.

These drive the REAL ``pipeline._execute_phase`` with DB-free mocks (mirroring
``test_execute_phase_api_auth.py``), monkeypatching only the agent boundary so
the actual dispatch branch is exercised — not a copy of it.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest

from app.services import pipeline


# --- DB-free harness (mirrors test_execute_phase_api_auth.py) ----------------

class _FakePhaseRow:
    def __init__(self):
        self.id = uuid4()


class _FakeSession:
    async def commit(self):
        return None


@asynccontextmanager
async def _fake_session():
    yield _FakeSession()


def _install_harness(monkeypatch):
    monkeypatch.setattr(pipeline, "SessionLocal", _fake_session)

    async def _create_or_reset(session, **kwargs):
        return _FakePhaseRow()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", _create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract", _noop)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", _noop)

    async def _no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", _no_sleep)


# A long, clean realistic paragraph that passes Gate A and isn't oversize.
_CLEAN_TEXT = (
    "Hujayra tirik organizmlarning eng kichik tuzilish va funksional birligidir. "
    "Har bir hujayra membrana, sitoplazma va yadrodan tashkil topgan. "
    "Yadro irsiy axborotni saqlaydi va hujayra faoliyatini boshqaradi. "
    "Mitoxondriyalar energiya ishlab chiqaradi, ribosomalar oqsil sintez qiladi. "
) * 8

_VISION_SUMMARY = (
    "A real vision lesson summary long enough to pass Gate B validation easily."
)


def _run_extract_phase(
    monkeypatch,
    *,
    page_start: Optional[int] = 1,
    page_end: Optional[int] = 3,
    extract_transport: str = "cli",
):
    return asyncio.run(pipeline._execute_phase(
        job_id=uuid4(),
        phase_name="extract",
        phase_order=1,
        subject="biology",
        provider="claude",
        model="claude-sonnet-4-6",
        pdf_path=Path("/tmp/does-not-matter.pdf"),
        attach_file=False,
        section={"id": None, "title": "T", "number": "1",
                 "page_start": page_start, "page_end": page_end},
        lesson_context=None,
        prior_outputs={},
        difficulty=None,
        transport="cli",
        extract_transport=extract_transport,
        judge_transport="cli",
        extract_provider="gemini",
        extract_model="gemini-2.5-flash",
    ))


def _install_agent_spies(monkeypatch, *, book_text: str):
    """Wire the agent boundary; return a dict of call counters."""
    calls = {"vision": 0, "normal": 0, "vision_kwargs": None}

    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda path: book_text)

    async def _vision(**kwargs):
        calls["vision"] += 1
        calls["vision_kwargs"] = kwargs
        return (_VISION_SUMMARY, 10, 20)

    async def _normal(**kwargs):
        calls["normal"] += 1
        return ("A normal whole-text lesson summary passing Gate B validation.", 5, 7)

    monkeypatch.setattr(pipeline.agent, "summarize_lesson_vision", _vision)
    monkeypatch.setattr(pipeline.agent, "summarize_lesson", _normal)
    # Gate B is exercised by Task-2 tests; isolate the DISPATCH branch here so a
    # short canned summary doesn't trip the ≥400-char floor.
    monkeypatch.setattr(pipeline.agent, "validate_extract_summary", lambda out: None)
    return calls


# --- tests -------------------------------------------------------------------

def test_scanned_book_routes_to_vision(monkeypatch):
    """Gate A fails (short/unreadable text) + page range set → vision called
    once, normal whole-text extract NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text="x" * 40)

    out_md, tin, tout, prompt_hash, parsed = _run_extract_phase(monkeypatch)

    assert calls["vision"] == 1
    assert calls["normal"] == 0
    assert out_md == _VISION_SUMMARY
    assert parsed is None


def test_scanned_book_no_pages_fails_loud(monkeypatch):
    """Gate A fails AND no page range → RuntimeError; vision NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text="x" * 40)

    with pytest.raises(RuntimeError):
        _run_extract_phase(monkeypatch, page_start=None, page_end=None)

    assert calls["vision"] == 0


def test_normal_book_unchanged(monkeypatch):
    """Gate A passes (long clean text, not oversize) → normal whole-text path
    runs (summarize_lesson called), vision NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text=_CLEAN_TEXT)

    out_md, tin, tout, prompt_hash, parsed = _run_extract_phase(monkeypatch)

    assert calls["normal"] == 1
    assert calls["vision"] == 0
    assert parsed is None
