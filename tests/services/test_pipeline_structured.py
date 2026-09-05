"""Structured generation wired into _execute_phase (Task 7).

Five contracts, each written so it BITES (each has a recorded RED proof):

  1. Only StructuredPhaseError triggers the markdown fallback. A transport-shaped
     error propagates and the markdown path is never entered.
  2. Widening `except StructuredPhaseError` to `except Exception` breaks (1).
  3. structured A -> structured regeneration B persists B's content_json.
  4. structured A -> markdown regeneration clears ALL structured fields.
  5. Nothing is persisted before the final accepted result: exactly ONE terminal
     set_status call per phase.

Plus: BOTH regen sites (judge and solver) swap the whole artifact — the judge
site and the solver site are asserted separately, because the two sites carried
the identical `output_md, tin, tout, produced_by = r_md, ...` line and fixing
only one is the exact bug this file exists to prevent.

$0: every model call, DB session and repo write is stubbed.
"""
import asyncio
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings as _settings
from app.schemas.content_json import RlcConfig, SentenceFillConfig
from app.services import pipeline
from app.services.errors import (
    SLOT_SATURATION_MARKER,
    PhaseAttemptTimeout,
    SlotSaturation,
)
from app.services.phase_artifact import (
    StructuredPhaseError,
    artifact_from_config,
    artifact_from_markdown,
)
from app.services.phase_judge import JudgeOutcome
from app.services.solver import SolveOutcome

STRUCTURED_PHASE = "practice-sentence"          # in SCHEMAS and in _SOLVER_PHASES
STRUCTURED_SOLVER_PHASE = "practice-rlc"        # in SCHEMAS *and* in _SOLVER_PHASES
PLAIN_PHASE = "preview"                         # no structured schema at all


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def _judge_ok() -> JudgeOutcome:
    return JudgeOutcome(available=True, passed=True, warnings=[], feedback="", has_major=False)


def _judge_major() -> JudgeOutcome:
    return JudgeOutcome(
        available=True, passed=False, warnings=["MAJOR: content issue"],
        feedback="\n\nfix this", has_major=True,
    )


def _solve_agree() -> SolveOutcome:
    return SolveOutcome(available=True, agrees=True, warnings=[], feedback="", has_mismatch=False)


def _solve_mismatch() -> SolveOutcome:
    return SolveOutcome(
        available=True, agrees=False, warnings=["[high] key mismatch"],
        feedback="\n\nfix the key", has_mismatch=True,
    )


def _gen_kwargs(**over) -> dict:
    """A realistic `_generate_artifact` kwarg set (the union both attempt
    functions accept), so these tests would still type-check against the real
    `_run_structured_attempt` / `_run_markdown_attempt` signatures."""
    kw = dict(
        is_custom=False,
        requested_provider="claude",
        model=None,
        run_fn=None,
        structured_prompt="author JSON",
        transport="cli",
        session_limit_strategy="pause",
        lesson_context="ctx",
        prior_outputs={},
        difficulty=None,
        attachments=[],
        job_id=uuid.uuid4(),
        po_id=uuid.uuid4(),
        source_map_digest="abc123",
    )
    kw.update(over)
    return kw


def _make_kwargs(phase_name: str = STRUCTURED_PHASE) -> dict:
    return dict(
        job_id=uuid.uuid4(),
        phase_name=phase_name,
        phase_order=1,
        subject="english",
        provider="claude",
        model=None,
        pdf_path=Path("/fake/book.pdf"),
        attach_file=False,
        section={"title": "Tenses", "number": "1.1", "page_start": 1, "page_end": 5,
                 "id": uuid.uuid4()},
        lesson_context="some context",
        prior_outputs={},
        difficulty=None,
        source_map_digest="abc123",
        transport="cli",
        extract_transport="cli",
        judge_transport="cli",
        solver_transport="cli",
        judge_provider_ov=None,
        judge_model_ov=None,
        solver_provider_ov=None,
        solver_model_ov=None,
        extract_provider="gemini",
        extract_model=None,
        solver_boss_arena_enabled=True,
    )


