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


# --- wiring on the real _execute_phase ---------------------------------------

from contextlib import asynccontextmanager
from uuid import uuid4

from app.config import settings
from app.services import agent as agent_mod


class _FakePhaseRow:
    def __init__(self):
        self.id = uuid4()


class _FakeSession:
    async def commit(self):
        return None


@asynccontextmanager
async def _fake_session():
    yield _FakeSession()


_CLEAN_TEXT = (
    "Hujayra tirik organizmlarning eng kichik tuzilish va funksional birligidir. "
    "Har bir hujayra membrana, sitoplazma va yadrodan tashkil topgan. "
    "Yadro irsiy axborotni saqlaydi va hujayra faoliyatini boshqaradi. "
) * 12


def _install_harness(monkeypatch, *, cached_extract=None):
    """DB-free harness; captures every set_status write so the test can assert
    what landed in validation_warnings on the done-write."""
    writes = []
    monkeypatch.setattr(pipeline, "SessionLocal", _fake_session)

    async def _create_or_reset(session, **kwargs):
        return _FakePhaseRow()

    async def _set_status(session, po_id, status, **kwargs):
        writes.append((status, kwargs))
        return None

    async def _noop(*args, **kwargs):
        return None

    async def _find_latest_extract(session, **kwargs):
        return cached_extract

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", _create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", _set_status)
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract", _find_latest_extract)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda path: _CLEAN_TEXT)
    monkeypatch.setattr(pipeline.agent, "pdf_page_count", lambda path: 2)
    monkeypatch.setattr(pipeline.agent, "read_page_range_text",
                        lambda path, ps, pe, *, margin=0: _CLEAN_TEXT)
    monkeypatch.setattr(pipeline.agent, "validate_extract_summary", lambda out: None)

    async def _normal(**kwargs):
        return ("A normal whole-text lesson summary passing Gate B validation.", 5, 7)

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", _normal)
    # The suite defaults the check OFF (tests/conftest.py) so no OTHER test can
    # reach a real spawn through the new call site. These tests are the ones
    # that exercise it, so they turn it back on explicitly.
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", True)
    return writes


def _install_coverage_spy(monkeypatch, *, misses=(), boom=None):
    calls = {"n": 0, "kwargs": None}

    async def _check(**kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        if boom is not None:
            raise boom
        return list(misses)

    monkeypatch.setattr(pipeline.agent, "check_extract_coverage", _check)
    return calls


def _run_extract_phase(**overrides):
    kwargs = dict(
        job_id=uuid4(), phase_name="extract", phase_order=1, subject="biology",
        provider="claude", model="claude-sonnet-4-6",
        pdf_path=Path("/tmp/does-not-matter.pdf"), attach_file=False,
        section={"id": None, "title": "Zamburug'lar", "number": "6",
                 "page_start": 19, "page_end": 24},
        lesson_context=None, prior_outputs={}, difficulty=None,
        transport="api", extract_transport="api", judge_transport="api",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
    )
    kwargs.update(overrides)
    return asyncio.run(pipeline._execute_phase(**kwargs))


def _done_warnings(writes):
    return next(kw.get("validation_warnings") for status, kw in writes if status == "done")


def test_coverage_warning_lands_on_the_extract_done_write(monkeypatch):
    writes = _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="Ektotrof mikoriza", central=True),
    ])
    _run_extract_phase()
    assert calls["n"] == 1
    warnings = _done_warnings(writes)
    assert warnings and warnings[0].startswith("extract_coverage:")
    assert "Ektotrof mikoriza" in warnings[0]
    # the pinned extract role serves the check, over the extract's transport
    assert calls["kwargs"]["provider"] == "gemini"
    assert calls["kwargs"]["model"] == "gemini-3.5-flash-lite"
    assert calls["kwargs"]["transport"] == "api"
    assert calls["kwargs"]["section_title"] == "Zamburug'lar"


def test_clean_extract_writes_no_warnings(monkeypatch):
    writes = _install_harness(monkeypatch)
    _install_coverage_spy(monkeypatch, misses=[])
    _run_extract_phase()
    assert _done_warnings(writes) is None


def test_kill_switch_makes_no_call(monkeypatch):
    # Order matters: _install_harness ENABLES the check (the suite defaults it
    # off), so the disable must come AFTER it or the harness wins and this test
    # fails against a correct implementation.
    writes = _install_harness(monkeypatch)
    monkeypatch.setattr(settings, "extract_coverage_check_enabled", False)
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="x", central=True)])
    _run_extract_phase()
    assert calls["n"] == 0
    assert _done_warnings(writes) is None


def test_model_override_wins_over_the_extract_role_model(monkeypatch):
    monkeypatch.setattr(settings, "extract_coverage_model", "gemini-3.5-flash")
    _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[])
    _run_extract_phase()
    assert calls["kwargs"]["model"] == "gemini-3.5-flash"


