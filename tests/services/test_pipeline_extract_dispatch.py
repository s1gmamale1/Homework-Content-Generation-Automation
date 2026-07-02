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


def _install_agent_spies(
    monkeypatch, *, book_text: str, page_range_text: Optional[str] = None, n_pages: int = 2
):
    """Wire the agent boundary; return a dict of call counters.

    ``page_range_text`` (if given) is what ``read_page_range_text`` returns when
    the whole-book text is oversize and the extract scopes down to the lesson.
    ``n_pages`` is the physical page count the dispatch sees (default 2, dense
    enough that the density gate doesn't fire for the realistic clean texts).
    ``extract_text_is_too_sparse`` is left REAL — it's the pure fn under test.
    """
    calls = {"vision": 0, "normal": 0, "page_range": 0,
             "vision_kwargs": None, "normal_kwargs": None}

    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda path: book_text)
    monkeypatch.setattr(pipeline.agent, "pdf_page_count", lambda path: n_pages)

    def _page_range(path, ps, pe, *, margin=0):
        calls["page_range"] += 1
        return page_range_text if page_range_text is not None else book_text

    monkeypatch.setattr(pipeline.agent, "read_page_range_text", _page_range)

    async def _vision(**kwargs):
        calls["vision"] += 1
        calls["vision_kwargs"] = kwargs
        return (_VISION_SUMMARY, 10, 20)

    async def _normal(**kwargs):
        calls["normal"] += 1
        calls["normal_kwargs"] = kwargs
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


def test_sparse_scanned_routes_to_vision(monkeypatch):
    """Gate A PASSES (17000 chars of letters > 500-char floor, printable) but the
    per-page density is ≈71 chars/page (240 pages) → the density gate flags it as
    scanned and routes to vision; the normal whole-text path is NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text="h" * 17000, n_pages=240)

    out_md, tin, tout, prompt_hash, parsed = _run_extract_phase(monkeypatch)

    assert calls["vision"] == 1
    assert calls["normal"] == 0
    assert out_md == _VISION_SUMMARY
    assert parsed is None


# --- Task 5: oversize whole-book → scope to lesson page-range TEXT -----------

def test_oversize_book_subsets_text(monkeypatch):
    """Whole-book text is oversize but the lesson page-range text is clean and
    in-budget → read_page_range_text is called, the NORMAL whole-text path runs
    on the subset, vision is NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(
        monkeypatch, book_text="a" * 700_000, page_range_text=_CLEAN_TEXT
    )

    out_md, tin, tout, prompt_hash, parsed = _run_extract_phase(monkeypatch)

    assert calls["page_range"] == 1
    assert calls["normal"] == 1
    assert calls["vision"] == 0
    assert parsed is None
    # The subset text (not the oversize whole book) is what got summarized.
    assert calls["normal_kwargs"]["book_text"] == _CLEAN_TEXT


def test_oversize_no_pages_fails_loud(monkeypatch):
    """Whole-book text oversize AND no page range to scope a subset →
    RuntimeError; read_page_range_text NOT called."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(
        monkeypatch, book_text="a" * 700_000, page_range_text=_CLEAN_TEXT
    )

    with pytest.raises(RuntimeError):
        _run_extract_phase(monkeypatch, page_start=None, page_end=None)

    assert calls["page_range"] == 0


def test_oversize_subset_still_oversize_fails(monkeypatch):
    """Whole-book oversize and the scoped page-range text is STILL oversize →
    RuntimeError mentioning 'still too large'."""
    _install_harness(monkeypatch)
    _install_agent_spies(
        monkeypatch, book_text="a" * 700_000, page_range_text="a" * 700_000
    )

    with pytest.raises(RuntimeError, match="still too large"):
        _run_extract_phase(monkeypatch)


# --- Task 3: vision transport routing for scanned PDFs -----------------------


def _run_extract_phase_for_provider(
    monkeypatch,
    *,
    page_start: Optional[int] = 1,
    page_end: Optional[int] = 3,
    extract_transport: str = "cli",
    extract_provider: str = "gemini",
    extract_model: Optional[str] = "gemini-2.5-flash",
):
    """Like _run_extract_phase but exposes extract_provider so transport tests
    can vary the provider without touching the existing tests."""
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
        extract_provider=extract_provider,
        extract_model=extract_model,
    ))


def test_scanned_gemini_api_passes_vision_transport_api(monkeypatch):
    """Scanned PDF with extract_provider=gemini + extract_transport=api →
    summarize_lesson_vision is called with transport='api' (Vertex)."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text="x" * 40)

    _run_extract_phase_for_provider(
        monkeypatch, extract_transport="api", extract_provider="gemini"
    )

    assert calls["vision"] == 1
    assert calls["vision_kwargs"]["transport"] == "api"