@pytest.fixture(autouse=True)
def _structured_enabled(monkeypatch):
    """Every contract in this module exercises the structured lane, so the
    global kill switch (settings.structured_output_enabled, default False in
    production) is turned ON as their precondition. The two dedicated
    kill-switch tests below override this back to False in-test."""
    monkeypatch.setattr(_settings, "structured_output_enabled", True)


@pytest.fixture()
def patch_io(monkeypatch):
    """Patch every DB / model boundary; capture set_status kwargs.

    `structured_results` and `markdown_results` are consumed in order by the
    stubbed attempt functions. An entry that is an Exception instance is raised
    instead of returned — that is how a test drives the fallback (or proves a
    transport error propagates).
    """
    ns = types.SimpleNamespace(
        set_status_calls=[], structured_calls=[], markdown_calls=[],
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
        ns.structured_calls.append(kw)
        return _next(ns.structured_results, "structured")

    async def fake_markdown(*, phase_name, **kw):
        ns.markdown_calls.append(kw)
        return _next(ns.markdown_results, "markdown")

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", fake_markdown)

    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase, **kw: "base prompt text")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase, **kw: "deadbeef" * 8)
    monkeypatch.setattr(
        pipeline, "get_structured_prompt", lambda subject, phase, **kw: "author JSON"
    )
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))
    monkeypatch.setattr(pipeline.model_tiers, "resolve_solver", lambda *a, **kw: ("claude", None))

    ns.judge_outcomes = [_judge_ok()]

    async def fake_judge(**kw):
        return ns.judge_outcomes.pop(0)
    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    ns.solve_outcomes = [_solve_agree()]

    async def fake_solve(**kw):
        return ns.solve_outcomes.pop(0)
    monkeypatch.setattr(pipeline.solver, "solve", fake_solve)

    monkeypatch.setattr(_settings, "max_judge_regens", 1)
    monkeypatch.setattr(_settings, "solver_enabled", False)
    monkeypatch.setattr(_settings, "max_solve_regens", 1)

    ns.done_kwargs = lambda: next(
        kwargs for status, kwargs in ns.set_status_calls if status == "done"
    )
    return ns


# ===========================================================================
# RED-PROOF 1 — only StructuredPhaseError triggers the markdown fallback
# ===========================================================================

async def test_structured_phase_error_falls_back_to_markdown(monkeypatch):
    md_calls = []

    async def boom(**kw):
        raise StructuredPhaseError("model emitted prose, not JSON")

    async def markdown(**kw):
        md_calls.append(kw)
        return "# fallback markdown", 11, 22, "claude"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", boom)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", markdown)

    art, tin, tout, produced_by = await pipeline._generate_artifact(
        phase_name=STRUCTURED_PHASE, **_gen_kwargs()
    )

    assert len(md_calls) == 1, "the markdown fallback must run exactly once"
    assert art.output_md == "# fallback markdown"
    assert art.authoring_mode == "markdown_fallback"
    assert art.content_json is None
    assert (tin, tout, produced_by) == (11, 22, "claude")


async def test_transport_error_propagates_and_markdown_never_runs(monkeypatch):
    """A 429 is NOT a "the model can't author JSON" signal. It must keep the
    existing classify/retry/failover semantics, which means it has to escape
    _generate_artifact untouched — never be laundered into a markdown fallback."""
    md_calls = []

    async def rate_limited(**kw):
        raise RuntimeError("429 rate limited")

    async def markdown(**kw):
        md_calls.append(kw)
        return "# fallback markdown", 1, 2, "claude"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", rate_limited)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", markdown)

    with pytest.raises(RuntimeError) as excinfo:
        await pipeline._generate_artifact(phase_name=STRUCTURED_PHASE, **_gen_kwargs())

    # StructuredPhaseError subclasses RuntimeError, so `raises(RuntimeError)`
    # alone would not distinguish the two — assert the exact type.
    assert type(excinfo.value) is RuntimeError, f"got {type(excinfo.value).__name__}"
    assert "429 rate limited" in str(excinfo.value)
    assert md_calls == [], (
        "the markdown path must NEVER run for a transport error — a widened "
        "`except Exception` would silently burn a second generation here"
    )


