"""Learner acceptance must belong to the final artifact and have no known major."""
import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import errors, pipeline
from app.services.lease import CancelRequested, JobLease, LeaseLost
from tests.services.test_pipeline_judge_status import _major, _minor, _ok, _unavail
from tests.services.test_pipeline_solver import _agree, _make_kwargs, _mismatch, patch_io


def review(monkeypatch, *outcomes):
    judge = AsyncMock(side_effect=outcomes)
    monkeypatch.setattr(pipeline, "_judge_with_timeout", judge)
    return judge


def blocked_write(patch_io):
    assert not [c for c in patch_io.set_status_calls if c[0] == "done"]
    failed = [c[1] for c in patch_io.set_status_calls if c[0] == "failed"]
    assert len(failed) == 1
    assert failed[0]["judge_status"] == "major_blocked"
    return failed[0]


@pytest.mark.parametrize("budget", [0, 1, 2])
async def test_persistent_major_is_terminal_even_without_regeneration(monkeypatch, patch_io, budget):
    monkeypatch.setattr(settings, "max_judge_regens", budget)
    patch_io.failover_outputs = [(f"# attempt {i}", 100+i, 50+i, "claude") for i in range(budget+1)]
    review(monkeypatch, *[_major() for _ in range(budget+1)])
    with pytest.raises(Exception) as caught:
        await pipeline._execute_phase(**_make_kwargs())
    assert type(caught.value).__name__ == "PersistentContentQualityFailure"
    failed = blocked_write(patch_io)
    assert failed["output_md"] == f"# attempt {budget}"
    assert failed["tokens_input"] == 100 + budget
    assert "MAJOR: content issue" in failed["validation_warnings"]


async def test_hard_failed_major_repair_retains_original(monkeypatch, patch_io):
    review(monkeypatch, _major())
    monkeypatch.setattr(pipeline, "_run_with_failover", AsyncMock(side_effect=[
        ("# original", 10, 5, "claude"), RuntimeError("invalid repair"),
    ]))
    with pytest.raises(Exception) as caught:
        await pipeline._execute_phase(**_make_kwargs())
    assert type(caught.value).__name__ == "PersistentContentQualityFailure"
    assert str(caught.value.repair_error) == "invalid repair"
    assert blocked_write(patch_io)["output_md"] == "# original"


@pytest.mark.parametrize("final", [_ok(), _minor()])
async def test_repaired_major_can_pass_with_minor_warnings(monkeypatch, patch_io, final):
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# repaired", 20, 8, "gemini")]
    review(monkeypatch, _major(), final)
    await pipeline._execute_phase(**_make_kwargs())
    done = [c[1] for c in patch_io.set_status_calls if c[0] == "done"][0]
    assert done["output_md"] == "# repaired" and done["judge_status"] == "ok"
    assert done["provider"] == "gemini" and done["tokens_input"] == 20


async def test_minor_only_never_regenerates(monkeypatch, patch_io):
    review(monkeypatch, _minor())
    await pipeline._execute_phase(**_make_kwargs())
    done = [c[1] for c in patch_io.set_status_calls if c[0] == "done"][0]
    assert "MINOR: style" in done["validation_warnings"]
    assert len(patch_io.failover_calls) == 1


async def test_known_major_unavailable_recheck_cannot_clear_block(monkeypatch, patch_io):
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# unchecked repair", 20, 8, "gemini")]
    review(monkeypatch, _major(), _unavail(), _unavail())
    with pytest.raises(Exception) as caught:
        await pipeline._execute_phase(**_make_kwargs())
    assert type(caught.value).__name__ == "PersistentContentQualityFailure"
    failed = blocked_write(patch_io)
    assert failed["output_md"] == "# unchecked repair"
    assert "MAJOR: content issue" in failed["validation_warnings"]


async def test_known_major_without_warning_text_still_blocks_unavailable_recheck(monkeypatch, patch_io):
    major = _major()
    major.warnings = []
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# unchecked", 20, 8, "gemini")]
    review(monkeypatch, major, _unavail(), _unavail())
    with pytest.raises(errors.PersistentContentQualityFailure):
        await pipeline._execute_phase(**_make_kwargs())
    assert blocked_write(patch_io)["output_md"] == "# unchecked"


@pytest.mark.parametrize("signal", [errors.LeaseLostSignal(), errors.CancelWonSignal(),
    errors.SessionLimitPause(None), errors.SlotSaturation("fleet credential slot wait exhausted"),
    errors.TransientPhaseError("provider failed"), asyncio.CancelledError(), ConnectionError("connection reset")])