def test_scanned_claude_api_forces_vision_transport_cli(monkeypatch):
    """Scanned PDF with extract_provider=claude + extract_transport=api →
    summarize_lesson_vision is called with transport='cli'
    (only gemini can attach PDFs over api; claude must fall back to cli)."""
    _install_harness(monkeypatch)
    calls = _install_agent_spies(monkeypatch, book_text="x" * 40)

    _run_extract_phase_for_provider(
        monkeypatch, extract_transport="api", extract_provider="claude"
    )

    assert calls["vision"] == 1
    assert calls["vision_kwargs"]["transport"] == "cli"


# --- Task 6: item-1 verify+regen-once fidelity guard -------------------------

from unittest.mock import AsyncMock
from app.services import agent as agent_mod  # noqa: F401

_SECTION = {"title": "T", "number": "1", "page_start": 1, "page_end": 2}
_GOOD = "Ishlangan misol natija −3/a bo‘ladi. " * 30
_DRIFT = "Ishlangan misol natija −3/(2a) bo‘ladi. " * 30
_BOOK = "Manba matni: qisqartirish natijasi −3/a. " * 40   # grounds -3/a, NOT -3/(2a)


@pytest.mark.asyncio
async def test_regens_once_on_confirmed_drift(monkeypatch):
    calls = {"n": 0}

    async def fake_summarize(*, correction_hint="", **kw):
        calls["n"] += 1
        return (_GOOD if correction_hint else _DRIFT), 2, 3

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", fake_summarize)
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity",
                        AsyncMock(return_value=["summary says -3/(2a); source has -3/a"]))
    # R2: verify source is lesson-scoped; monkeypatch the page read to the section text.
    monkeypatch.setattr(pipeline.agent, "read_page_range_text", lambda *a, **k: _BOOK)
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_DRIFT, book_text=_BOOK, pdf_path="x.pdf", prov="gemini",
        mdl="gemini-2.5-flash", transport="api", section=_SECTION, job_id=None, po_id=None,
    )
    assert calls["n"] == 1                     # exactly one regen
    # NOTE: fixtures use U+2212 (unicode minus) — match it, not ASCII '-'.
    assert "−3/(2a)" not in out and "−3/a" in out
    assert (xin, xout) == (2, 3)               # regen tokens billed


@pytest.mark.asyncio
async def test_verify_source_is_lesson_scoped(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline.agent, "read_page_range_text",
                        lambda pdf, ps, pe, **k: f"SCOPED[{ps}-{pe}]")

    async def fake_verify(*, book_text, **kw):
        seen["source"] = book_text
        return []

    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", fake_verify)
    await pipeline._verify_and_maybe_regen_extract(
        out=_DRIFT, book_text=_BOOK, pdf_path="x.pdf", prov="gemini", mdl=None,
        transport="api", section=_SECTION, job_id=None, po_id=None,
    )
    assert seen["source"] == "SCOPED[1-2]"     # bounded to the section pages, not whole book


@pytest.mark.asyncio
async def test_no_verify_call_when_no_candidates(monkeypatch):
    spy = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", spy)
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_GOOD, book_text=_BOOK, pdf_path="x.pdf", prov="gemini", mdl=None,
        transport="api", section=_SECTION, job_id=None, po_id=None,
    )
    assert out == _GOOD and (xin, xout) == (0, 0)
    spy.assert_not_called()                    # -3/a is grounded → no paid call


@pytest.mark.asyncio
async def test_no_regen_when_verify_clean(monkeypatch):
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", AsyncMock(return_value=[]))
    monkeypatch.setattr(pipeline.agent, "read_page_range_text", lambda *a, **k: _BOOK)
    called = {"n": 0}

    async def fake_summarize(**kw):
        called["n"] += 1
        return _GOOD, 1, 1

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", fake_summarize)
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_DRIFT, book_text=_BOOK, pdf_path="x.pdf", prov="gemini", mdl=None,
        transport="api", section=_SECTION, job_id=None, po_id=None,
    )
    assert out == _DRIFT and called["n"] == 0 and (xin, xout) == (0, 0)