async def test_phase_without_a_schema_never_attempts_structured(monkeypatch):
    async def boom(**kw):
        raise AssertionError("structured attempt must not run for a schema-less phase")

    async def markdown(**kw):
        return "# plain markdown", 3, 4, "gemini"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", boom)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", markdown)

    art, *_ = await pipeline._generate_artifact(phase_name=PLAIN_PHASE, **_gen_kwargs())
    assert art.authoring_mode == "markdown_builtin"
    assert art.content_json is None


async def test_kill_switch_off_forces_markdown_builtin_for_a_structured_phase(monkeypatch):
    """The global kill switch OFF (production default) must make a phase that
    IS in SCHEMAS render markdown_builtin and never attempt JSON-authoring —
    this is what keeps the content_json lane dark until deliberately flipped."""
    monkeypatch.setattr(_settings, "structured_output_enabled", False)

    async def boom(**kw):
        raise AssertionError("kill switch OFF must not attempt structured generation")

    async def markdown(**kw):
        return "# plain markdown", 7, 8, "gemini"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", boom)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", markdown)

    art, *_ = await pipeline._generate_artifact(
        phase_name=STRUCTURED_PHASE, **_gen_kwargs()
    )
    assert art.authoring_mode == "markdown_builtin"
    assert art.content_json is None


async def test_kill_switch_on_attempts_structured_for_a_structured_phase(monkeypatch):
    """The kill switch ON restores the structured attempt for a SCHEMAS phase."""
    monkeypatch.setattr(_settings, "structured_output_enabled", True)
    ran = []

    async def structured(**kw):
        ran.append(kw)
        return artifact_from_config("practice-sentence", _sentence_cfg()), 1, 2, "claude"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", structured)

    art, *_ = await pipeline._generate_artifact(
        phase_name=STRUCTURED_PHASE, **_gen_kwargs()
    )
    assert len(ran) == 1, "kill switch ON must attempt structured generation"
    assert art.content_json is not None


async def test_custom_prompt_disables_structured_and_records_markdown_custom(monkeypatch):
    async def boom(**kw):
        raise AssertionError("a custom prompt is a MARKDOWN contract — no structured attempt")

    async def markdown(**kw):
        return "# custom markdown", 5, 6, "claude"

    monkeypatch.setattr(pipeline, "_run_structured_attempt", boom)
    monkeypatch.setattr(pipeline, "_run_markdown_attempt", markdown)

    art, *_ = await pipeline._generate_artifact(
        phase_name=STRUCTURED_PHASE, **_gen_kwargs(is_custom=True)
    )
    assert art.authoring_mode == "markdown_custom"


async def test_execute_phase_with_custom_prompt_records_markdown_custom(patch_io):
    patch_io.markdown_results = [("# custom output", 10, 5, "claude")]
    kw = _make_kwargs()
    kw["custom_prompts"] = {STRUCTURED_PHASE: "my own contract"}

    await pipeline._execute_phase(**kw)

    assert patch_io.structured_calls == []
    assert patch_io.done_kwargs()["authoring_mode"] == "markdown_custom"


# ===========================================================================
# RED-PROOF 3 — structured A -> structured regen B persists B
# ===========================================================================

