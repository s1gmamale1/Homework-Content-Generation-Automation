"""Task 10 — artifact-aware judge routing.

A structured artifact's markdown is DERIVED. Grading it against the
hand-authored markdown contract is a category error: that prompt demands
narrative sections (Task/Context/Prediction/Final summary, Why + confidence
prompts, feedback lines, "How to play") that `content_json` does not carry and
must not grow — a live acceptance run returned MAJOR for exactly that reason.
So a structured artifact is graded on its canonical JSON against the
JSON-authoring contract; every markdown mode keeps today's path byte-for-byte.

Three judge call sites exist in `_execute_phase` (initial, one-free-retry,
post-regen) and ALL THREE must re-derive their inputs from the CURRENT artifact
— the post-regen site above all, because a judge regen that fell back to
markdown leaves the artifact in `markdown_fallback` mode with `content_json=None`.

$0: every model call, DB session and repo write is stubbed.
"""
import inspect
import json
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings as _settings
from app.schemas.content_json import RlcConfig, SentenceFillConfig
from app.services import pipeline
from app.services.phase_artifact import (
    PhaseArtifact,
    StructuredPhaseError,
    artifact_from_config,
)
from app.services.phase_judge import JudgeOutcome
from app.services.solver import SolveOutcome

STRUCTURED_PHASE = "practice-sentence"          # in SCHEMAS, NOT in _SOLVER_PHASES
STRUCTURED_SOLVER_PHASE = "practice-rlc"        # in SCHEMAS *and* in _SOLVER_PHASES


# ---------------------------------------------------------------------------
# Unit: the helper itself
# ---------------------------------------------------------------------------

def _structured():
    return PhaseArtifact(output_md="# md", content_json={"b": 2, "a": 1},
                         authoring_mode="structured",
                         content_schema_version="rlc_config@1", renderer_version="2")


def test_structured_artifact_is_judged_on_canonical_json_with_structured_contract():
    text, contract = pipeline._judge_inputs_for(
        _structured(), subject="history", phase_name="practice-rlc", output_language="uz")
    assert json.loads(text) == {"a": 1, "b": 2}
    assert text.index('"a"') < text.index('"b"')      # canonical: sorted keys
    assert contract and "JSON" in contract            # the structured authoring prompt


def test_canonical_json_is_unescaped_utf8_and_indented():
    """`ensure_ascii=False` keeps Uzbek/Russian text readable to the judge —
    \\u0448\\u0430\\u0445\\u0430\\u0440 is not gradable content."""
    art = PhaseArtifact(output_md="# md", content_json={"title": "shahar — o‘quv"},
                        authoring_mode="structured")
    text, _ = pipeline._judge_inputs_for(
        art, subject="history", phase_name="practice-rlc", output_language="uz")
    assert "o‘quv" in text
    assert "\\u" not in text
    assert "\n" in text, "indent=2 — the judge reads a formatted document"


@pytest.mark.parametrize("mode", ["markdown_builtin", "markdown_custom", "markdown_fallback"])
def test_markdown_modes_keep_todays_judge_path(mode):
    art = PhaseArtifact(output_md="# original markdown", authoring_mode=mode)
    text, contract = pipeline._judge_inputs_for(
        art, subject="history", phase_name="practice-rlc", output_language="uz")
    assert text == "# original markdown"
    assert contract is None


def test_markdown_custom_keeps_its_own_contract_override():
    """The custom uploaded prompt IS the markdown contract. The helper must
    thread it through, never clobber it with None."""
    art = PhaseArtifact(output_md="# custom md", authoring_mode="markdown_custom")
    text, contract = pipeline._judge_inputs_for(
        art, subject="history", phase_name="practice-rlc", output_language="uz",
        custom_override="MY CONTRACT")
    assert text == "# custom md"
    assert contract == "MY CONTRACT"


def test_markdown_fallback_of_a_structured_phase_is_graded_as_markdown():
    """A structured phase that fell back carries content_json=None — grading
    must follow the artifact's MODE, not the phase's schema membership."""
    art = PhaseArtifact(output_md="# fell back", authoring_mode="markdown_fallback")
    text, contract = pipeline._judge_inputs_for(
        art, subject="history", phase_name="practice-rlc", output_language="uz")
    assert (text, contract) == ("# fell back", None)


def test_all_three_judge_sites_route_through_the_helper():
    src = inspect.getsource(pipeline._execute_phase)
    assert src.count("_judge_inputs_for(") >= 3, src.count("_judge_inputs_for(")


# ---------------------------------------------------------------------------
# Integration: the three call sites inside _execute_phase
# ---------------------------------------------------------------------------

def _sentence_cfg(word: str = "cat"):
    return SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": f"A ___ ran ({word}).",
        "answers": [word], "word_bank": [word, "dog"]}]})


