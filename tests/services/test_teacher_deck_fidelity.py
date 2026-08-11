"""Task 7 — teacher-deck factual-fidelity gate: judge + regen-once.

Contracts:
  1. `serialize_deck_for_fidelity` renders only fact-bearing deck content
     (objectives, core idea, stage points/screen_text, quiz, answer key) and
     excludes pure structural/teaching chrome (badges, timings, rubric).
  2. The Fable-Critical guard: the REAL `phase_judge.judge(...)` — with
     `contract_override=get_teacher_deck_fidelity_contract()` — is actually
     reachable and returns `available=True` on a clean verdict. Only the
     judge's own `agent.run_phase` is mocked, so a broken/missing
     `contract_override` wiring would surface as `available=False` here.
  3. A deck whose answer-key contradicts the extract -> judge `has_major=True`
     once, `False` on re-judge -> EXACTLY one regeneration, feeding
     `outcome.feedback` into the regen prompt, and the regenerated deck is
     what gets persisted.
  4. A clean deck (`has_major=False` on the first judge call) -> zero regens.
  5. A regeneration failure keeps the ORIGINAL deck + records the judge's
     warnings (fail-open) — the job is NOT failed.
  6. An api-auth error raised by the judge propagates (job fails loudly, not
     degraded to judge-unavailable).

$0: every model call, DB session and repo write is stubbed except test 2,
which stubs only `agent.run_phase` underneath the real judge.
"""
import json
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.content_json import TeacherDeck
from app.services import agent, phase_judge, pipeline
from app.services.prompts import get_teacher_deck_fidelity_contract
from app.services.teacher_deck import serialize_deck_for_fidelity

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _load_deck_dict() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _load_deck() -> TeacherDeck:
    return TeacherDeck.model_validate(_load_deck_dict())


def _mutated_deck(**patch) -> TeacherDeck:
    """A copy of the fixture deck with a shallow top-level field replaced —
    used to give the 'regenerated' deck a distinguishably different payload."""
    raw = _load_deck_dict()
    raw.update(patch)
    return TeacherDeck.model_validate(raw)


# ===========================================================================
# 1 — serializer: fact-bearing content in, structural chrome out
# ===========================================================================

def test_serializer_contains_fact_bearing_content_and_excludes_chrome():
    deck = _load_deck()
    out = serialize_deck_for_fidelity(deck)

    # Fact-bearing content survives.
    assert "Narasimxa Rao" in out
    assert "1992" in out
    assert deck.core_idea.statement in out
    assert deck.core_idea.elaboration in out
    assert deck.objectives.bilib_oladi in out
    for q in deck.quiz:
        assert q.question in out
        for opt in q.options:
            assert opt.text in out
    for a in deck.answer_key:
        assert a.explanation in out

    # Pure structural/teaching chrome does NOT survive.
    assert "teacher_only" not in out          # badge literal
    assert "ekranga" not in out                # badge literal
    for stage in deck.stages:
        assert stage.teacher_action not in out
        assert stage.student_action not in out
    # Rubric scoring chrome (component detail text is distinctive, doesn't
    # appear anywhere else in the deck).
    for comp in deck.rubric.components:
        assert comp.detail not in out
    for item in deck.lesson_map:
        assert item.description not in out


def test_serializer_is_a_pure_function():
    deck = _load_deck()
    a = serialize_deck_for_fidelity(deck)
    b = serialize_deck_for_fidelity(deck)
    assert a == b


# ===========================================================================
# 2 — Fable-Critical guard: the real judge must be reachable via
# contract_override, proving the wiring (not just the mock) is correct.
# ===========================================================================

async def test_real_judge_is_reachable_with_teacher_deck_fidelity_contract(monkeypatch):
    deck = _load_deck()
    serialized = serialize_deck_for_fidelity(deck)

    async def fake_run_phase(**kwargs):
        return agent.PhaseResult(
            text="{}",
            parsed=phase_judge.Verdict(passed=True, failures=[]),
            usage={"prompt_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
                   "total_tokens": 15, "raw": {}},
        )

    monkeypatch.setattr(agent, "run_phase", fake_run_phase)

    outcome = await phase_judge.judge(
        subject="history",
        phase_name="teacher-deck",
        output_md=serialized,
        lesson_context="EXTRACT: Narasimxa Rao boshladi bozor islohotlarini 1991-yilda...",
        prior_outputs={},
        gen_provider="claude",
        gen_model="claude-opus-4-7",
        judge_provider="claude",
        judge_model="claude-opus-4-7",
        contract_override=get_teacher_deck_fidelity_contract(),
    )

    assert outcome.available is True
    assert outcome.has_major is False
    assert outcome.passed is True


# ===========================================================================
# Regen-loop behavior via _execute_one_phase (phase_judge.judge scripted)
# ===========================================================================

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


def _phase_result(deck: TeacherDeck) -> agent.PhaseResult:
    return agent.PhaseResult(
        text="{}", parsed=deck,
        usage={"prompt_tokens": 500, "output_tokens": 900, "cached_tokens": 0,
               "total_tokens": 1400, "raw": {}},
    )