async def test_judge_regen_structured_persists_the_regenerated_json(patch_io):
    art_a = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("alpha"))
    art_b = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("bravo"))
    patch_io.structured_results = [(art_a, 100, 50, "claude"), (art_b, 110, 55, "claude")]
    patch_io.judge_outcomes = [_judge_major(), _judge_ok()]

    await pipeline._execute_phase(**_make_kwargs())

    done = patch_io.done_kwargs()
    assert done["authoring_mode"] == "structured"
    assert done["content_json"] == art_b.content_json, "the REGENERATED config must win"
    assert done["content_json"] != art_a.content_json
    assert done["output_md"] == art_b.output_md, "markdown and JSON must be the same attempt's"
    assert done["content_schema_version"] == art_b.content_schema_version
    assert done["renderer_version"] == art_b.renderer_version
    assert (done["tokens_input"], done["tokens_output"]) == (110, 55)


# ===========================================================================
# RED-PROOF 4 — structured A -> markdown regen clears every structured field
# ===========================================================================

async def test_judge_regen_falling_back_to_markdown_clears_structured_fields(patch_io):
    art_a = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg("alpha"))
    patch_io.structured_results = [
        (art_a, 100, 50, "claude"),
        StructuredPhaseError("regen emitted invalid JSON"),
    ]
    patch_io.markdown_results = [("# regenned markdown", 111, 66, "claude")]
    patch_io.judge_outcomes = [_judge_major(), _judge_ok()]

    await pipeline._execute_phase(**_make_kwargs())

    done = patch_io.done_kwargs()
    assert done["output_md"] == "# regenned markdown"
    assert done["authoring_mode"] == "markdown_fallback"
    assert done["content_json"] is None, "stale JSON must not survive a markdown regen"
    assert done["content_schema_version"] is None
    assert done["renderer_version"] is None


async def test_solver_regen_falling_back_to_markdown_clears_structured_fields(
    patch_io, monkeypatch
):
    """The solver regen site carried the SAME wholesale-markdown assignment as
    the judge site. Fixing only one leaves stale JSON beside new markdown."""
    monkeypatch.setattr(_settings, "solver_enabled", True)
    art_a = artifact_from_config(STRUCTURED_SOLVER_PHASE, _rlc_cfg("alpha"))
    patch_io.structured_results = [
        (art_a, 100, 50, "claude"),
        StructuredPhaseError("solver regen emitted invalid JSON"),
    ]
    patch_io.markdown_results = [("# solver regenned markdown", 120, 70, "claude")]
    patch_io.judge_outcomes = [_judge_ok(), _judge_ok()]
    patch_io.solve_outcomes = [_solve_mismatch(), _solve_agree()]

    await pipeline._execute_phase(**_make_kwargs(phase_name=STRUCTURED_SOLVER_PHASE))

    done = patch_io.done_kwargs()
    assert done["output_md"] == "# solver regenned markdown"
    assert done["authoring_mode"] == "markdown_fallback"
    assert done["content_json"] is None
    assert done["content_schema_version"] is None
    assert done["renderer_version"] is None


async def test_solver_regen_structured_persists_the_regenerated_json(patch_io, monkeypatch):
    monkeypatch.setattr(_settings, "solver_enabled", True)
    art_a = artifact_from_config(STRUCTURED_SOLVER_PHASE, _rlc_cfg("alpha"))
    art_b = artifact_from_config(STRUCTURED_SOLVER_PHASE, _rlc_cfg("bravo"))
    patch_io.structured_results = [(art_a, 100, 50, "claude"), (art_b, 130, 80, "claude")]
    patch_io.judge_outcomes = [_judge_ok(), _judge_ok()]
    patch_io.solve_outcomes = [_solve_mismatch(), _solve_agree()]

    await pipeline._execute_phase(**_make_kwargs(phase_name=STRUCTURED_SOLVER_PHASE))

    done = patch_io.done_kwargs()
    assert done["content_json"] == art_b.content_json
    assert done["output_md"] == art_b.output_md
    assert (done["tokens_input"], done["tokens_output"]) == (130, 80)


# ===========================================================================
# RED-PROOF 5 — nothing persisted before the final accepted result
# ===========================================================================

_TERMINAL = ("done", "failed", "cancelled")


