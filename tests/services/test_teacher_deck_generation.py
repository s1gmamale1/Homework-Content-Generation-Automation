"""Task 6 — teacher-deck generation: resilient, structured, content_json only.

Contracts:
  1. Executing the `teacher-deck` phase (via `_execute_one_phase`) calls
     `agent.run_phase` with `schema=TeacherDeck`, `job.provider/model/transport`,
     and `lesson_context=<the extract>`, then persists the parsed deck as
     `content_json` with `authoring_mode="structured"` and a stamped
     `content_schema_version` — routed through `_run_with_failover` (the SAME
     resilience wrapper the content structured lane uses), not a bare call.
  2. `artifact_from_config` is NEVER called for teacher-deck (no renderer).
  3. Structured generation lands even when `settings.structured_output_enabled`
     is False — teacher-deck bypasses that kill switch entirely.
  4. `SchemaValidationExhausted` fails the phase loudly (propagates) — no
     markdown fallback exists to catch it.

$0: every model call, DB session and repo write is stubbed.
"""
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings as _settings
from app.schemas.content_json import TeacherDeck
from app.services import agent, pipeline
from app.services.errors import LeaseLostSignal

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _load_deck() -> TeacherDeck:
    import json
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return TeacherDeck.model_validate(json.load(fh))


def _make_kwargs(**over) -> dict:
    kw = dict(
        job_id=uuid.uuid4(),
        resource_id="job:x",
        log=MagicMock(),
        phase_name="teacher-deck",
        phase_order=1,
        total_phases_hint=2,
        subject="history",
        provider="claude",
        model="claude-opus-4-6",
        pdf_path=None,
        file_phases=set(),
        section_data={"id": uuid.uuid4(), "title": "Hindiston", "number": "19",
                      "page_start": 85, "page_end": 89, "chapter": ""},
        lesson_context="EXTRACT: Hindiston mustaqillik topshirig'i, 1992 yil...",
        prior_outputs={},
        difficulty=None,
        transport="api",
        extract_transport="api",
        judge_transport="api",
        solver_transport="api",
        session_limit_strategy="pause",
        output_language="uz",
        lease=None,
    )
    kw.update(over)
    return kw


@pytest.fixture()
def patch_io(monkeypatch):
    """Patch every DB / model boundary; capture set_status kwargs and the
    exact kwargs agent.run_phase (via _run_teacher_deck_attempt's inner
    run_fn) was called with."""
    ns = types.SimpleNamespace(
        set_status_calls=[], run_phase_calls=[], run_phase_results=[],
    )

    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        ns.set_status_calls.append((status, kw))
        return True

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)

    async def fake_jobs_set_status(session, job_id, status, **kw):
        return True
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", fake_jobs_set_status)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    monkeypatch.setattr(
        pipeline, "get_structured_prompt",
        lambda subject, phase, **kw: "author the deck as JSON",
    )

    async def boom_artifact_from_config(*a, **kw):
        raise AssertionError("artifact_from_config must NOT be called for teacher-deck")
    monkeypatch.setattr(pipeline, "artifact_from_config", boom_artifact_from_config)

    async def fake_run_phase(**kw):
        ns.run_phase_calls.append(kw)
        item = ns.run_phase_results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    monkeypatch.setattr(pipeline.agent, "run_phase", fake_run_phase)

    ns.done_kwargs = lambda: next(
        kwargs for status, kwargs in ns.set_status_calls if status == "done"
    )
    return ns


def _phase_result(deck: TeacherDeck) -> agent.PhaseResult:
    return agent.PhaseResult(
        text="{}", parsed=deck,
        usage={"prompt_tokens": 500, "output_tokens": 900, "cached_tokens": 0,
               "total_tokens": 1400, "raw": {}},
    )


# ===========================================================================
# 1 + 2 — resilient generation, correct kwargs, content_json persisted
# ===========================================================================

async def test_teacher_deck_persists_content_json_structured(patch_io):
    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    await pipeline._execute_one_phase(**_make_kwargs())

    done = patch_io.done_kwargs()
    assert done["content_json"] == deck.model_dump()
    assert done["authoring_mode"] == "structured"
    assert done["content_schema_version"] == TeacherDeck.SCHEMA_VERSION == "teacher_deck@1"


async def test_teacher_deck_run_phase_called_with_job_provider_model_transport_and_extract(
    patch_io,
):
    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    kw = _make_kwargs(
        provider="claude", model="claude-opus-4-6", transport="api",
        lesson_context="EXTRACT: the cached lesson extract text",
    )
    await pipeline._execute_one_phase(**kw)

    assert len(patch_io.run_phase_calls) == 1
    call = patch_io.run_phase_calls[0]
    assert call["provider"] == "claude"
    assert call["model"] == "claude-opus-4-6"
    assert call["transport"] == "api"
    assert call["lesson_context"] == "EXTRACT: the cached lesson extract text"
    assert call["schema"] is TeacherDeck
    assert call["phase_name"] == "teacher-deck"


async def test_teacher_deck_never_calls_artifact_from_config(patch_io):
    """boom_artifact_from_config in patch_io raises if it's ever invoked — a
    clean run (no assertion error) proves it wasn't called."""
    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    await pipeline._execute_one_phase(**_make_kwargs())  # would raise if called

    done = patch_io.done_kwargs()
    assert done["content_json"] is not None


# ===========================================================================
# 3 — bypasses settings.structured_output_enabled entirely
# ===========================================================================

async def test_teacher_deck_generates_structured_even_with_kill_switch_off(
    patch_io, monkeypatch,
):
    assert _settings.structured_output_enabled is False, (
        "precondition: production default is OFF"
    )
    monkeypatch.setattr(_settings, "structured_output_enabled", False)

    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    await pipeline._execute_one_phase(**_make_kwargs())

    done = patch_io.done_kwargs()
    assert done["authoring_mode"] == "structured"
    assert done["content_json"] == deck.model_dump()


# ===========================================================================
# 4 — SchemaValidationExhausted fails the phase loudly, no markdown fallback
# ===========================================================================

async def test_teacher_deck_schema_exhausted_fails_loudly(patch_io):
    patch_io.run_phase_results = [
        agent.SchemaValidationExhausted("teacher-deck: could not validate")
    ]

    with pytest.raises(agent.SchemaValidationExhausted):
        await pipeline._execute_one_phase(**_make_kwargs())

    statuses = [s for s, _ in patch_io.set_status_calls]
    assert "failed" in statuses, "the phase row must be marked failed"
    assert not any(s == "done" for s in statuses), (
        "no markdown fallback exists for teacher-deck — 'done' must never fire"
    )


async def test_teacher_deck_lease_lost_is_not_a_content_failure(patch_io):
    """Control signals (fenced job leases) must propagate untouched — never
    laundered into a phase-failed write."""
    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def lease_lost_create(session, **kw):
        from app.services.lease import LeaseLost
        return LeaseLost
    import app.services.pipeline as _p
    orig = _p.phase_repo.create_or_reset

    async def fake_create_or_reset(session, **kw):
        from app.services.lease import LeaseLost
        return LeaseLost
    import unittest.mock as _m

    with _m.patch.object(_p.phase_repo, "create_or_reset", fake_create_or_reset):
        with pytest.raises(LeaseLostSignal):
            await pipeline._execute_one_phase(**_make_kwargs(lease=MagicMock(claim_token=uuid.uuid4())))
