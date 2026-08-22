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
    assert est.copied_phase_count == 10        # content phases only; extract
    assert est.copied_extract_count == 1       # is reported by its own field
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


# ───────────── the copied/regenerated CONTENT-phase partition ────────


def _content_phases():
    from app.services import flows

    return list(flows.flow_for(SUBJECT))


@pytest.mark.asyncio
async def test_copied_and_regenerated_counts_partition_the_content_phases_only():
    """`extract` is reported by the two `*_extract_count` fields and by nothing
    else: the phase counts are a partition of the 11 CONTENT phases, so
    `regenerated + copied` is the same number whatever the extraction does."""
    from app.services import regeneration_estimator as estimator

    content = _content_phases()
    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    assert est.regenerated_phase_count == 1
    assert est.copied_phase_count == len(content) - 1 == 10
    assert est.regenerated_phase_count + est.copied_phase_count == len(content)
    # extract travels ONLY on its own two fields
    assert (est.copied_extract_count, est.regenerated_extract_count) == (1, 0)


@pytest.mark.asyncio
async def test_a_refreshed_extraction_does_not_shrink_the_phase_partition():
    """With `refresh_extraction=True` the old code reported 11 + 0 = 11 while
    the copied case reported 1 + 11 = 12: the same snapshot, two different
    totals. Both must total the content-phase count."""
    from app.services import regeneration_estimator as estimator

    content = _content_phases()
    source = _source()
    plan = build_phase_plan(
        subject=SUBJECT, selected_phases=["reflection"], refresh_extraction=True
    )
    est = await estimator.estimate_regeneration(
        _FakeSession([]),
        targets=[source],
        plans={source.source_job_id: plan},
        launch_contract=_contract(),
        now=NOW,
    )

    assert est.regenerated_phase_count == len(content) == 11
    assert est.copied_phase_count == 0
    assert est.regenerated_phase_count + est.copied_phase_count == len(content)
    assert (est.copied_extract_count, est.regenerated_extract_count) == (0, 1)


@pytest.mark.asyncio
async def test_every_content_phase_regenerated_with_a_copied_extract_still_partitions():
    """The mixed case the other two miss: all 11 content phases regenerated
    while the extraction is COPIED. `copied_phase_count` must be 0 — the copied
    extract belongs to `copied_extract_count`."""
    from app.services import regeneration_estimator as estimator

    content = _content_phases()
    source = _source()
    plan = build_phase_plan(subject=SUBJECT, selected_phases=content)
    est = await estimator.estimate_regeneration(
        _FakeSession([]),
        targets=[source],
        plans={source.source_job_id: plan},
        launch_contract=_contract(),
        now=NOW,
    )

    assert est.regenerated_phase_count == len(content)
    assert est.copied_phase_count == 0
    assert (est.copied_extract_count, est.regenerated_extract_count) == (1, 0)
    assert not any(li.kind == "extract" for li in est.line_items)


@pytest.mark.asyncio
async def test_the_partition_holds_across_a_mix_of_targets():
    """One campaign, two lessons, different plans: the counts are per-campaign
    sums of the same per-target partition."""
    from app.services import regeneration_estimator as estimator

    content = _content_phases()
    a, b = _source(), _source()
    refreshed = build_phase_plan(
        subject=SUBJECT, selected_phases=["reflection"], refresh_extraction=True
    )
    selective = build_phase_plan(subject=SUBJECT, selected_phases=["reflection"])
    est = await estimator.estimate_regeneration(
        _FakeSession([]),
        targets=[a, b],
        plans={a.source_job_id: refreshed, b.source_job_id: selective},
        launch_contract=_contract(),
        now=NOW,
    )

    assert est.target_count == 2
    assert est.regenerated_phase_count + est.copied_phase_count == 2 * len(content)
    assert est.regenerated_phase_count == len(content) + 1
    assert est.copied_phase_count == len(content) - 1
    assert (est.copied_extract_count, est.regenerated_extract_count) == (1, 1)


# ───────────────────── unpriced provider/model lines ─────────────────


@pytest.mark.asyncio
async def test_a_model_absent_from_the_price_table_is_never_shown_as_a_free_line(
    monkeypatch,
):
    """`pricing.cost_usd` bills $0 for a pair it has no rate for. Priced through
    the conservative envelope that is a $0.00 line whose basis reads
    "conservative static token envelope" — the estimate reads CHEAP in exactly
    the case where the cost is UNKNOWN. The number stays 0.0 for compatibility;
    the line and the estimate must say so."""
    from app.services import pricing
    from app.services import regeneration_estimator as estimator

    monkeypatch.delitem(pricing.PRICE_MAP, ("gemini", GEN_MODEL))

    source, plans, session = _one_target_setup(rows=[])  # static envelope
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    # the envelope really is nonzero — this is a missing PRICE, not no work
    assert estimator.STATIC_TOKEN_ENVELOPE["authoring"]["prompt_tokens"] > 0
    assert authoring.unit_cost_usd == 0.0 and authoring.cost_low_usd == 0.0
    assert authoring.is_unpriced is True
    assert authoring.basis.startswith(estimator.UNPRICED_BASIS)
    assert authoring.basis != estimator.STATIC_BASIS
    assert estimator.STATIC_BASIS in authoring.basis  # volume provenance kept

    # the judge model is still priced, and is untouched
    (judge,) = [li for li in est.line_items if li.kind == "judge" and li.budget == "base"]
    assert judge.is_unpriced is False
    assert judge.basis == estimator.STATIC_BASIS
    assert judge.unit_cost_usd > 0

    # aggregate marker + a loud, human-readable note naming the pair
    assert est.has_unpriced_lines is True
    unpriced_notes = [n for n in est.notes if estimator.UNPRICED_BASIS.split(":")[0] in n]
    assert unpriced_notes, est.notes
    assert any(GEN_MODEL in n and "gemini" in n for n in unpriced_notes)


