"""Transient failures propagate for queue retry; hard failures stay terminal.

RED-proofs: today _execute_one_phase marks the job failed for EVERY class and
every upward path swallows, so (a) asserts an exception escapes where today
none does, and (c) asserts NO set_status('failed') call where today there is
one."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from loguru import logger

from app.services import pipeline
from app.services.errors import (
    PhaseAttemptTimeout,
    SlotSaturation,
    TransientPhaseError,
)


def test_phase_error_message_never_blank():
    assert pipeline._phase_error_message("extract", asyncio.TimeoutError()) == \
        "extract: TimeoutError()"
    assert pipeline._phase_error_message("extract", RuntimeError("boom")) == \
        "extract: boom"


@pytest.mark.parametrize("exc,expected", [
    (PhaseAttemptTimeout("per-attempt timeout after 600s"), True),
    (RuntimeError("429 RESOURCE_EXHAUSTED"), True),          # rate-limited
    (RuntimeError("socket connection closed unexpectedly"), True),  # transient
    (RuntimeError("malformed response envelope"), False),    # hard
    (RuntimeError("quota exceeded for project"), False),     # wall stays terminal
    # REGRESSION (2026-08-13): httpx's ConnectError text matched neither
    # `agent._TRANSIENT_NET_TERMS` nor `failure_classifier._TRANSIENT`, so this
    # returned False and the job went terminal at attempts=1 of 3 — on a host
    # that was healthy seconds later. google-genai speaks httpx.
    (RuntimeError(
        "practice-jigsaw: phase.run practice-jigsaw: gemini api call failed "
        "rc=1: All connection attempts failed :: All connection attempts failed"
    ), True),
    # The permanent shapes must stay terminal — retries bill real $.
    (RuntimeError("401 UNAUTHENTICATED"), False),
    (RuntimeError("prompt is too long"), False),
])
def test_requeue_worthy_classes(exc, expected):
    assert pipeline._requeue_worthy(exc) is expected


def _phase_kwargs(**over):
    """Full real signature of _execute_one_phase (pipeline.py:485-514)."""
    base = dict(
        job_id=uuid4(),
        resource_id="job-x",
        log=logger,
        phase_name="extract",
        phase_order=0,
        total_phases_hint=1,
        subject="history",
        provider="gemini",
        model="gemini-2.5-flash",
        pdf_path=Path("/nonexistent.pdf"),
        file_phases=set(),
        section_data={"title": "L1"},
        lesson_context=None,
        prior_outputs={},
        difficulty=None,
        source_map_digest="",
        transport="api",
        extract_transport="api",
        judge_transport="api",
        solver_transport="api",
        custom_prompts=None,
        judge_provider_ov=None,
        judge_model_ov=None,
        solver_provider_ov=None,
        solver_model_ov=None,
        solver_boss_arena_enabled=False,
        extract_provider="gemini",
        extract_model="gemini-2.5-flash",
        session_limit_strategy="pause",
        output_language="uz",
    )
    base.update(over)
    return base


def _stub_session_local(monkeypatch):
    """Async-context stub pattern copied from test_pipeline_judge_status.py."""
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))
    return fake_session


def test_transient_failure_raises_transient_phase_error(monkeypatch):
    async def failing(*a, **k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    _stub_session_local(monkeypatch)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(TransientPhaseError) as ei:
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    assert str(ei.value).startswith("extract: ")
    set_status.assert_not_awaited()     # job NOT marked failed — worker decides


def test_hard_failure_marks_failed_and_raises(monkeypatch):
    async def failing(*a, **k):
        raise RuntimeError("malformed response envelope")
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    _stub_session_local(monkeypatch)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    assert set_status.await_args.kwargs["error_message"] == \
        "extract: malformed response envelope"


def test_marker_error_from_vision_path_parks(monkeypatch):
    """Gate correction 1: a saturation-marker RuntimeError that BYPASSED
    _run_with_failover (scanned-PDF vision extract) still parks the job."""
    async def failing(*a, **k):
        raise RuntimeError(
            "gemini api call failed rc=1: 429 fleet credential slot wait "
            "exhausted (credential=gemini:p, budget=120s)"
        )
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    _stub_session_local(monkeypatch)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(SlotSaturation):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    set_status.assert_not_awaited()


@pytest.mark.parametrize("err,expect_exc,expect_db_mark", [
    (RuntimeError("429 RESOURCE_EXHAUSTED"), TransientPhaseError, False),
    (RuntimeError("malformed response envelope"), RuntimeError, True),
])
def test_broken_event_bus_never_eats_signals(monkeypatch, err, expect_exc, expect_db_mark):
    """Gate correction 2: events_bus.publish raising must not swallow the
    transient signal NOR the terminal DB mark. RED-proof: with publish-first
    unguarded ordering, the bus error replaces both."""
    async def failing(*a, **k):
        raise err
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    monkeypatch.setattr(pipeline, "_emit_started", AsyncMock())
    monkeypatch.setattr(
        pipeline.events_bus, "publish", AsyncMock(side_effect=RuntimeError("bus down"))
    )
    _stub_session_local(monkeypatch)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(expect_exc):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    assert set_status.await_count == (1 if expect_db_mark else 0)


def test_slot_saturation_passes_through_unmarked(monkeypatch):
    async def saturated(*a, **k):
        raise SlotSaturation("429 fleet credential slot wait exhausted (…)")
    monkeypatch.setattr(pipeline, "_execute_phase", saturated)
    monkeypatch.setattr(pipeline.events_bus, "publish", AsyncMock())
    _stub_session_local(monkeypatch)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(SlotSaturation):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    set_status.assert_not_awaited()


def test_phase_row_error_never_blank(monkeypatch):
    """Gate correction 5: the PHASE-row catch inside _execute_phase must also
    write a non-blank error_message — a bare asyncio.TimeoutError() str()s to
    '', so _error_text's repr fallback must be used there too, not just at
    the JOB-row write in _execute_one_phase."""
    fake_po = MagicMock()
    fake_po.id = uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    calls: list[tuple[str, dict]] = []

    async def fake_set_status(session, po_id, status, **kw):
        calls.append((status, kw))

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", AsyncMock())
    _stub_session_local(monkeypatch)

    def raise_timeout(*a, **kw):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(pipeline, "get_prompt", raise_timeout)
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda *a, **kw: "deadbeef" * 8)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(pipeline._execute_phase(
            job_id=uuid4(),
            phase_name="preview",
            phase_order=1,
            subject="history",
            provider="gemini",
            model="gemini-2.5-flash",
            pdf_path=Path("/nonexistent.pdf"),
            attach_file=False,
            section={
                "title": "L1", "number": "1", "page_start": 1, "page_end": 2,
                "id": uuid4(),
            },
            lesson_context="ctx",
            prior_outputs={},
            difficulty=None,
            transport="api",
            extract_transport="api",
            judge_transport="api",
            solver_transport="api",
            custom_prompts=None,
            judge_provider_ov=None,
            judge_model_ov=None,
            solver_provider_ov=None,
            solver_model_ov=None,
            solver_boss_arena_enabled=False,
            extract_provider="gemini",
            extract_model="gemini-2.5-flash",
            session_limit_strategy="pause",
            output_language="uz",
        ))

    failed_calls = [kw for status, kw in calls if status == "failed"]
    assert failed_calls, "expected a phase_repo.set_status('failed', …) call"
    assert failed_calls[0]["error_message"] == "TimeoutError()"