def test_reused_extract_never_pays_for_the_check(monkeypatch):
    class _Cached:
        id = uuid4()
        job_id = uuid4()
        output_md = "A previously produced extract summary, reused verbatim."

    writes = _install_harness(monkeypatch, cached_extract=_Cached())
    calls = _install_coverage_spy(monkeypatch, misses=[
        agent_mod.ExtractCoverageMiss(label="x", central=True)])

    async def _record(**kwargs):
        return None

    monkeypatch.setattr(pipeline.agent, "record_cached_lesson_extract", _record)
    monkeypatch.setattr(pipeline.agent, "summarize_lesson",
                        lambda **kw: pytest.fail("cached path must not re-extract"))
    out_md, tin, tout, _ph, _ps = _run_extract_phase(
        section={"id": uuid4(), "title": "T", "number": "1",
                 "page_start": 19, "page_end": 24})
    assert out_md == _Cached.output_md
    # the producing job already ran the check — re-running it would re-bill the
    # same lesson on every repeat job for that section.
    assert calls["n"] == 0


def test_no_page_range_skips_the_check_without_failing_the_phase(monkeypatch):
    writes = _install_harness(monkeypatch)
    calls = _install_coverage_spy(monkeypatch, misses=[])
    out_md, *_ = _run_extract_phase(
        section={"id": None, "title": "T", "number": "1",
                 "page_start": None, "page_end": None})
    assert calls["n"] == 0
    assert out_md            # the extract itself still completed
    assert _done_warnings(writes) is None


def test_check_failure_is_fail_open_and_the_phase_still_completes(monkeypatch):
    writes = _install_harness(monkeypatch)
    _install_coverage_spy(monkeypatch, boom=RuntimeError("verdict blew up"))
    out_md, *_ = _run_extract_phase()
    assert out_md
    assert _done_warnings(writes) is None


def test_slow_check_is_bounded_and_fails_open(monkeypatch):
    """extract is the sequential head of the job — a hung advisory call must not
    stall it. The check sits outside _run_with_failover's wait_for, so it needs
    its own bound.

    The elapsed assertion is what gives this test teeth: without asyncio.wait_for
    the phase still completes and still writes no warnings, so asserting only
    those two things would pass against an implementation with NO timeout at all
    — it would merely take 30 seconds."""
    import time

    monkeypatch.setattr(settings, "extract_coverage_timeout_seconds", 0.01)
    writes = _install_harness(monkeypatch)
    calls = {"n": 0}

    async def _slow(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(pipeline.agent, "check_extract_coverage", _slow)
    t0 = time.monotonic()
    out_md, *_ = _run_extract_phase()
    elapsed = time.monotonic() - t0

    assert calls["n"] == 1               # RED before the wiring exists
    assert elapsed < 5                   # RED without the timeout (would be ~30s)
    assert out_md                        # the extract itself still completed
    assert _done_warnings(writes) is None


def test_lease_and_cancel_signals_are_never_swallowed(monkeypatch):
    """A control signal means this worker no longer owns the job. Swallowing it
    inside an advisory check would let an obsolete worker keep writing."""
    for signal in (pipeline.LeaseLostSignal, pipeline.CancelWonSignal):
        _install_harness(monkeypatch)
        _install_coverage_spy(monkeypatch, boom=signal())
        with pytest.raises(signal):
            _run_extract_phase()


@pytest.mark.parametrize("subject, section_id", [
    ("history", "768820b7-54ea-45d2-bbb4-d95275ef95e6"),
    ("texnologiya", "d93f33a7-8120-4895-bc51-d2055c8ef7d4"),
])
@pytest.mark.parametrize("lane", ["fresh", "vision", "cache"])
def test_errata_reaches_return_persistence_and_coverage(monkeypatch, subject, section_id, lane):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import UUID

    original = (Path(__file__).parents[1] / "fixtures" / "lesson_errata" /
                f"{subject}-original.md").read_text(encoding="utf-8")
    cached = SimpleNamespace(id=uuid4(), job_id=uuid4(), output_md=original) if lane == "cache" else None
    writes = _install_harness(monkeypatch, cached_extract=cached)
    calls = _install_coverage_spy(monkeypatch)
    generation = AsyncMock(return_value=(original, 5, 7))
    monkeypatch.setattr(pipeline.agent, "summarize_lesson", generation)
    monkeypatch.setattr(pipeline.agent, "summarize_lesson_vision", generation)
    monkeypatch.setattr(pipeline.agent, "record_cached_lesson_extract", AsyncMock())
    if lane == "vision":
        monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda path: "x")
    out, *_ = _run_extract_phase(subject=subject, section={
        "id": UUID(section_id), "title": "T", "number": "1", "page_start": 1, "page_end": 2})
    assert "Xuanxe" not in out and "количество песка" not in out
    assert out != original
    assert next(kw["output_md"] for status, kw in writes if status == "done") == out
    if lane == "cache":
        generation.assert_not_awaited()
        assert calls["n"] == 0
        assert cached.output_md == original  # never rewrite another job's evidence
    else:
        generation.assert_awaited_once()
        assert calls["kwargs"]["summary"] == out