@pytest.mark.asyncio
async def test_an_observed_unpriced_model_is_marked_too(monkeypatch):
    """Real observed volume priced against a missing rate is the same lie."""
    from app.services import pricing
    from app.services import regeneration_estimator as estimator

    monkeypatch.delitem(pricing.PRICE_MAP, ("gemini", GEN_MODEL))
    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.is_unpriced is True
    assert authoring.observations == 4
    assert "observed" in authoring.basis
    assert authoring.basis.startswith(estimator.UNPRICED_BASIS)
    assert est.has_unpriced_lines is True
    # the priced judge line still carries the whole (nonzero) total
    assert est.low_usd == pytest.approx(JUDGE_UNIT)


@pytest.mark.asyncio
async def test_known_priced_models_are_untouched_by_the_unpriced_marker():
    """Regression fence: the normal, fully priced estimate keeps its exact
    numbers, its plain basis strings and a clean aggregate marker."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    assert est.low_usd == pytest.approx(AUTHORING_UNIT + JUDGE_UNIT)
    assert est.has_unpriced_lines is False
    assert all(li.is_unpriced is False for li in est.line_items)
    assert all(not li.basis.startswith(estimator.UNPRICED_BASIS) for li in est.line_items)
    assert not any("UNPRICED" in note for note in est.notes)


@pytest.mark.asyncio
async def test_copied_work_is_zero_without_being_called_unpriced():
    """Zero because nothing runs is not the same as zero because the rate is
    unknown: copied phases and a copied extract emit no line at all, and an
    empty campaign is a clean $0."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_AUTHORING_OBS, _JUDGE_OBS])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )
    assert {li.phase for li in est.line_items} == {"reflection"}
    assert est.low_usd == pytest.approx(AUTHORING_UNIT + JUDGE_UNIT)
    assert est.has_unpriced_lines is False

    empty = await estimator.estimate_regeneration(
        _FakeSession([]), targets=[], plans={}, launch_contract=_contract(), now=NOW
    )
    assert (empty.low_usd, empty.high_usd) == (0.0, 0.0)
    assert empty.has_unpriced_lines is False


# ───────────── zero-volume observations are not evidence ─────────────


def _zero_volume_obs(**overrides):
    """A successful, well-formed observation that BILLS NOTHING.

    A real shape, not a contrivance: a provider whose envelope reports no token
    breakdown writes successful rows with every billable field 0 (the documented
    kimi gap is exactly this), and a run of them averages to exactly this row.
    """
    kwargs = dict(prompt=0, output=0, cached=0, cache_creation=0, n=4)
    kwargs.update(overrides)
    return _obs("phase.run", "reflection", GEN_MODEL, **kwargs)


#   static authoring envelope: 24,000 prompt × $1.50/M + 6,000 output × $9.00/M
STATIC_AUTHORING_UNIT = 0.036 + 0.054


