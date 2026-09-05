"""Real artifact/reviewer boundaries; only generation, provider and DB I/O are fake."""
import types

import pytest

from app.config import settings
from app.schemas.solver import Discrepancy, SolveVerdict
from app.services import errors, pipeline, prompts, solver
from app.services.errors import PersistentContentQualityFailure, PersistentSolverMismatch
from app.services.phase_artifact import StructuredPhaseError, artifact_from_config
from app.services.phase_judge import Failure, Verdict
from tests.services.test_pipeline_structured import (
    _make_kwargs, _rlc_cfg, _sentence_cfg, _structured_enabled, patch_io,
)

_REAL_JUDGE = pipeline._judge_with_timeout
_REAL_SOLVER = solver.solve


@pytest.fixture
def review_boundary(patch_io, monkeypatch):
    monkeypatch.setattr(pipeline, "get_prompt", prompts.get_prompt)
    monkeypatch.setattr(pipeline, "get_structured_prompt", prompts.get_structured_prompt)
    monkeypatch.setattr(pipeline, "_judge_with_timeout", _REAL_JUDGE)
    monkeypatch.setattr(solver, "solve", _REAL_SOLVER)
    monkeypatch.setattr(settings, "solver_enabled", True)
    patch_io.reviews = []
    patch_io.judge_verdicts = []
    patch_io.solver_verdicts = []

    async def provider(**kw):
        patch_io.reviews.append(kw)
        bucket = (patch_io.judge_verdicts if kw["phase_name"] == "__judge__"
                  else patch_io.solver_verdicts)
        verdict = bucket.pop(0) if bucket else (
            Verdict(passed=True) if kw["phase_name"] == "__judge__"
            else SolveVerdict(agrees=True, discrepancies=[]))
        if isinstance(verdict, BaseException):
            raise verdict
        return types.SimpleNamespace(parsed=verdict)

    monkeypatch.setattr(pipeline.agent, "run_phase", provider)
    return patch_io


def _missing_evidence():
    return Verdict(passed=True, failures=[Failure(
        requirement="Supply necessary visible evidence", severity="major",
        evidence="'Use the chart' requires data, but no chart or values are supplied.")])


def _wrong_answer():
    return SolveVerdict(agrees=True, discrepancies=[Discrepancy(
        item="1", generated_key="4", solver_answer="5", confidence="high",
        explanation="The visible question asks 2 + 3, which equals 5.")])


@pytest.mark.parametrize("phase,config", [
    ("practice-rlc", _rlc_cfg), ("practice-sentence", _sentence_cfg),
])
@pytest.mark.parametrize("repair,fallback", [
    (None, False), ("judge", False), ("solver", False),
    (None, True), ("judge", True), ("solver", True),
    ("solver-then-judge", False), ("solver-then-judge", True),
])
async def test_current_artifact_selects_real_review_contract(
    review_boundary, phase, config, repair, fallback,
):
    io = review_boundary
    a = artifact_from_config(phase, config("first"))
    b = artifact_from_config(phase, config("repaired"))
    c = artifact_from_config(phase, config("judge-repaired"))
    io.structured_results = [(a, 1, 2, "claude")]
    if repair:
        io.structured_results.append((b, 3, 4, "claude"))
        if repair == "judge":
            io.judge_verdicts = [_missing_evidence()]
        else:
            io.solver_verdicts = [_wrong_answer()]
            if repair == "solver-then-judge":
                io.judge_verdicts = [Verdict(passed=True), _missing_evidence()]
                io.structured_results.append((c, 7, 8, "claude"))
    if fallback:
        io.structured_results[-1] = StructuredPhaseError("invalid JSON")
        io.markdown_results = [("# authored fallback", 5, 6, "claude")]
    kw = _make_kwargs(phase)
    kw.update(output_language="en", lesson_context="Grade 5. Current lesson facts.")
    await pipeline._execute_phase(**kw)

    assert len(io.reviews) == {None: 2, "judge": 3, "solver": 4, "solver-then-judge": 5}[repair]
    for review in io.reviews:
        prompt = review["phase_prompt"]
        contract, output = prompt.split("## CONTRACT", 1)[1].split(
            "## OUTPUT UNDER REVIEW" if review["phase_name"] == "__judge__"
            else "## OUTPUT TO CHECK", 1)
        assert review["lesson_context"] == kw["lesson_context"]
        if "# authored fallback" in output:
            assert prompts.get_prompt("english", phase, output_language="en").strip() in contract
            assert "deterministic Markdown projection" not in contract
        else:
            assert "deterministic Markdown projection" in contract
            assert prompts.get_structured_prompt("english", phase, output_language="en").strip() in contract
            assert "Answer key" in contract
            assert "not student-visible evidence" in contract
            assert "fixed renderer labels" in contract
            assert "Shared learner-quality policy" in contract
            assert any(art.output_md.strip() in output for art in (a, b, c))
            assert "## Answer key" in output
            assert ("The five ordered steps are H2 headings" if phase == "practice-rlc"
                    else "The fixed H1 is 'Sentence fill'") in contract
    done = io.done_kwargs()
    assert done["authoring_mode"] == ("markdown_fallback" if fallback else "structured")
    assert done["judge_status"] == "ok"
    final = c if repair == "solver-then-judge" else b if repair else a
    assert done["output_md"] == ("# authored fallback" if fallback else final.output_md)
    assert done["content_json"] == (None if fallback else final.content_json)