async def test_exactly_one_terminal_write_across_both_regens(patch_io, monkeypatch):
    """Two regens run (judge + solver) and the phase row is written ONCE.

    An intermediate write would publish a pre-regen artifact — and, worse, the
    `guard` in set_status freezes a row once it is `done`, so an early write
    would make the accepted result unpersistable."""
    monkeypatch.setattr(_settings, "solver_enabled", True)
    arts = [artifact_from_config(STRUCTURED_SOLVER_PHASE, _rlc_cfg(w))
            for w in ("alpha", "bravo", "charlie")]
    patch_io.structured_results = [(a, 100, 50, "claude") for a in arts]
    patch_io.judge_outcomes = [_judge_major(), _judge_ok(), _judge_ok()]
    patch_io.solve_outcomes = [_solve_mismatch(), _solve_agree()]

    await pipeline._execute_phase(**_make_kwargs(phase_name=STRUCTURED_SOLVER_PHASE))

    statuses = [s for s, _ in patch_io.set_status_calls]
    terminal = [s for s in statuses if s in _TERMINAL]
    assert terminal == ["done"], f"expected exactly one terminal write, got {statuses}"
    # ...and it carries the LAST artifact, proving the single write is the final one.
    assert patch_io.done_kwargs()["content_json"] == arts[2].content_json
    assert patch_io.structured_results == [], "all three generations were consumed"


async def test_structured_success_persists_all_four_fields(patch_io):
    art = artifact_from_config(STRUCTURED_PHASE, _sentence_cfg())
    patch_io.structured_results = [(art, 90, 40, "claude")]

    await pipeline._execute_phase(**_make_kwargs())

    done = patch_io.done_kwargs()
    assert done["content_json"] == art.content_json
    assert done["authoring_mode"] == "structured"
    assert done["content_schema_version"] == "sentence_fill_config@1"
    assert done["renderer_version"] == art.renderer_version


async def test_extract_phase_records_markdown_builtin(patch_io, monkeypatch):
    """extract has no structured lane; it still records a real authoring_mode."""
    async def fake_failover(**kw):
        return "# extract summary", 10, 5, "gemini"

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)
    monkeypatch.setattr(pipeline.agent, "read_whole_book_text", lambda p: "book text " * 500)
    monkeypatch.setattr(pipeline.agent, "pdf_page_count", lambda p: 20)
    monkeypatch.setattr(pipeline.agent, "extract_text_is_oversize", lambda t: False)
    monkeypatch.setattr(pipeline.agent, "validate_extract_text", lambda t: None)
    monkeypatch.setattr(pipeline.agent, "extract_text_is_too_sparse", lambda t, n: False)

    async def no_cache(session, **kw):
        return None
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract", no_cache)

    await pipeline._execute_phase(**_make_kwargs(phase_name="extract"))

    done = patch_io.done_kwargs()
    assert done["authoring_mode"] == "markdown_builtin"
    assert done["content_json"] is None


def test_markdown_attempt_is_the_failover_driver():
    """The 9 schema-less phases must keep byte-identical behaviour: the extracted
    markdown attempt is exactly _run_with_failover, nothing more."""
    import inspect
    src = inspect.getsource(pipeline._run_markdown_attempt)
    assert "_run_with_failover" in src
    assert "artifact_from" not in src, "the markdown attempt must not build artifacts itself"


def test_structured_attempt_uses_the_failover_driver_via_a_sentinel():
    """Supersedes `test_structured_attempt_bypasses_the_failover_driver`.

    Task 7 shipped the structured lane calling `agent.run_phase` directly to
    keep a `StructuredPhaseError` out of the classify-and-retry driver — but
    that also dropped the per-attempt timeout, slot-saturation parking, error
    classification, same-provider retry and failover from EVERY structured
    call, with no layer above restoring them. The original intent is preserved
    by the `_SCHEMA_EXHAUSTED` sentinel: schema exhaustion is *returned*, not
    raised, so it still short-circuits the driver, while every transport fault
    keeps the full driver semantics."""
    import inspect
    src = inspect.getsource(pipeline._run_structured_attempt)
    assert "await _run_with_failover(" in src
    assert "agent.run_phase(" in src
    assert "_SCHEMA_EXHAUSTED" in src


