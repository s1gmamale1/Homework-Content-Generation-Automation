"""Unit tests for ``app/services/regeneration_estimator.py``.

Every expectation here is hand-derived from ``pricing.PRICE_MAP`` and the
fixture token counts — no test recomputes the number the way the code does.

Fixed inputs throughout: a fixed ``now`` (so the 30-day window is an exact
pair of timestamps, not a moving one), fixed usage observations, and a fixed
``ResolvedLaunchContract``. The observation SELECT is asserted structurally
(the fake session cannot apply a WHERE clause); that the window boundary,
``success`` and ``auth_mode='api'`` filters really select those rows is proven
against a real Postgres in
``tests/integration/test_regeneration_source_and_version_queries.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.schemas.regeneration_contract import LaunchContract, ResolvedLaunchContract
from app.services.regeneration_planner import build_phase_plan

SUBJECT = "math-algebra"
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

# Rates read off pricing.PRICE_MAP ($ per 1M tokens), 2026-08-20:
#   gemini-3.5-flash        input 1.50  output 9.00
#   gemini-3.6-flash        input 1.50  output 7.50
#   gemini-3.5-flash-lite   input 0.30  output 2.50
GEN_MODEL = "gemini-3.5-flash"
JUDGE_MODEL = "gemini-3.6-flash"
ROLE_MODEL = "gemini-3.5-flash-lite"


def _contract(**overrides) -> ResolvedLaunchContract:
    kwargs = dict(
        provider="gemini",
        model=GEN_MODEL,
        transport="api",
        extract_provider="gemini",
        extract_model=ROLE_MODEL,
        judge_provider="gemini",
        judge_model=JUDGE_MODEL,
        solver_provider="gemini",
        solver_model=ROLE_MODEL,
        session_limit_strategy="pause",
    )
    kwargs.update(overrides)
    return ResolvedLaunchContract(**kwargs)


def _source(**overrides):
    from app.services.regeneration_discovery import EligibleRegenerationSource

    kwargs = dict(
        source_job_id=uuid.uuid4(),
        toc_entry_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        subject=SUBJECT,
        grade="7",
        output_language="uz",
        source_publication_version=1,
        next_expected_version=2,
        source_is_revision=False,
        book_filename="algebra-7.pdf",
        section_number="1",
        section_title="Kirish",
        chapter_title="I bob",
        page_start=4,
        notion_lesson_page_id=None,
        order_index=0,
    )
    kwargs.update(overrides)
    return EligibleRegenerationSource(**kwargs)


def _obs(operation, phase, model, *, prompt, output, cached=0, cache_creation=0, n=4):
    """One pre-aggregated observation row, in the SELECT's column order."""
    return (operation, phase, "gemini", model, prompt, output, cached, cache_creation, n)


class _FakeSession:
    def __init__(self, rows: list[tuple]):
        self._rows = rows
        self.statements: list[Any] = []

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _R(self._rows)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


@pytest.fixture(autouse=True)
def _fixed_config(monkeypatch):
    """Pin every configuration input the estimate reads, so the arithmetic in
    these tests cannot move when a default changes."""
    from app.config import settings

    monkeypatch.setattr(settings, "solver_enabled", True, raising=False)
    monkeypatch.setattr(settings, "max_judge_regens", 1, raising=False)
    monkeypatch.setattr(settings, "max_solve_regens", 1, raising=False)
    monkeypatch.setattr(settings, "structured_output_enabled", False, raising=False)


def _plan(selected):
    return build_phase_plan(subject=SUBJECT, selected_phases=selected)


def _one_target_setup(selected=("reflection",), rows=(), **plan_kwargs):
    source = _source()
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=list(selected), **plan_kwargs
    )
    return source, {source.source_job_id: plan}, _FakeSession(list(rows))


# unit costs, hand-derived:
#   authoring  10,000 prompt × $1.50/M + 1,000 output × $9.00/M = 0.015 + 0.009
AUTHORING_UNIT = 0.024
#   judge       8,000 prompt × $1.50/M +   500 output × $7.50/M = 0.012 + 0.00375
JUDGE_UNIT = 0.01575
#   solver      6,000 prompt × $0.30/M +   400 output × $2.50/M = 0.0018 + 0.001
SOLVER_UNIT = 0.0028
#   extract    40,000 prompt × $0.30/M + 3,000 output × $2.50/M = 0.012 + 0.0075
EXTRACT_UNIT = 0.0195