@pytest.fixture()
def patch_io(monkeypatch):
    """Patch every DB / model boundary; capture set_status kwargs and the
    exact kwargs agent.run_phase was called with (deck generation only —
    phase_judge.judge is mocked directly in these tests, so it never reaches
    agent.run_phase)."""
    ns = types.SimpleNamespace(
        set_status_calls=[], jobs_set_status_calls=[],
        run_phase_calls=[], run_phase_results=[],
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
        ns.jobs_set_status_calls.append((status, kw))
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


def _fake_judge(outcomes: list):
    """Scripts a sequence of JudgeOutcome (or exception) returns; records the
    kwargs of every call."""
    calls: list[dict] = []

    async def fake(**kwargs):
        calls.append(kwargs)
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    fake.calls = calls
    return fake


# ===========================================================================
# 3 — major issue -> exactly one regeneration, feedback fed forward
# ===========================================================================

async def test_major_fidelity_issue_triggers_exactly_one_regen_and_persists_new_deck(
    patch_io, monkeypatch,
):
    original = _load_deck()
    corrected = _mutated_deck(objectives={
        "bilib_oladi": "CORRECTED: 1991-yilgi burilishni toʻgʻri sanalar bilan.",
        "qila_oladi": original.objectives.qila_oladi,
        "tushunadi": original.objectives.tushunadi,
    })
    patch_io.run_phase_results = [_phase_result(original), _phase_result(corrected)]

    judge_fn = _fake_judge([
        phase_judge.JudgeOutcome(
            available=True, passed=False,
            warnings=["[major] answer_key[1] contradicts extract — evidence: ..."],
            feedback="Fix: answer_key[1] must match the extract's date.",
            has_major=True,
        ),
        phase_judge.JudgeOutcome(available=True, passed=True, warnings=[], feedback=""),
    ])
    monkeypatch.setattr(pipeline.phase_judge, "judge", judge_fn)

    await pipeline._execute_one_phase(**_make_kwargs())

    # Exactly one regeneration: 2 deck-generation calls total.
    assert len(patch_io.run_phase_calls) == 2
    # Feedback was fed into the regen's structured prompt.
    assert "Fix: answer_key[1] must match the extract's date." in patch_io.run_phase_calls[1]["phase_prompt"]
    # Judge called twice: initial + post-regen.
    assert len(judge_fn.calls) == 2
    assert judge_fn.calls[0]["contract_override"] == get_teacher_deck_fidelity_contract()
    assert judge_fn.calls[0]["phase_name"] == "teacher-deck"

    done = patch_io.done_kwargs()
    assert done["content_json"] == corrected.model_dump(mode="json")
    assert done["judge_status"] == "ok"


# ===========================================================================
# 4 — clean deck -> zero regens
# ===========================================================================

async def test_clean_deck_triggers_zero_regens(patch_io, monkeypatch):
    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    judge_fn = _fake_judge([
        phase_judge.JudgeOutcome(available=True, passed=True, warnings=[], feedback=""),
    ])
    monkeypatch.setattr(pipeline.phase_judge, "judge", judge_fn)

    await pipeline._execute_one_phase(**_make_kwargs())

    assert len(patch_io.run_phase_calls) == 1  # only the initial generation
    assert len(judge_fn.calls) == 1

    done = patch_io.done_kwargs()
    assert done["content_json"] == deck.model_dump(mode="json")
    assert done["judge_status"] == "ok"


# ===========================================================================
# 5 — regen failure: fail-open, keep original + warnings, job NOT failed
# ===========================================================================

async def test_regen_failure_keeps_original_deck_and_records_warnings(patch_io, monkeypatch):
    original = _load_deck()
    patch_io.run_phase_results = [
        _phase_result(original),
        agent.SchemaValidationExhausted("teacher-deck: could not validate on regen"),
    ]

    major_warnings = ["[major] answer_key[2] contradicts extract"]
    judge_fn = _fake_judge([
        phase_judge.JudgeOutcome(
            available=True, passed=False, warnings=major_warnings,
            feedback="Fix answer_key[2].", has_major=True,
        ),
    ])
    monkeypatch.setattr(pipeline.phase_judge, "judge", judge_fn)

    # Must NOT raise — validation failures never fail the job.
    await pipeline._execute_one_phase(**_make_kwargs())

    assert len(patch_io.run_phase_calls) == 2  # initial + failed regen attempt
    assert len(judge_fn.calls) == 1  # regen never succeeded -> no post-regen re-judge

    done = patch_io.done_kwargs()
    assert done["content_json"] == original.model_dump(mode="json")  # ORIGINAL kept
    assert done["validation_warnings"] == major_warnings
    assert done["judge_status"] == "major_regen_failed"

    statuses = [s for s, _ in patch_io.set_status_calls]
    assert "failed" not in statuses
    assert not patch_io.jobs_set_status_calls or all(
        s != "failed" for s, _ in patch_io.jobs_set_status_calls
    )


# ===========================================================================
# 6 — api-auth error from the judge propagates (job fails loudly)
# ===========================================================================

async def test_api_auth_error_from_judge_propagates_and_fails_job(patch_io, monkeypatch):
    deck = _load_deck()
    patch_io.run_phase_results = [_phase_result(deck)]

    async def fake_judge_raises_auth(**kwargs):
        raise RuntimeError("401 Unauthorized: invalid api key")
    monkeypatch.setattr(pipeline.phase_judge, "judge", fake_judge_raises_auth)

    with pytest.raises(RuntimeError, match="401"):
        await pipeline._execute_one_phase(**_make_kwargs(transport="api", judge_transport="api"))

    # The job must be marked failed — never degraded to a silent "done".
    statuses = [s for s, _ in patch_io.set_status_calls]
    assert "done" not in statuses
    assert any(s == "failed" for s, _ in patch_io.jobs_set_status_calls)