@pytest.mark.asyncio
async def test_an_observation_that_bills_nothing_is_not_volume_evidence():
    """A successful call that bills zero tokens is MISSING volume, not a free
    call. Treated as authoritative it prices a real future model call as a
    complete $0.00 line; it must fall back to the static envelope exactly like a
    phase with no history at all."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_zero_volume_obs()])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.basis == estimator.STATIC_BASIS
    assert authoring.observations == 0
    assert authoring.unit_cost_usd == pytest.approx(STATIC_AUTHORING_UNIT)
    assert authoring.cost_low_usd == pytest.approx(STATIC_AUTHORING_UNIT)
    # a priced pair: this fallback is about VOLUME and says nothing about rates
    assert authoring.is_unpriced is False
    assert est.has_unpriced_lines is False
    # identical treatment to the judge line, which has no observation at all
    (judge,) = [
        li for li in est.line_items if li.kind == "judge" and li.budget == "base"
    ]
    assert (judge.basis, judge.observations) == (
        authoring.basis,
        authoring.observations,
    )


@pytest.mark.asyncio
async def test_the_ignored_zero_volume_history_is_explained_in_the_notes():
    """The operator must be able to see WHY a line that has history is priced
    from the envelope anyway."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_zero_volume_obs(n=7)])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (note,) = [n for n in est.notes if n.startswith(estimator.ZERO_VOLUME_HISTORY)]
    assert "7" in note                        # how much history was ignored
    assert "reflection" in note               # for which phase
    assert f"gemini/{GEN_MODEL}" in note      # on which pair
    assert estimator.STATIC_BASIS in note     # and what was priced instead
    # NOT reported as absent history: history exists, it just bills nothing
    assert not any("no successful api authoring call" in n for n in est.notes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value, unit",
    [
        # hand-derived against gemini-3.5-flash: in $1.50, out $9.00, cached $0.15
        ("prompt", 10_000, 0.015),
        ("output", 1_000, 0.009),
        ("cached", 6_000, 0.0009),  # prompt 0 ⇒ uncached input clamps to 0
    ],
)
async def test_one_nonzero_billable_field_keeps_the_observed_basis(field, value, unit):
    """The fallback triggers on ZERO billable volume, never on a small or
    lopsided one: any single field ``pricing.cost_usd`` bills is real evidence
    and keeps the observed basis."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(
        rows=[_zero_volume_obs(**{field: value})]
    )
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.observations == 4
    assert authoring.basis == "observed mean of 4 api call(s) in the last 30 days"
    assert authoring.unit_cost_usd == pytest.approx(unit)
    assert not any(n.startswith(estimator.ZERO_VOLUME_HISTORY) for n in est.notes)


@pytest.mark.asyncio
async def test_cache_creation_volume_is_evidence_even_with_no_write_rate():
    """Volume and RATE stay separate questions, and the fourth billable field is
    really in the predicate. gemini entries deliberately carry no ``cache_write``
    rate, so a cache-creation-only observation prices at $0.00 — but it IS real
    volume, so it keeps its observed basis and is surfaced by the pre-existing
    missing-rate marker rather than silently re-based on the envelope."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(
        rows=[_zero_volume_obs(cache_creation=4_000)]
    )
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.observations == 4
    assert "observed mean of 4 api call(s)" in authoring.basis
    assert estimator.STATIC_BASIS not in authoring.basis
    assert authoring.unit_cost_usd == 0.0
    assert authoring.is_unpriced is True
    assert not any(n.startswith(estimator.ZERO_VOLUME_HISTORY) for n in est.notes)


@pytest.mark.asyncio
async def test_an_unpriced_pair_stays_visibly_unpriced_after_the_volume_fallback(
    monkeypatch,
):
    """The two conditions compose. Missing volume is repaired from the static
    envelope; the missing RATE must survive that repair loudly. What this fences
    is a $0.00 line that reads neither UNPRICED nor static — the exact shape an
    operator would approve as a free campaign."""
    from app.services import pricing
    from app.services import regeneration_estimator as estimator

    monkeypatch.delitem(pricing.PRICE_MAP, ("gemini", GEN_MODEL))
    source, plans, session = _one_target_setup(rows=[_zero_volume_obs()])
    est = await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    (authoring,) = [
        li for li in est.line_items if li.kind == "authoring" and li.budget == "base"
    ]
    assert authoring.observations == 0
    assert authoring.unit_cost_usd == 0.0
    assert authoring.is_unpriced is True
    assert authoring.basis.startswith(estimator.UNPRICED_BASIS)
    assert estimator.STATIC_BASIS in authoring.basis  # volume provenance kept
    assert est.has_unpriced_lines is True
    assert any(n.startswith(estimator.ZERO_VOLUME_HISTORY) for n in est.notes)
    # the judge pair is still priced, and the static volume really is nonzero
    (judge,) = [
        li for li in est.line_items if li.kind == "judge" and li.budget == "base"
    ]
    assert judge.is_unpriced is False and judge.unit_cost_usd > 0


@pytest.mark.asyncio
async def test_a_non_billable_total_cannot_turn_zero_priced_volume_into_evidence():
    """``agent_usages`` carries a ``total_tokens`` column, and a row may report a
    nonzero total while every field ``pricing.cost_usd`` actually bills is 0 (a
    provider summary figure, thoughts-only accounting, a metadata total).
    Deciding on a total would re-admit exactly the line this change removes: a
    real call priced as observed at $0.00. Three fences — the SELECT never
    carries a total, the priced mapping has no total key, and the predicate
    itself ignores one."""
    from app.services import regeneration_estimator as estimator

    source, plans, session = _one_target_setup(rows=[_zero_volume_obs()])
    await estimator.estimate_regeneration(
        session, targets=[source], plans=plans, launch_contract=_contract(), now=NOW
    )

    sql = _sql(session.statements[0])
    assert "total_tokens" not in sql
    for key in estimator._TOKEN_KEYS:
        assert f"avg(agent_usages.{key})" in sql

    priced = estimator._Observation(
        prompt_tokens=0.0,
        output_tokens=0.0,
        cached_tokens=0.0,
        cache_creation_tokens=0.0,
        samples=1,
    ).usage()
    assert set(priced) == set(estimator._TOKEN_KEYS)

    metadata_only = {**priced, "total_tokens": 99_000}
    assert estimator.billable_token_volume(metadata_only) == 0
    assert estimator.price_unit("gemini", GEN_MODEL, metadata_only) == (0.0, False)