_AUTHORING_OBS = _obs("phase.run", "reflection", GEN_MODEL, prompt=10_000, output=1_000)
_JUDGE_OBS = _obs("judge:reflection", "reflection", JUDGE_MODEL, prompt=8_000, output=500)


# ───────────────────────── the observation query ─────────────────────


@pytest.mark.asyncio
async def test_the_window_is_exactly_thirty_days_ending_at_now():
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup()
    est = await estimator.estimate_regeneration(
        session,
        targets=[source],
        plans=plans,
        launch_contract=_contract(),
        now=NOW,
    )

    assert est.window_start == NOW - timedelta(days=30)
    assert est.window_end == NOW
    bound_values = set(_params(session.statements[0]).values())
    assert NOW - timedelta(days=30) in bound_values
    assert NOW in bound_values


@pytest.mark.asyncio
async def test_only_successful_api_calls_joined_to_a_phase_row_are_observed():
    """The INNER JOIN is load-bearing: a usage row with no phase_output_id (a
    TOC extraction, a golden eval) is not evidence about a homework phase."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup()
    await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    sql = _sql(session.statements[0])
    assert "JOIN phase_outputs ON phase_outputs.id = agent_usages.phase_output_id" in sql
    assert "agent_usages.auth_mode = " in sql
    assert "agent_usages.success" in sql
    assert "agent_usages.started_at >= " in sql
    assert "agent_usages.started_at <= " in sql
    assert "GROUP BY" in sql
    params = _params(session.statements[0])
    assert "api" in params.values()


# ─────────────────────────── base arithmetic ─────────────────────────


@pytest.mark.asyncio
async def test_authoring_and_judge_are_priced_once_per_regenerated_phase():
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    assert est.low_usd == pytest.approx(AUTHORING_UNIT + JUDGE_UNIT)
    base = [li for li in est.line_items if li.budget == "base"]
    assert {(li.kind, li.phase, li.calls_low) for li in base} == {
        ("authoring", "reflection", 1),
        ("judge", "reflection", 1),
    }
    assert all(li.basis.startswith("observed") for li in base)
    assert all(li.observations == 4 for li in base)
    # The judge is priced with the CONTRACT's judge model, not the content one.
    assert {li.model for li in base if li.kind == "judge"} == {JUDGE_MODEL}
    assert {li.model for li in base if li.kind == "authoring"} == {GEN_MODEL}


@pytest.mark.asyncio
async def test_two_targets_double_the_estimate():
    from app.services import regeneration_estimator as estimator

    a, b = _source(), _source()
    plan = _plan(["reflection"])
    session = _FakeSession([_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session,
        targets=[a, b],
        plans={a.source_job_id: plan, b.source_job_id: plan},
        launch_contract=_contract(),
        now=NOW,
    )
    assert est.target_count == 2
    assert est.low_usd == pytest.approx(2 * (AUTHORING_UNIT + JUDGE_UNIT))


@pytest.mark.asyncio
async def test_copied_phases_and_a_copied_extract_cost_zero():
    """A selective campaign's whole point: 11 of 12 phases are copied forward,
    and the estimate must charge for exactly one."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    assert est.regenerated_phase_count == 1
    assert est.copied_phase_count == 11        # 10 content phases + extract
    assert est.copied_extract_count == 1
    assert est.regenerated_extract_count == 0
    priced_phases = {li.phase for li in est.line_items}
    assert priced_phases == {"reflection"}
    assert not any(li.kind == "extract" for li in est.line_items)


@pytest.mark.asyncio
async def test_refresh_extraction_adds_one_extract_call_at_the_extract_role_price():
    from app.services import regeneration_estimator as estimator

    extract_obs = _obs("lesson.extract", "extract", ROLE_MODEL, prompt=40_000, output=3_000)
    source = _source()
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=["reflection"], refresh_extraction=True
    )
    session = _FakeSession([_AUTHORING_OBS, _JUDGE_OBS, extract_obs])
    est = await estimator.estimate_regeneration(
        session,
        targets=[source],
        plans={source.source_job_id: plan},
        launch_contract=_contract(),
        now=NOW,
    )

    (extract_line,) = [li for li in est.line_items if li.kind == "extract"]
    assert extract_line.calls_low == 1
    assert extract_line.model == ROLE_MODEL
    assert extract_line.cost_low_usd == pytest.approx(EXTRACT_UNIT)
    assert est.regenerated_extract_count == 1
    assert est.copied_extract_count == 0
    # refresh_extraction pulls every content phase into the closure.
    assert est.regenerated_phase_count == 11