def test_artifact_from_markdown_is_the_only_fallback_shape():
    art = artifact_from_markdown("# x", mode="markdown_fallback")
    assert (art.content_json, art.content_schema_version, art.renderer_version) == (
        None, None, None
    )


# ===========================================================================
# REAL CALL CHAIN (task 7b)
#
# Everything above stubs `_run_structured_attempt` itself, so it proves the
# artifact/regen/atomicity contract but says nothing about whether a REAL
# schema failure ever reaches the fallback. It did not: `agent.run_phase`
# raised a bare RuntimeError on schema exhaustion and `_run_structured_attempt`
# caught only ValidationError, so the job failed instead of falling back.
#
# These tests stub ONLY the provider boundary — `agent._spawn`, the narrowest
# seam below `run_phase` (its caller `_spawn`/`_spawn_once` is the last thing
# before argv/SDK). Real code under test: `_generate_artifact` ->
# `_run_structured_attempt` -> `_run_with_failover` -> `agent.run_phase`
# (prompt build, Pydantic validation, the one-retry-with-error-feedback loop,
# the typed `SchemaValidationExhausted` raise) -> sentinel -> fallback.
# ===========================================================================

_SPAWN_USAGE = {
    "prompt_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
    "total_tokens": 15, "raw": {},
}


@pytest.fixture()
def real_chain(monkeypatch):
    """Stub the provider boundary + the usage-row write; nothing else.

    Monkeypatching `agent.run_phase` instead would re-open the exact gap this
    file's task exists to close, so it is deliberately left real.
    """
    ns = types.SimpleNamespace(spawn_prompts=[], md_calls=[])

    async def fake_record_usage(**kw):
        return None
    monkeypatch.setattr(pipeline.agent, "_record_usage", fake_record_usage)

    # Pin the failover chain to the requested provider so the assertions do not
    # depend on which fallback CLIs happen to be installed on the test box.
    # (The requested provider is never skipped — see _run_with_failover.)
    monkeypatch.setattr(pipeline.agent, "provider_cli_installed", lambda name: False)

    async def markdown_run(prov, mdl):
        ns.md_calls.append(prov)
        return "# fallback markdown", 11, 22
    ns.markdown_run = markdown_run
    return ns


async def test_real_chain_two_invalid_structured_responses_fall_back_to_markdown(
    real_chain, monkeypatch
):
    """1. The model returns non-conforming JSON twice.

    `run_phase` validates, retries once with the validation error appended,
    validates again, and raises `SchemaValidationExhausted`. That must surface
    as a markdown fallback — NOT as a job failure.
    """
    async def fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
        real_chain.spawn_prompts.append(prompt)
        return 0, '{"totally": "not a sentence-fill config"}', dict(_SPAWN_USAGE), ""

    monkeypatch.setattr(pipeline.agent, "_spawn", fake_spawn)

    art, tin, tout, produced_by = await pipeline._generate_artifact(
        phase_name=STRUCTURED_PHASE, **_gen_kwargs(run_fn=real_chain.markdown_run)
    )

    # run_phase's REAL retry loop ran: two spawns, the second carrying the
    # validation-error feedback. (A stubbed run_phase could not show this.)
    assert len(real_chain.spawn_prompts) == 2
    assert "failed schema validation" in real_chain.spawn_prompts[1]
    # ...and the schema exhaustion was NOT retried by the failover driver.
    assert real_chain.md_calls == ["claude"]

    assert art.authoring_mode == "markdown_fallback"
    assert art.content_json is None
    assert art.output_md == "# fallback markdown"
    assert (tin, tout, produced_by) == (11, 22, "claude")


