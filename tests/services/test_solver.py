# tests/services/test_solver.py
import pytest
from types import SimpleNamespace
from app.services import solver
from app.schemas.solver import SolveVerdict, Discrepancy


def _run_stub(verdict):
    async def _r(**kw):
        return SimpleNamespace(text="", parsed=verdict, usage={}, raw_envelope={})
    return _r

COMMON = dict(subject="matematika", phase_name="memory-check", phase_output_md="...",
              lesson_context="ctx", prior_outputs={}, output_language="uz",
              solver_provider="claude", solver_model="claude-opus-4-7", transport="api",
              homework_job_id=None, phase_output_id=None)


@pytest.mark.parametrize("subject,item,key,alternative,reason", [
    ("math", "Which equals half? A: 1/2 B: 0.5", "A only", "A and B", "math-equivalent options"),
    ("english", "Choose a synonym of glad: happy / pleased", "happy only", "happy and pleased", "language synonyms without restricting context"),
    ("biology", "Classify a whale: mammal / vertebrate", "mammal only", "both categories", "scientific category overlap"),
    ("history", "Name the ruler: sovereign / monarch", "monarch only", "both terms", "historical terminology permits both"),
])
async def test_agrees_boolean_cannot_erase_defensible_second_answer(
    monkeypatch, subject, item, key, alternative, reason,
):
    verdict = SolveVerdict(agrees=True, discrepancies=[Discrepancy(
        item=item, generated_key=key, solver_answer=alternative,
        explanation=reason, confidence="high",
    )])
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(verdict))
    out = await solver.solve(**{**COMMON, "subject": subject, "contract_override": "Check all options"})
    assert out.has_mismatch and not out.agrees
    assert alternative in out.feedback


@pytest.mark.asyncio
async def test_agree_yields_no_mismatch(monkeypatch):
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(SolveVerdict(agrees=True, discrepancies=[])))
    out = await solver.solve(**COMMON)
    assert out.available and out.agrees and not out.has_mismatch


@pytest.mark.asyncio
async def test_high_confidence_disagreement_triggers_mismatch_and_feedback(monkeypatch):
    v = SolveVerdict(agrees=False, discrepancies=[Discrepancy(
        item="card 9", generated_key="Oy=xato", solver_answer="Oy=to'g'ri",
        explanation="both symmetries hold", confidence="high")])
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(v))
    out = await solver.solve(**COMMON)
    assert out.has_mismatch and "card 9" in out.feedback


@pytest.mark.asyncio
async def test_low_confidence_disagreement_does_not_regen(monkeypatch):
    v = SolveVerdict(agrees=False, discrepancies=[Discrepancy(
        item="q2", generated_key="a", solver_answer="b", explanation="maybe", confidence="low")])
    monkeypatch.setattr("app.services.agent.run_phase", _run_stub(v))
    out = await solver.solve(**COMMON)
    assert not out.has_mismatch  # advisory only