@pytest.mark.asyncio
async def test_the_solver_is_priced_only_for_solver_bearing_phases():
    from app.services import regeneration_estimator as estimator

    solver_obs = _obs("solve:boss-arena", "boss-arena", ROLE_MODEL, prompt=6_000, output=400)
    source = _source()
    plan = _plan(["boss-arena"])  # boss-arena + reflection; only the first solves
    session = _FakeSession([solver_obs])
    est = await estimator.estimate_regeneration(
        session,
        targets=[source],
        plans={source.source_job_id: plan},
        launch_contract=_contract(),
        now=NOW,
    )

    solver_lines = [li for li in est.line_items if li.kind == "solver"]
    assert {li.phase for li in solver_lines} == {"boss-arena"}
    base_solver = [li for li in solver_lines if li.budget == "base"]
    assert len(base_solver) == 1
    assert base_solver[0].calls_low == 1
    assert base_solver[0].cost_low_usd == pytest.approx(SOLVER_UNIT)


@pytest.mark.asyncio
async def test_the_solver_is_not_priced_when_it_is_globally_disabled(monkeypatch):
    from app.config import settings
    from app.services import regeneration_estimator as estimator

    monkeypatch.setattr(settings, "solver_enabled", False, raising=False)
    source, plans, session = _one_target_setup(selected=["boss-arena"])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    assert not any(li.kind == "solver" for li in est.line_items)


def test_the_solver_phase_set_matches_the_pipeline():
    """The estimator copies `pipeline._SOLVER_PHASES` rather than importing the
    orchestrator; this is the drift guard that keeps the copy honest."""
    from app.services import pipeline
    from app.services import regeneration_estimator as estimator

    assert set(estimator.SOLVER_PHASES) == set(pipeline._SOLVER_PHASES)


# ──────────────────────── retry / regen budgets ──────────────────────


@pytest.mark.asyncio
async def test_the_high_estimate_adds_schema_retry_and_regeneration_budgets():
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    # low  = 1 authoring + 1 judge
    # high = low + 1 judge schema retry + max_judge_regens × (authoring + judge)
    assert est.low_usd == pytest.approx(AUTHORING_UNIT + JUDGE_UNIT)
    assert est.high_usd == pytest.approx(
        (AUTHORING_UNIT + JUDGE_UNIT) + JUDGE_UNIT + (AUTHORING_UNIT + JUDGE_UNIT)
    )
    budgets = {li.budget for li in est.line_items}
    assert budgets == {"base", "schema_retry", "judge_regeneration"}
    for line in est.line_items:
        if line.budget != "base":
            assert line.calls_low == 0, "a retry budget must not inflate the LOW estimate"
            assert line.cost_low_usd == 0.0


@pytest.mark.asyncio
async def test_a_larger_judge_regeneration_budget_costs_proportionally_more(monkeypatch):
    from app.config import settings
    from app.services import regeneration_estimator as estimator

    monkeypatch.setattr(settings, "max_judge_regens", 3, raising=False)
    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    (regen,) = [
        li
        for li in est.line_items
        if li.budget == "judge_regeneration" and li.kind == "authoring"
    ]
    assert regen.calls_high == 3
    assert regen.cost_high_usd == pytest.approx(3 * AUTHORING_UNIT)


@pytest.mark.asyncio
async def test_the_solver_regeneration_budget_is_counted_separately(monkeypatch):
    from app.config import settings
    from app.services import regeneration_estimator as estimator

    monkeypatch.setattr(settings, "max_solve_regens", 2, raising=False)
    source, plans, session = _one_target_setup(selected=["boss-arena"])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    solver_regen = [li for li in est.line_items if li.budget == "solver_regeneration"]
    assert {(li.kind, li.phase, li.calls_high) for li in solver_regen} == {
        ("authoring", "boss-arena", 2),
        ("solver", "boss-arena", 2),
    }