@pytest.mark.parametrize("phase,config", [
    ("practice-rlc", _rlc_cfg), ("practice-sentence", _sentence_cfg),
])
@pytest.mark.parametrize("defect", ["missing-evidence", "wrong-answer", "post-solver-major"])
async def test_structured_semantic_defects_still_block_with_bounded_repairs(
    review_boundary, phase, config, defect,
):
    io = review_boundary
    data = config().model_dump(mode="json")
    if phase == "practice-rlc":
        data["steps"][0]["prompt"] = "What is 2 + 3?" if defect == "wrong-answer" else "Use the chart: which option fits?"
        data["steps"][0]["options"][0]["label"] = "4"
        data["steps"][0]["options"][1]["label"] = "5"
    else:
        data["items"][0].update(
            passage="2 + 3 = ___." if defect == "wrong-answer" else "Use the chart: the value is ___.",
            answers=["4"], word_bank=["4", "5"],
        )
    art = artifact_from_config(phase, type(config()).model_validate(data))
    io.structured_results = [(art, 1, 2, "claude")] * 3
    if defect == "missing-evidence":
        io.judge_verdicts = [_missing_evidence()] * 2
    elif defect == "wrong-answer":
        io.solver_verdicts = [_wrong_answer()] * 2
    else:
        io.solver_verdicts = [_wrong_answer()]
        io.judge_verdicts = [Verdict(passed=True), _missing_evidence(), _missing_evidence()]
    expected = PersistentSolverMismatch if defect == "wrong-answer" else PersistentContentQualityFailure
    with pytest.raises(expected):
        await pipeline._execute_phase(**_make_kwargs(phase))
    assert len(io.structured_calls) == (3 if defect == "post-solver-major" else 2)
    assert not any(status == "done" for status, _ in io.set_status_calls)
    failed = next(data for status, data in io.set_status_calls if status == "failed")
    assert failed["content_json"] == art.content_json
    assert failed["output_md"] == art.output_md
    if defect == "wrong-answer":
        assert failed["solver_status"] == "mismatch_blocked"
    else:
        assert failed["judge_status"] == "major_blocked"


@pytest.mark.parametrize("custom", [None, "Custom Markdown: require a table and answer key."])
async def test_ordinary_and_custom_markdown_keep_their_contract(review_boundary, monkeypatch, custom):
    io = review_boundary
    monkeypatch.setattr(settings, "structured_output_enabled", custom is not None)
    io.markdown_results = [("# authored Markdown", 1, 2, "claude")]
    kw = _make_kwargs("practice-rlc")
    if custom:
        kw["custom_prompts"] = {"practice-rlc": custom}
    await pipeline._execute_phase(**kw)
    for review in io.reviews:
        assert (custom or prompts.get_prompt("english", "practice-rlc")).strip() in review["phase_prompt"]
        assert "deterministic Markdown projection" not in review["phase_prompt"]
    assert io.done_kwargs()["authoring_mode"] == ("markdown_custom" if custom else "markdown_builtin")


@pytest.mark.parametrize("reviewer", ["judge", "solver"])
@pytest.mark.parametrize("signal", [
    errors.LeaseLostSignal(), errors.CancelWonSignal(), errors.SessionLimitPause(None),
    errors.SlotSaturation("fleet credential slot wait exhausted"),
    errors.TransientPhaseError("provider unavailable"),
    errors.AuthEnvError("required provider credential missing"),
    RuntimeError("401 Invalid API key"),
])
async def test_structured_post_solver_review_preserves_control_signals(review_boundary, reviewer, signal):
    io = review_boundary
    art = artifact_from_config("practice-rlc", _rlc_cfg())
    io.structured_results = [(art, 1, 2, "claude")] * 2
    io.judge_verdicts = [Verdict(passed=True), signal if reviewer == "judge" else Verdict(passed=True)]
    io.solver_verdicts = [_wrong_answer()] + ([signal] if reviewer == "solver" else [])
    kw = _make_kwargs("practice-rlc")
    kw.update(judge_transport="api", solver_transport="api")
    with pytest.raises(type(signal)) as caught:
        await pipeline._execute_phase(**kw)
    assert caught.value is signal
    assert not any(status in {"done", "failed"} for status, _ in io.set_status_calls)


@pytest.mark.parametrize("phase,config", [
    ("practice-rlc", _rlc_cfg), ("practice-sentence", _sentence_cfg),
])
async def test_malformed_markdown_fallback_does_not_gain_projection_exceptions(review_boundary, phase, config):
    io = review_boundary
    io.structured_results = [StructuredPhaseError("invalid JSON")] * 2
    io.markdown_results = [("# No questions or answers", 1, 2, "claude")] * 2
    missing = Verdict(passed=False, failures=[Failure(requirement="Required questions and answers",
        evidence="The whole output is '# No questions or answers'", severity="major")])
    io.judge_verdicts = [missing] * 2
    with pytest.raises(PersistentContentQualityFailure):
        await pipeline._execute_phase(**_make_kwargs(phase))
    assert all("deterministic Markdown projection" not in r["phase_prompt"] for r in io.reviews)
    assert not any(status == "done" for status, _ in io.set_status_calls)