def _rlc_cfg(word: str = "alpha"):
    def opts():
        return [{"id": "o0", "label": "Yes", "is_correct": True},
                {"id": "o1", "label": "No", "is_correct": False}]
    return RlcConfig.model_validate({
        "id": "c1", "title": f"Fire audit ({word})", "intro": "You inspect a hall.",
        "expert_role": "fire_inspector",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "Choose", "prompt": "Evacuate?",
             "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "Ask", "prompt": "What data?",
             "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "Decide", "prompt": "Final?",
             "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "Concept", "prompt": "Which?",
             "concept_chips": [{"id": "k1", "label": "Load", "is_correct": True},
                               {"id": "k2", "label": "Colour", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "Explain", "prompt": "Why?",
             "min_chars": 80},
        ],
    })


def _canonical(content_json: dict) -> str:
    return json.dumps(content_json, sort_keys=True, ensure_ascii=False, indent=2)


def _judge_ok() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)


def _judge_major() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=False, warnings=["MAJOR: x"],
                        feedback="\n\nfix this", has_major=True)


def _judge_unavailable() -> JudgeOutcome:
    return JudgeOutcome(available=False, passed=True, warnings=["judge-unavailable"],
                        feedback="")


def _solve_agree() -> SolveOutcome:
    return SolveOutcome(available=True, agrees=True, warnings=[], feedback="",
                        has_mismatch=False)


def _make_kwargs(phase_name: str = STRUCTURED_PHASE) -> dict:
    return dict(
        job_id=uuid.uuid4(), phase_name=phase_name, phase_order=1, subject="english",
        provider="claude", model=None, pdf_path=Path("/fake/book.pdf"), attach_file=False,
        section={"title": "Tenses", "number": "1.1", "page_start": 1, "page_end": 5,
                 "id": uuid.uuid4()},
        lesson_context="some context", prior_outputs={}, difficulty=None,
        source_map_digest="abc123", transport="cli", extract_transport="cli",
        judge_transport="cli", solver_transport="cli", judge_provider_ov=None,
        judge_model_ov=None, solver_provider_ov=None, solver_model_ov=None,
        extract_provider="gemini", extract_model=None, solver_boss_arena_enabled=True,
    )


@pytest.fixture()
def judge_io(monkeypatch):
    """Stub every DB / model boundary; capture the judge's and solver's kwargs."""
    ns = types.SimpleNamespace(
        judge_calls=[], solve_calls=[], set_status_calls=[],
        structured_results=[], markdown_results=[],
    )

    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        return fake_po

    async def fake_set_status(session, po_id, status, **kw):
        ns.set_status_calls.append((status, kw))

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)

    async def fake_jobs_set_status(session, job_id, status, **kw):
        pass
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", fake_jobs_set_status)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    def _next(bucket, label):
        if not bucket:
            raise AssertionError(f"unexpected extra {label} attempt")
        item = bucket.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def fake_structured(*, phase_name, **kw):
        return _next(ns.structured_results, "structured")

    async def fake_markdown(*, phase_name, **kw):
        return _next(ns.markdown_results, "markdown")

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", fake_markdown)

    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase, **kw: "base prompt text")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase, **kw: "dead" * 16)
    monkeypatch.setattr(
        pipeline, "get_structured_prompt",
        lambda subject, phase, **kw: f"AUTHOR-JSON-CONTRACT:{phase}",
    )
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))
    monkeypatch.setattr(pipeline.model_tiers, "resolve_solver", lambda *a, **kw: ("claude", None))

    ns.judge_outcomes = [_judge_ok()]

    async def fake_judge(**kw):
        ns.judge_calls.append(kw)
        return ns.judge_outcomes.pop(0)
    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    ns.solve_outcomes = [_solve_agree()]

    async def fake_solve(**kw):
        ns.solve_calls.append(kw)
        return ns.solve_outcomes.pop(0)
    monkeypatch.setattr(pipeline.solver, "solve", fake_solve)

    monkeypatch.setattr(_settings, "max_judge_regens", 1)
    monkeypatch.setattr(_settings, "solver_enabled", False)
    monkeypatch.setattr(_settings, "max_solve_regens", 1)
    return ns


# --- site 1: the initial judge ---------------------------------------------

async def test_initial_judge_grades_structured_json_against_the_structured_contract(judge_io):
    art = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg())
    judge_io.structured_results = [(art, 90, 40, "claude")]

    await pipeline._execute_phase(**_make_kwargs())

    assert len(judge_io.judge_calls) == 1
    call = judge_io.judge_calls[0]
    assert call["output_md"] == _canonical(art.content_json)
    assert call["contract_override"] == f"AUTHOR-JSON-CONTRACT:{STRUCTURED_PHASE}"
    # ...and the rendered markdown is what gets PERSISTED, unchanged.
    done = next(kw for status, kw in judge_io.set_status_calls if status == "done")
    assert done["output_md"] == art.output_md