@pytest.mark.asyncio
async def test_authoring_carries_a_schema_retry_only_while_structured_output_is_on(
    monkeypatch,
):
    """`run_phase(schema=…)` retries once on a validation failure — authoring
    only passes a schema while the structured lane is enabled."""
    from app.config import settings
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    off = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    assert not [
        li
        for li in off.line_items
        if li.budget == "schema_retry" and li.kind == "authoring"
    ]

    monkeypatch.setattr(settings, "structured_output_enabled", True, raising=False)
    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    on = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    (retry,) = [
        li
        for li in on.line_items
        if li.budget == "schema_retry" and li.kind == "authoring"
    ]
    assert retry.calls_high == 1
    assert on.high_usd == pytest.approx(off.high_usd + AUTHORING_UNIT)
    assert on.low_usd == pytest.approx(off.low_usd)


# ──────────────────── observation matching / fallback ────────────────


@pytest.mark.asyncio
async def test_judge_usage_hung_off_a_detached_phase_row_is_not_evidence():
    """`judge:flashcards` must be linked to the FLASHCARDS phase row. A row
    whose join lands on a synthetic `__judge__` phase says nothing about
    flashcards, and counting it would price the wrong work."""
    from app.services import regeneration_estimator as estimator

    detached = _obs("judge:reflection", "__judge__", JUDGE_MODEL, prompt=8_000, output=500)
    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, detached])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (judge_line,) = [
        li for li in est.line_items if li.kind == "judge" and li.budget == "base"
    ]
    assert judge_line.basis == estimator.STATIC_BASIS
    assert any("__judge__" in note for note in est.notes)


@pytest.mark.asyncio
async def test_an_unmatched_operation_falls_back_to_the_static_envelope():
    from app.services import pricing
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[])  # no history at all
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    expected_authoring = pricing.cost_usd(
        "gemini", GEN_MODEL, estimator.STATIC_TOKEN_ENVELOPE["authoring"]
    )
    expected_judge = pricing.cost_usd(
        "gemini", JUDGE_MODEL, estimator.STATIC_TOKEN_ENVELOPE["judge"]
    )
    assert expected_authoring > 0 and expected_judge > 0
    assert est.low_usd == pytest.approx(expected_authoring + expected_judge)
    assert all(li.basis == estimator.STATIC_BASIS for li in est.line_items)
    assert all(li.observations == 0 for li in est.line_items)
    assert any("static" in note for note in est.notes)


@pytest.mark.asyncio
async def test_an_observation_of_a_different_model_is_not_reused():
    """Prices differ per model; a flash-lite observation must not price a
    flash call just because the phase and provider match."""
    from app.services import regeneration_estimator as estimator

    wrong_model = _obs("phase.run", "reflection", ROLE_MODEL, prompt=10_000, output=1_000)
    source, plans, session = _one_target_setup(rows=[wrong_model])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.basis == estimator.STATIC_BASIS


@pytest.mark.asyncio
async def test_gemini_cached_prompt_tokens_are_not_double_billed():
    """gemini's prompt count INCLUDES the cached span; `pricing.cost_usd` owns
    that rule and the estimator must not re-derive it."""
    from app.services import pricing
    from app.services import regeneration_estimator as estimator

    cached_obs = _obs(
        "phase.run", "reflection", GEN_MODEL, prompt=10_000, output=1_000, cached=6_000
    )
    source, plans, session = _one_target_setup(rows=[cached_obs])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.unit_cost_usd == pytest.approx(
        pricing.cost_usd(
            "gemini",
            GEN_MODEL,
            {
                "prompt_tokens": 10_000,
                "output_tokens": 1_000,
                "cached_tokens": 6_000,
                "cache_creation_tokens": 0,
            },
        )
    )
    # 4,000 uncached × $1.50/M + 6,000 cached × $0.15/M + 1,000 out × $9.00/M
    assert authoring.unit_cost_usd == pytest.approx(0.006 + 0.0009 + 0.009)


# ─────────────────────────── contract handling ───────────────────────