async def test_major_repair_control_and_transient_signals_propagate(monkeypatch, patch_io, signal):
    review(monkeypatch, _major())
    monkeypatch.setattr(pipeline, "_run_with_failover", AsyncMock(side_effect=[
        ("# initial", 10, 5, "claude"), signal,
    ]))
    with pytest.raises(type(signal)):
        await pipeline._execute_phase(**_make_kwargs())
    assert not [c for c in patch_io.set_status_calls if c[0] in {"failed", "done"}]


@pytest.mark.parametrize("sentinel,signal", [(LeaseLost, errors.LeaseLostSignal), (CancelRequested, errors.CancelWonSignal)])
async def test_blocked_write_is_fenced(monkeypatch, patch_io, sentinel, signal):
    monkeypatch.setattr(settings, "max_judge_regens", 0)
    review(monkeypatch, _major())
    captured = []
    async def set_status(session, po_id, status, **kw):
        if status == "failed":
            captured.append(kw)
            return sentinel
    monkeypatch.setattr(pipeline.phase_repo, "set_status", set_status)
    kw = _make_kwargs()
    kw["lease"] = JobLease(job_id=kw["job_id"], claim_token=uuid.uuid4(), owner_id="test")
    with pytest.raises(signal):
        await pipeline._execute_phase(**kw)
    assert captured[0]["claim_token"] == kw["lease"].claim_token


async def test_solver_repaired_artifact_requires_its_own_judge(monkeypatch, patch_io):
    monkeypatch.setattr(settings, "max_judge_regens", 0)
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# bad solver repair", 20, 8, "gemini")]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    judge = review(monkeypatch, _ok(), _major())
    with pytest.raises(Exception) as caught:
        await pipeline._execute_phase(**_make_kwargs())
    assert type(caught.value).__name__ == "PersistentContentQualityFailure"
    assert blocked_write(patch_io)["output_md"] == "# bad solver repair"
    assert judge.call_args_list[-1].kwargs["output_md"] == "# bad solver repair"


async def test_judge_repair_after_solver_repair_is_solved_again(monkeypatch, patch_io):
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# solver repair", 20, 8, "gemini"), ("# final", 30, 9, "claude")]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    review(monkeypatch, _ok(), _major(), _ok())
    await pipeline._execute_phase(**_make_kwargs())
    done = [c[1] for c in patch_io.set_status_calls if c[0] == "done"][0]
    assert done["output_md"] == "# final"
    assert patch_io.solve_calls[-1]["phase_output_md"] == "# final"


@pytest.mark.parametrize("phase", ["case-based-preview", "practice-sentence"])
async def test_additional_answer_bearing_phases_are_solved(patch_io, phase):
    await pipeline._execute_phase(**_make_kwargs(phase))
    assert patch_io.solver_status == "ok"
    assert patch_io.solve_calls[0]["phase_name"] == phase


def test_content_failure_quotes_never_become_provider_transients():
    error_type = getattr(errors, "PersistentContentQualityFailure", None)
    assert error_type is not None
    exc = error_type("memory-check", ['[major] The quoted word "timeout" is not supplied evidence'])
    assert pipeline._requeue_worthy(exc) is False


@pytest.mark.parametrize("structured_final", [True, False])
async def test_blocked_artifact_keeps_matching_rendered_and_structured_data(
    monkeypatch, patch_io, structured_final,
):
    from app.services.phase_artifact import artifact_from_config, artifact_from_markdown
    from tests.services.test_pipeline_structured import _sentence_cfg

    original = artifact_from_config("practice-sentence", _sentence_cfg("cat"))
    final = (artifact_from_config("practice-sentence", _sentence_cfg("fox"))
             if structured_final else artifact_from_markdown("# final fallback", mode="markdown_fallback"))
    monkeypatch.setattr(pipeline, "_generate_artifact", AsyncMock(side_effect=[
        (original, 10, 5, "claude"), (final, 30, 12, "gemini"),
    ]))
    judge = review(monkeypatch, _major(), _major())
    with pytest.raises(errors.PersistentContentQualityFailure):
        await pipeline._execute_phase(**_make_kwargs("practice-sentence"))
    failed = blocked_write(patch_io)
    assert failed["output_md"] == judge.call_args_list[-1].kwargs["output_md"]
    assert failed["output_md"] == final.output_md
    assert failed["content_json"] == final.content_json
    assert failed["content_schema_version"] == final.content_schema_version
    assert failed["renderer_version"] == final.renderer_version
    assert failed["authoring_mode"] == final.authoring_mode
    assert failed["tokens_input"] == 30 and failed["provider"] == "gemini"
    if structured_final:
        assert failed["content_json"]["items"][0]["answers"] == ["fox"]
    else:
        assert failed["content_json"] is None


async def test_unavailable_judge_after_solver_repair_is_not_reported_as_verified(monkeypatch, patch_io):
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# repair", 20, 8, "gemini")]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    review(monkeypatch, _ok(), _unavail(), _unavail())
    await pipeline._execute_phase(**_make_kwargs())
    done = [c[1] for c in patch_io.set_status_calls if c[0] == "done"][0]
    assert done["judge_status"] == "unavailable"
    assert done["output_md"] == "# repair"