async def test_initial_judge_of_a_markdown_phase_is_unchanged(judge_io):
    judge_io.markdown_results = [("# plain markdown", 10, 5, "claude")]

    await pipeline._execute_phase(**_make_kwargs(phase_name="preview"))

    call = judge_io.judge_calls[0]
    assert call["output_md"] == "# plain markdown"
    assert call["contract_override"] is None


async def test_custom_prompt_still_supplies_its_own_contract_override(judge_io):
    judge_io.markdown_results = [("# custom output", 10, 5, "claude")]
    kw = _make_kwargs()
    kw["custom_prompts"] = {STRUCTURED_PHASE: "MY OWN CONTRACT"}

    await pipeline._execute_phase(**kw)

    call = judge_io.judge_calls[0]
    assert call["output_md"] == "# custom output", "custom is a MARKDOWN contract"
    assert call["contract_override"] == "MY OWN CONTRACT", (
        "the helper must not clobber the custom override with None"
    )


# --- site 2: the one-free-retry judge ---------------------------------------

async def test_retry_judge_reroutes_the_structured_inputs(judge_io):
    """The retry site is a separate spawn; if it kept `output_md` it would grade
    the DERIVED markdown against the markdown contract — the exact MAJOR the
    live acceptance run produced."""
    art = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg())
    judge_io.structured_results = [(art, 90, 40, "claude")]
    judge_io.judge_outcomes = [_judge_unavailable(), _judge_ok()]

    await pipeline._execute_phase(**_make_kwargs())

    assert len(judge_io.judge_calls) == 2
    assert judge_io.judge_calls[1]["output_md"] == _canonical(art.content_json)


# --- site 3: the post-regen judge (the subtle one) --------------------------

async def test_post_regen_judge_uses_markdown_after_a_fallback_regen(judge_io):
    """THE regression this task exists to prevent.

    A judge regen that falls back to markdown leaves the artifact in
    `markdown_fallback` with content_json=None. Re-deriving from a STALE
    structured artifact would grade the previous attempt's JSON — text that is
    no longer the phase's output at all.
    """
    art_a = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("alpha"))
    judge_io.structured_results = [
        (art_a, 100, 50, "claude"),
        StructuredPhaseError("regen emitted invalid JSON"),
    ]
    judge_io.markdown_results = [("# regenned markdown", 111, 66, "claude")]
    judge_io.judge_outcomes = [_judge_major(), _judge_ok()]

    await pipeline._execute_phase(**_make_kwargs())

    assert len(judge_io.judge_calls) == 2
    post = judge_io.judge_calls[1]
    assert post["output_md"] == "# regenned markdown", (
        "the post-regen judge must grade the CURRENT artifact, not the stale JSON"
    )
    assert post["contract_override"] is None, "markdown_fallback → markdown contract"


async def test_post_regen_judge_grades_the_regenerated_json(judge_io):
    art_a = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("alpha"))
    art_b = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("bravo"))
    judge_io.structured_results = [(art_a, 100, 50, "claude"), (art_b, 110, 55, "claude")]
    judge_io.judge_outcomes = [_judge_major(), _judge_ok()]

    await pipeline._execute_phase(**_make_kwargs())

    post = judge_io.judge_calls[1]
    assert post["output_md"] == _canonical(art_b.content_json)
    assert post["output_md"] != _canonical(art_a.content_json)
    assert post["contract_override"] == f"AUTHOR-JSON-CONTRACT:{STRUCTURED_PHASE}"


async def test_post_regen_judge_of_a_custom_phase_keeps_the_custom_override(judge_io):
    judge_io.markdown_results = [("# custom v1", 10, 5, "claude"),
                                 ("# custom v2", 11, 6, "claude")]
    judge_io.judge_outcomes = [_judge_major(), _judge_ok()]
    kw = _make_kwargs()
    kw["custom_prompts"] = {STRUCTURED_PHASE: "MY OWN CONTRACT"}

    await pipeline._execute_phase(**kw)

    post = judge_io.judge_calls[1]
    assert post["output_md"] == "# custom v2"
    assert post["contract_override"] == "MY OWN CONTRACT"


# --- the solver is deliberately untouched -----------------------------------

async def test_solver_still_grades_the_rendered_markdown(judge_io, monkeypatch):
    """The solver re-derives the answer key from the STUDENT-facing rendering
    and reads the author-only `## Answer key` section — both live in markdown.
    Handing it JSON would break its whole premise."""
    monkeypatch.setattr(_settings, "solver_enabled", True)
    art = artifact_from_config(STRUCTURED_SOLVER_PHASE, _rlc_cfg())
    judge_io.structured_results = [(art, 100, 50, "claude")]

    await pipeline._execute_phase(**_make_kwargs(phase_name=STRUCTURED_SOLVER_PHASE))

    assert len(judge_io.solve_calls) == 1
    assert judge_io.solve_calls[0]["phase_output_md"] == art.output_md
    assert judge_io.solve_calls[0]["contract_override"] is None
