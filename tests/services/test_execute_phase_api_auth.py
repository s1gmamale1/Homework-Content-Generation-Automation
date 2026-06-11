"""api post-regen-judge (and regen-gen) auth failures fail the job LOUDLY.

The regen block in ``_execute_phase`` is wrapped in a broad ``except`` whose
job is to keep the pre-regen output when a regen exhausts providers (the common
cli case). Spec §3 carves out one exception: under ``transport="api"``, an
auth/401 error from the regen GENERATION or the POST-REGEN JUDGE must re-raise
and fail the job — consistent with the initial judge — rather than silently
degrading to the pre-regen output. A cli job keeps the graceful swallow.

These drive the real ``pipeline._execute_phase`` with DB-free mocks
(``SessionLocal`` / ``phase_repo`` / ``jobs_repo`` stubbed) so the actual guard
logic is exercised, not a copy of it.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from app.services import pipeline
from app.services import phase_judge
from app.services.phase_judge import JudgeOutcome

_AUTH_ERR = "api_error_status: 401 Invalid API key"


# --- DB-free harness -------------------------------------------------------

class _FakePhaseRow:
    def __init__(self):
        self.id = uuid4()


class _FakeSession:
    async def commit(self):
        return None


@asynccontextmanager
async def _fake_session():
    # The async-with body never touches a real session; all repo calls are stubbed.
    yield _FakeSession()


def _install_harness(monkeypatch):
    """Stub out everything _execute_phase needs to reach the judge/regen path,
    minus the LLM calls and the judge (the caller wires those per-test)."""
    monkeypatch.setattr(pipeline, "SessionLocal", _fake_session)

    async def _create_or_reset(session, **kwargs):
        return _FakePhaseRow()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", _create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", _noop)
    monkeypatch.setattr(pipeline, "get_prompt", lambda subject, phase: "PROMPT")
    monkeypatch.setattr(pipeline, "get_prompt_hash", lambda subject, phase: "hash")
    monkeypatch.setattr(pipeline, "max_output_tokens_for", lambda phase: 1024)

    # Same-provider retry backoff would add real ~2s sleeps; no-op them so the
    # non-auth-regen-failure test (which exhausts the hard-error retry budget)
    # stays fast and the gen-call counter stays deterministic.
    async def _no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", _no_sleep)


def _run_execute_phase(transport: str):
    return asyncio.run(pipeline._execute_phase(
        job_id=uuid4(),
        phase_name="case-based-preview",
        phase_order=1,
        subject="biology",
        provider="claude",
        model="claude-sonnet-4-6",
        pdf_path=Path("/tmp/does-not-matter.pdf"),
        attach_file=False,
        section={"id": None, "title": "T", "number": "1",
                 "page_start": 1, "page_end": 2},
        lesson_context=None,
        prior_outputs={},
        difficulty=None,
        transport=transport,
    ))


# A judge result that triggers the one regen (available + has_major).
_MAJOR = JudgeOutcome(
    available=True, passed=False,
    warnings=["[major] req — evidence"],
    feedback="\nFIX THIS", has_major=True,
)


def _make_judge_then_raise(exc: Exception):
    """First judge() call returns _MAJOR (→ regen); second call raises `exc`
    (the post-regen judge auth failure)."""
    calls = {"n": 0}

    async def _judge(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _MAJOR
        raise exc

    return _judge


async def _gen_ok(**kwargs):
    # agent.run_phase_prompt stand-in: (text, tin, tout)
    return "GENERATED", 10, 20


# --- tests -----------------------------------------------------------------

def test_api_post_regen_judge_auth_error_reraises(monkeypatch):
    """api + post-regen judge auth error → _execute_phase fails loudly."""
    _install_harness(monkeypatch)
    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _gen_ok)
    monkeypatch.setattr(
        pipeline.phase_judge, "judge",
        _make_judge_then_raise(RuntimeError(_AUTH_ERR)),
    )

    with pytest.raises(RuntimeError, match="401"):
        _run_execute_phase("api")


def test_cli_post_regen_judge_auth_error_swallowed(monkeypatch):
    """cli + same post-regen judge auth error → swallowed; phase completes
    with the pre-regen output (no raise). Proves the guard still shields cli."""
    _install_harness(monkeypatch)
    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _gen_ok)
    monkeypatch.setattr(
        pipeline.phase_judge, "judge",
        _make_judge_then_raise(RuntimeError(_AUTH_ERR)),
    )

    out_md, tin, tout, prompt_hash, parsed = _run_execute_phase("cli")
    # Regen ran (generation succeeded) but post-regen judge auth error was
    # swallowed → phase still completes 'done'.
    assert out_md == "GENERATED"


def test_api_non_auth_regen_failure_swallowed(monkeypatch):
    """api + NON-auth regen GENERATION failure → swallowed (keeps pre-regen
    output, no raise). Proves we didn't break the guard's primary purpose."""
    _install_harness(monkeypatch)

    gen_calls = {"n": 0}

    async def _gen(**kwargs):
        gen_calls["n"] += 1
        if gen_calls["n"] == 1:
            return "PRE_REGEN", 10, 20
        # The regen generation exhausts providers (non-auth) and raises.
        raise RuntimeError("all providers exhausted :: malformed response envelope")

    monkeypatch.setattr(pipeline.agent, "run_phase_prompt", _gen)

    async def _judge(**kwargs):
        # Initial judge → regen; (no second judge runs, regen-gen raises first).
        return _MAJOR

    monkeypatch.setattr(pipeline.phase_judge, "judge", _judge)

    # sanity: the error is genuinely non-auth
    assert not phase_judge._is_auth_error(
        RuntimeError("all providers exhausted :: malformed response envelope")
    )

    out_md, tin, tout, prompt_hash, parsed = _run_execute_phase("api")
    # Non-auth regen failure is swallowed → keep the pre-regen output.
    assert out_md == "PRE_REGEN"