async def test_failed_judge_call_after_solver_repair_cannot_retain_old_ok(monkeypatch, patch_io):
    patch_io.failover_outputs = [("# initial", 10, 5, "claude"), ("# repair", 20, 8, "gemini")]
    patch_io.solve_outputs = [_mismatch()]
    review(monkeypatch, _ok(), RuntimeError("unparseable review"))
    with pytest.raises(errors.PersistentSolverMismatch):
        await pipeline._execute_phase(**_make_kwargs())
    failed = [c[1] for c in patch_io.set_status_calls if c[0] == "failed"][0]
    assert failed["output_md"] == "# repair"
    assert failed["judge_status"] != "ok"


@pytest.mark.parametrize("stage,api_transport", [
    ("generation", "transport"),
    ("judge", "judge_transport"),
    ("solver", "solver_transport"),
])
@pytest.mark.parametrize("auth_error", [
    RuntimeError("api_error_status: 401 Invalid API key"),
    errors.AuthEnvError("required provider credential missing"),
])
async def test_auth_error_after_solver_repair_preserves_infrastructure_failure(
    monkeypatch, patch_io, stage, api_transport, auth_error,
):
    patch_io.failover_outputs = [
        ("# initial", 10, 5, "claude"), ("# solver repair", 20, 8, "gemini"),
    ]
    patch_io.solve_outputs = [_mismatch()]
    judge = review(monkeypatch, _ok(), auth_error if stage == "judge" else _ok())
    if stage == "generation":
        monkeypatch.setattr(pipeline, "_run_with_failover", AsyncMock(side_effect=[
            ("# initial", 10, 5, "claude"), auth_error,
        ]))
    elif stage == "solver":
        monkeypatch.setattr(pipeline.solver, "solve", AsyncMock(side_effect=[_mismatch(), auth_error]))
    kw = _make_kwargs()
    kw[api_transport] = "api"
    with pytest.raises(type(auth_error)) as caught:
        await pipeline._execute_phase(**kw)
    assert caught.value is auth_error
    assert not [c for c in patch_io.set_status_calls if c[0] in {"done", "failed"}]
    if stage == "judge":
        assert judge.call_args.kwargs["output_md"] == "# solver repair"


@pytest.mark.parametrize("reviewer", ["judge", "solver"])
@pytest.mark.parametrize("signal", [errors.LeaseLostSignal(), errors.CancelWonSignal(),
    errors.SessionLimitPause(None), errors.TransientPhaseError("provider error")])
async def test_review_services_do_not_swallow_control_signals(monkeypatch, reviewer, signal):
    from app.services import agent, phase_judge, solver
    monkeypatch.setattr(agent, "run_phase", AsyncMock(side_effect=signal))
    common = dict(subject="biology", phase_name="memory-check", lesson_context="ctx", prior_outputs={})
    with pytest.raises(type(signal)):
        if reviewer == "judge":
            await phase_judge.judge(**common, output_md="Q", gen_provider="claude", gen_model=None,
                                    judge_provider="claude", judge_model=None)
        else:
            await solver.solve(**common, phase_output_md="Q", solver_provider="claude", solver_model=None)


@pytest.mark.parametrize("quoted", ["timeout", "429", "fleet credential slot wait exhausted"])
async def test_real_phase_failure_marks_job_terminal_with_provider_words(monkeypatch, patch_io, quoted):
    from tests.services.test_pipeline_transient_propagation import _phase_kwargs
    monkeypatch.setattr(settings, "max_judge_regens", 0)
    major = _major()
    major.warnings = [f"[major] Missing evidence for '{quoted}'"]
    review(monkeypatch, major)
    jobs = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", jobs)
    with pytest.raises(errors.PersistentContentQualityFailure):
        await pipeline._execute_one_phase(**_phase_kwargs(phase_name="memory-check"))
    failed = blocked_write(patch_io)
    assert quoted in failed["error_message"]
    assert jobs.call_args.args[2] == "failed"


async def test_teacher_pack_post_repair_failure_retains_advisory_policy(monkeypatch, patch_io):
    monkeypatch.setattr(settings, "teacher_pack_gate_retries", 0)
    patch_io.failover_outputs = [("# teacher initial", 10, 5, "claude"), ("# teacher repair", 20, 8, "claude")]
    review(monkeypatch, _major(), RuntimeError("invalid review"))
    await pipeline._execute_phase(**_make_kwargs("teacher-pack"))
    done = [c[1] for c in patch_io.set_status_calls if c[0] == "done"][0]
    assert done["judge_status"] == "major_regen_failed"
    assert done["output_md"] == "# teacher repair"