async def test_real_chain_hung_structured_call_times_out_without_falling_back(
    real_chain, monkeypatch
):
    """2. A hung provider must hit the per-attempt timeout `_run_structured_attempt`
    used to be missing entirely — and must NOT be laundered into a fallback."""
    monkeypatch.setattr(_settings, "per_attempt_timeout_seconds", 0.05)

    async def hung_spawn(**kw):
        await asyncio.sleep(60)

    monkeypatch.setattr(pipeline.agent, "_spawn", hung_spawn)

    with pytest.raises(PhaseAttemptTimeout) as excinfo:
        await pipeline._generate_artifact(
            phase_name=STRUCTURED_PHASE, **_gen_kwargs(run_fn=real_chain.markdown_run)
        )

    assert "per-attempt timeout" in str(excinfo.value)
    assert real_chain.md_calls == [], "a hung provider is not a fallback trigger"


async def test_real_chain_slot_saturation_parks_and_never_falls_back(
    real_chain, monkeypatch
):
    """3a. Fleet slot exhaustion must park the job (SlotSaturation), unretried
    and unclassified — the structured lane had lost this handling entirely."""
    async def saturated_spawn(**kw):
        real_chain.spawn_prompts.append("x")
        raise RuntimeError(f"429 {SLOT_SATURATION_MARKER} (credential=gemini:p, budget=120s)")

    monkeypatch.setattr(pipeline.agent, "_spawn", saturated_spawn)

    with pytest.raises(SlotSaturation):
        await pipeline._generate_artifact(
            phase_name=STRUCTURED_PHASE, **_gen_kwargs(run_fn=real_chain.markdown_run)
        )

    assert len(real_chain.spawn_prompts) == 1, "saturation is parked, never retried"
    assert real_chain.md_calls == []


async def test_real_chain_transport_error_retries_same_provider_and_never_falls_back(
    real_chain, monkeypatch
):
    """3b. A transport fault keeps the classify -> same-provider-retry budget
    (`hard` = 1 retry) and still propagates — never a markdown fallback."""
    async def failing_spawn(**kw):
        real_chain.spawn_prompts.append("x")
        raise RuntimeError("claude api call failed rc=1: 429 Too Many Requests")

    monkeypatch.setattr(pipeline.agent, "_spawn", failing_spawn)

    with pytest.raises(RuntimeError) as excinfo:
        await pipeline._generate_artifact(
            phase_name=STRUCTURED_PHASE, **_gen_kwargs(run_fn=real_chain.markdown_run)
        )

    assert type(excinfo.value) is RuntimeError, f"got {type(excinfo.value).__name__}"
    assert "429 Too Many Requests" in str(excinfo.value)
    # 1 attempt + 1 same-provider retry ('hard' budget). Calling run_phase
    # directly (the pre-fix shape) gave exactly 1 — no retry at all.
    assert len(real_chain.spawn_prompts) == 2
    assert real_chain.md_calls == []


async def test_cached_extract_records_markdown_builtin(patch_io, monkeypatch):
    """4. A reused extract is builtin-prompt markdown like any other extract.

    Leaving authoring_mode unset makes the row read as `markdown_legacy`, which
    the provenance contract reserves for pre-migration / NULL rows.
    """
    cached = MagicMock()
    cached.output_md = "# cached extract summary"
    cached.job_id = uuid.uuid4()
    cached.id = uuid.uuid4()

    async def has_cache(session, **kw):
        return cached
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract", has_cache)

    async def fake_record_cached(**kw):
        return None
    monkeypatch.setattr(
        pipeline.agent, "record_cached_lesson_extract", fake_record_cached
    )

    async def no_agent_call(*a, **kw):
        raise AssertionError("the cached path must not call the model")
    monkeypatch.setattr(pipeline, "_run_with_failover", no_agent_call)

    await pipeline._execute_phase(**_make_kwargs(phase_name="extract"))

    done = patch_io.done_kwargs()
    assert done["output_md"] == "# cached extract summary"
    assert done["authoring_mode"] == "markdown_builtin"
