"""Offline safety and assertion tests for the paid solver acceptance harness.

These tests never import the application DB engine and never call a provider.
They pin the two boundaries that matter before the separately-approved smoke:
the exact remaining-budget arming gesture and the five acceptance outcomes.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts import smoke_solver_fail_closed as smoke


_SAFE_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://edu:secret@127.0.0.1/edu_scratch_solver_smoke",
    "SOURCE_DB_URL": "postgresql://reader:secret@127.0.0.1/edu_copy",
}


def _passing_snapshot() -> smoke.AcceptanceSnapshot:
    return smoke.AcceptanceSnapshot(
        blocked_phase_status="failed",
        blocked_solver_status="mismatch_blocked",
        blocked_output_md=smoke.FINAL_WRONG_OUTPUT,
        blocked_error_message="persistent answer-key mismatch after repair",
        blocked_job_status="failed",
        blocked_job_attempts=1,
        blocked_job_claimed_by=smoke.WORKER_ID,
        blocked_job_claim_token="claim-token",
        expected_claim_token="claim-token",
        blocked_job_completed=True,
        blocked_phase_completed_events=0,
        blocked_job_completed_events=0,
        blocked_archive_calls=0,
        usage_rows=(
            smoke.UsageRecord(
                operation="solve:memory-check",
                model_name="gemini-3.1-pro-preview",
                prompt_tokens=12_000,
                output_tokens=2_000,
                cached_tokens=0,
                cost_usd=Decimal("0.048"),
            ),
            smoke.UsageRecord(
                operation="solve:memory-check",
                model_name="gemini-3.1-pro-preview",
                prompt_tokens=11_000,
                output_tokens=1_500,
                cached_tokens=0,
                cost_usd=Decimal("0.040"),
            ),
        ),
        mismatch_detections=2,
        control_phase_status="done",
        control_solver_status="ok",
        control_job_status="done",
        control_generation_calls=1,
        control_archive_calls=1,
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--max-cost-usd", "0.175"],
        ["--max-cost-usd", "0.20"],
        ["--max-cost-usd", "0.1753760"],
    ],
)
def test_runner_cannot_start_without_the_exact_approved_cap(argv):
    """Removing/narrowing the exact-cap guard would invoke the dangerous runner."""
    calls = 0

    async def dangerous_runner(_preflight):
        nonlocal calls
        calls += 1
        raise AssertionError("paid runner must not start")

    with pytest.raises(smoke.PreflightError, match=r"--max-cost-usd 0\.175376"):
        smoke.main(argv, environ=_SAFE_ENV, runner=dangerous_runner)
    assert calls == 0


def test_runner_cannot_start_when_target_database_is_not_scratch():
    """Deleting the scratch-name check would expose production to smoke writes."""
    calls = 0

    async def dangerous_runner(_preflight):
        nonlocal calls
        calls += 1

    env = dict(_SAFE_ENV, DATABASE_URL="postgresql+asyncpg://edu:secret@db/edu_copy")
    with pytest.raises(smoke.PreflightError, match="scratch/test database"):
        smoke.main(
            ["--max-cost-usd", "0.175376"], environ=env, runner=dangerous_runner
        )
    assert calls == 0


def test_runner_cannot_start_without_an_explicit_separate_source_database():
    """Defaulting SOURCE_DB_URL could accidentally turn the source into a writer."""
    calls = 0

    async def dangerous_runner(_preflight):
        nonlocal calls
        calls += 1

    env = {"DATABASE_URL": _SAFE_ENV["DATABASE_URL"]}
    with pytest.raises(smoke.PreflightError, match="SOURCE_DB_URL"):
        smoke.main(
            ["--max-cost-usd", "0.175376"], environ=env, runner=dangerous_runner
        )
    assert calls == 0


def test_exact_cap_and_database_boundaries_arm_one_runner_invocation():
    """Over-tightening the guard must not make the approved invocation unusable."""
    seen = []

    async def safe_runner(preflight):
        seen.append(preflight)

    assert smoke.main(
        ["--max-cost-usd", "0.175376"], environ=_SAFE_ENV, runner=safe_runner
    ) == 0
    assert len(seen) == 1
    assert seen[0].max_cost_usd == Decimal("0.175376")
    assert seen[0].max_paid_calls == 2


@pytest.mark.asyncio
async def test_paid_call_gate_never_invokes_a_third_model_call():
    """Removing the two-call ceiling would permit an unapproved third charge."""
    calls = 0

    async def provider_call(**_kwargs):
        nonlocal calls
        calls += 1
        return "ok"

    gate = smoke.PaidSolverGate(smoke.APPROVED_CAP)
    assert await gate.call(provider_call) == "ok"
    assert await gate.call(provider_call) == "ok"
    with pytest.raises(smoke.BudgetExceeded, match="two paid solver calls"):
        await gate.call(provider_call)
    assert calls == 2


@pytest.mark.asyncio
async def test_spawn_retry_cannot_make_a_third_actual_provider_attempt(monkeypatch):
    """A transient retry loop must hit the paid gate before attempt three."""
    from app.services import agent

    actual_provider_attempts = 0

    async def transient_provider(**_kwargs):
        nonlocal actual_provider_attempts
        actual_provider_attempts += 1
        return 1, "", {}, "429 RESOURCE_EXHAUSTED"

    gate = smoke.PaidSolverGate(smoke.APPROVED_CAP)

    async def bounded_once(**kwargs):
        return await gate.call(transient_provider, **kwargs)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(agent, "_spawn_once", bounded_once)
    monkeypatch.setattr(agent.settings, "rate_limit_max_retries", 4)
    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(agent, "_rate_limit_delay", lambda _attempt: 0.0)

    with pytest.raises(smoke.BudgetExceeded, match="two paid solver calls"):
        await agent._spawn(
            provider=agent.get_provider("gemini"),
            model=smoke.SOLVER_MODEL,
            prompt="bounded retry probe",
            attachments=[],
            transport="api",
        )

    assert gate.calls == 2
    assert actual_provider_attempts == 2


def test_paid_smoke_wraps_spawn_once_not_the_retry_driver():
    """The live harness must count actual transport attempts, not solver calls."""
    import inspect

    source = inspect.getsource(smoke._run_paid_smoke)
    assert "real_spawn_once = agent._spawn_once" in source
    assert 'patch.object(agent, "_spawn_once", bounded_spawn_once)' in source
    assert 'patch.object(agent, "_spawn",' not in source


def test_all_five_acceptance_assertions_pass_for_the_literal_control_snapshot():
    """Breaking any acceptance invariant must make the pure verifier reject it."""
    report = smoke.assert_acceptance(_passing_snapshot(), smoke.APPROVED_CAP)
    assert report.total_cost_usd == Decimal("0.088")
    assert report.paid_calls == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"blocked_phase_status": "done"}, "blocked phase"),
        ({"blocked_job_attempts": 2}, "attempts/lease"),
        ({"blocked_job_completed_events": 1}, "completion/archive"),
        ({"mismatch_detections": 1}, "both solver calls"),
        ({"control_generation_calls": 2}, "control fixture"),
    ],
)
def test_each_acceptance_category_fails_closed(change, message):
    """Each row names a production regression that the paid result must catch."""
    values = vars(_passing_snapshot()) | change
    with pytest.raises(smoke.AcceptanceFailure, match=message):
        smoke.assert_acceptance(smoke.AcceptanceSnapshot(**values), smoke.APPROVED_CAP)


def test_usage_assertion_rejects_zero_tokens_wrong_model_and_overspend():
    """A parsed verdict without correctly-priced token evidence is not acceptance."""
    base = _passing_snapshot()
    bad_rows = (
        smoke.UsageRecord(
            operation="solve:memory-check",
            model_name="gemini-3.1-pro-preview",
            prompt_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=Decimal("0"),
        ),
        smoke.UsageRecord(
            operation="solve:memory-check",
            model_name="wrong-model",
            prompt_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            cost_usd=Decimal("0.21"),
        ),
    )
    with pytest.raises(smoke.AcceptanceFailure, match="usage rows"):
        smoke.assert_acceptance(
            smoke.AcceptanceSnapshot(**(vars(base) | {"usage_rows": bad_rows})),
            smoke.APPROVED_CAP,
        )