@pytest.mark.asyncio
async def test_an_unresolved_launch_contract_is_refused():
    """Pricing a draft would price whatever `launch_defaults` happened to say
    at estimate time, which is not what the campaign will run."""
    from pydantic import ValidationError

    from app.services import regeneration_estimator as estimator

    # A legal DRAFT: no content model ("use the provider default") and no role
    # providers — exactly the shape `create_campaign` resolves before storing.
    draft = LaunchContract(provider="claude", model=None, transport="cli")
    source, plans, session = _one_target_setup()
    with pytest.raises(ValidationError):
        await estimator.estimate_regeneration(
            session, targets=[source], plans=plans, launch_contract=draft, now=NOW
        )


@pytest.mark.asyncio
async def test_a_stored_contract_mapping_is_accepted_through_ensure_resolved():
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session,
        targets=[source],
        plans=plans,
        launch_contract=_contract().model_dump(),  # as read back from JSONB
        now=NOW,
    )
    assert est.low_usd == pytest.approx(AUTHORING_UNIT + JUDGE_UNIT)


@pytest.mark.asyncio
async def test_the_estimator_reads_neither_launch_defaults_nor_the_session_default(
    monkeypatch,
):
    from app.repositories import launch_defaults as ld_repo
    from app.services import agent_models
    from app.services import regeneration_estimator as estimator

    def _explode(*a, **kw):
        raise AssertionError("the estimator must price the STORED contract only")

    for name in dir(ld_repo):
        attr = getattr(ld_repo, name)
        if callable(attr) and not name.startswith("_") and attr.__module__ == ld_repo.__name__:
            monkeypatch.setattr(ld_repo, name, _explode)
    monkeypatch.setattr(agent_models, "resolve_session_limit_strategy", _explode)

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    assert est.low_usd > 0


@pytest.mark.asyncio
async def test_a_target_without_a_plan_is_refused_loudly():
    from app.services import regeneration_estimator as estimator

    source = _source()
    session = _FakeSession([])
    with pytest.raises(KeyError) as excinfo:
        await estimator.estimate_regeneration(
            session, targets=[source], plans={}, launch_contract=_contract(), now=NOW
        )
    assert str(source.source_job_id) in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_empty_selection_estimates_zero_and_still_says_it_is_an_estimate():
    from app.services import regeneration_estimator as estimator

    est = await estimator.estimate_regeneration(
        _FakeSession([]), targets=[], plans={}, launch_contract=_contract(), now=NOW
    )
    assert (est.low_usd, est.high_usd, est.target_count) == (0.0, 0.0, 0)
    assert est.is_estimate is True
    assert est.line_items == ()


@pytest.mark.asyncio
async def test_the_result_is_explicitly_marked_an_estimate():
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    assert est.is_estimate is True
    assert est.high_usd >= est.low_usd


@pytest.mark.asyncio
async def test_the_solver_over_count_note_appears_only_when_a_solver_is_priced():
    """The note explains a conservative over-count. On a plan with no
    solver-bearing phase there is nothing to over-count, and printing it anyway
    would tell the operator their estimate is padded when it is not."""
    from app.services import regeneration_estimator as estimator

    def _has_note(est):
        return any("boss-arena solver toggle" in note for note in est.notes)

    source, plans, session = _one_target_setup(selected=["reflection"])
    assert not _has_note(
        await estimator.estimate_regeneration(
            session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
        )
    )

    source, plans, session = _one_target_setup(selected=["boss-arena"])
    assert _has_note(
        await estimator.estimate_regeneration(
            session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
        )
    )


def test_two_observation_groups_of_one_key_combine_by_sample_weighted_mean():
    """Defensive: nothing produces two groups for one key today, so the merge
    must not depend on which row arrived first."""
    from app.services.regeneration_estimator import summarize_observations

    rows = [
        _obs("phase.run", "reflection", GEN_MODEL, prompt=10_000, output=0, n=1),
        _obs("phase.run", "reflection", GEN_MODEL, prompt=20_000, output=0, n=3),
    ]
    forward, _ = summarize_observations(rows)
    backward, _ = summarize_observations(list(reversed(rows)))

    key = ("authoring", "reflection", "gemini", GEN_MODEL)
    assert forward[key].samples == 4
    # (10,000×1 + 20,000×3) / 4
    assert forward[key].prompt_tokens == pytest.approx(17_500)
    assert backward[key].prompt_tokens == pytest.approx(forward[key].prompt_tokens)