@pytest.mark.asyncio
async def test_exception_degrades_never_blocks(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr("app.services.agent.run_phase", _boom)
    out = await solver.solve(**COMMON)
    assert out.available is False and out.has_mismatch is False


@pytest.mark.asyncio
async def test_unavailable_outcome_retains_failure_for_post_mismatch_policy(monkeypatch):
    failure = ConnectionError("temporary resolver failure")

    async def _boom(**kw):
        raise failure

    monkeypatch.setattr("app.services.agent.run_phase", _boom)
    out = await solver.solve(**COMMON)
    assert out.available is False
    assert out.failure is failure


@pytest.mark.asyncio
async def test_api_auth_error_reraises(monkeypatch):
    async def _auth(**kw):
        raise __import__("app.services.agent", fromlist=["AuthEnvError"]).AuthEnvError("no key")
    monkeypatch.setattr("app.services.agent.run_phase", _auth)
    with pytest.raises(Exception):
        await solver.solve(**{**COMMON, "transport": "api"})


def test_boss_arena_prompt_carries_objective_key_guidance():
    p = solver._build_solver_prompt(
        contract="CONTRACT", phase_output_md="OUTPUT", phase_name="boss-arena")
    assert "objectively" in p.lower()
    assert "open" in p.lower()
    assert "independently SOLVES each item" in p  # generic instructions still present


def test_non_boss_prompt_has_no_boss_addendum():
    p = solver._build_solver_prompt(
        contract="C", phase_output_md="O", phase_name="memory-check")
    assert "Boss Arena phase" not in p


def test_build_solver_prompt_phase_name_optional():
    p = solver._build_solver_prompt(contract="C", phase_output_md="O")
    assert "Boss Arena phase" not in p


def test_solver_prompt_does_not_treat_curriculum_scope_as_a_closed_dictionary():
    """A less-common valid alias must be checked even when the lesson teaches another term."""
    prompt = solver._build_solver_prompt(
        contract="Use the lesson's common term.",
        phase_output_md="A) Karvonsaroy B) Rabot",
        phase_name="memory-check",
    )

    assert "Curriculum scope restricts methods, not ordinary word meanings" in prompt
    assert "rabot has a caravanserai meaning" in prompt
    assert "explicitly asking which term the supplied text uses" in prompt
    assert "reports answer correctness only" in prompt
    assert "leave phase item counts" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contract_override",
    [None, "CUSTOM MEMORY CONTRACT"],
    ids=["resolved-contract", "custom-contract"],
)
async def test_memory_solve_request_carries_recall_response_semantics(
    monkeypatch, contract_override,
):
    """Removing the memory-only clarification must break the production request."""
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text="", parsed=SolveVerdict(agrees=True, discrepancies=[]),
            usage={}, raw_envelope={},
        )

    monkeypatch.setattr("app.services.agent.run_phase", _capture)
    call = {
        **COMMON,
        "contract_override": contract_override,
        "lesson_context": "distinct lesson context",
        "prior_outputs": {"earlier-phase": "distinct prior output"},
    }

    await solver.solve(**call)

    prompt = captured.pop("phase_prompt")
    assert "Free-text recall accepts answers naming the same fact or entity" in prompt
    assert "including an expanded place name" in prompt
    assert "grading aliases, not instructions to paste each alias" in prompt
    assert "An underscored recall stem alone does not require literal insertion" in prompt
    assert "Pomir and Pomir tog'i" in prompt
    assert "Sian and Sian shahri" in prompt
    assert "explicitly told to insert a word-bank entry verbatim" in prompt
    assert "Complete the review of every item independently" in prompt
    assert "do not stop after finding problems in other cards" in prompt
    if contract_override is not None:
        assert contract_override in prompt
    assert captured == {
        "provider": "claude",
        "model": "claude-opus-4-7",
        "phase_name": "__solver__",
        "schema": SolveVerdict,
        "lesson_context": "distinct lesson context",
        "prior_outputs": {"earlier-phase": "distinct prior output"},
        "difficulty": None,
        "operation": "solve:memory-check",
        "homework_job_id": None,
        "phase_output_id": None,
        "transport": "api",
    }


@pytest.mark.asyncio
async def test_practice_sentence_request_keeps_its_addendum_without_memory_semantics(
    monkeypatch,
):
    """A memory-specific addendum must not leak into another phase contract."""
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text="", parsed=SolveVerdict(agrees=True, discrepancies=[]),
            usage={}, raw_envelope={},
        )

    monkeypatch.setattr("app.services.agent.run_phase", _capture)

    await solver.solve(
        **{
            **COMMON,
            "phase_name": "practice-sentence",
            "contract_override": "SENTENCE CONTRACT",
        }
    )

    prompt = captured["phase_prompt"]
    assert "Sentence-fill: independently fill every blank" in prompt
    assert "test all word-bank entries and defensible alternatives" in prompt
    assert "Free-text recall accepts answers" not in prompt
    assert "An underscored recall stem alone" not in prompt
