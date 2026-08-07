# tests/services/test_extract_coverage.py
"""Extract-completeness check (warn-only) — agent boundary + config defaults.

The check is the inverse of CQ-D's fidelity guard: fidelity asks whether the
extract INVENTED something, this asks whether it DROPPED something. It must
never raise — a broken check degrades to 'no findings', never to a failed job.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import agent as agent_mod
from app.services.agent import (
    ExtractCoverageMiss,
    ExtractCoverageVerdict,
    check_extract_coverage,
)


def test_config_defaults_are_warn_only_and_inherit_the_extract_model():
    # Kill switch present; model override defaults to "inherit the extract role".
    assert isinstance(settings.extract_coverage_check_enabled, bool)
    assert settings.extract_coverage_model is None
    assert settings.extract_coverage_max_items >= 1
    # The check runs outside the failover timeout guard — it must carry a bound
    # of its own, and one far tighter than per_attempt_timeout_seconds (600s),
    # because extract is the sequential head of the whole job.
    assert 0 < settings.extract_coverage_timeout_seconds < settings.per_attempt_timeout_seconds


def test_shipped_default_is_independent_of_the_test_environment():
    """The suite forces the check OFF via env (tests/conftest.py) so no unit test
    can reach a real spawn — so assert the SHIPPED default on the class, not on
    the env-resolved instance, or this test would silently stop meaning anything."""
    from app.config import Settings

    assert Settings.model_fields["extract_coverage_check_enabled"].default is True


@pytest.mark.asyncio
async def test_check_returns_missing_items_from_model():
    fake = agent_mod.PhaseResult(
        text="{}",
        parsed=ExtractCoverageVerdict(missing=[
            ExtractCoverageMiss(label="Izotoplar massa ulushi orqali o'rtacha atom massasi", central=True),
            ExtractCoverageMiss(label="Ion zaryadi miqdori qoidasi", central=False),
        ]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)) as rp:
        out = await check_extract_coverage(
            summary="periodic trends narrative only",
            source_text="… 3-misol … 4-misol …",
            section_title="Kimyoviy elementlar", section_number="13",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert [m.label for m in out] == [
        "Izotoplar massa ulushi orqali o'rtacha atom massasi",
        "Ion zaryadi miqdori qoidasi",
    ]
    assert [m.central for m in out] == [True, False]
    assert rp.call_args.kwargs["schema"] is ExtractCoverageVerdict
    assert rp.call_args.kwargs["operation"] == "lesson.extract.coverage"
    # The lesson identity MUST reach the prompt — the ±1 page window carries
    # neighbouring lessons, and without the title the checker reports their
    # items as omissions.
    prompt = rp.call_args.kwargs["phase_prompt"]
    assert "Kimyoviy elementlar" in prompt and "13" in prompt
    assert "periodic trends narrative only" in prompt
    assert "3-misol" in prompt


@pytest.mark.asyncio
async def test_check_clean_extract_returns_empty():
    fake = agent_mod.PhaseResult(text="{}", parsed=ExtractCoverageVerdict(missing=[]))
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        out = await check_extract_coverage(
            summary="complete", source_text="source", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []


@pytest.mark.asyncio
async def test_check_is_fail_open_on_model_error():
    with patch.object(agent_mod, "run_phase", AsyncMock(side_effect=RuntimeError("429 boom"))):
        out = await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []          # advisory: an error degrades to 'no findings'


@pytest.mark.asyncio
async def test_check_drops_blank_labels_and_unparsed_verdicts():
    fake = agent_mod.PhaseResult(
        text="not json",
        parsed=ExtractCoverageVerdict(missing=[ExtractCoverageMiss(label="   ", central=True)]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        assert await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []

    unparsed = agent_mod.PhaseResult(text="plain text, no schema", parsed=None)
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=unparsed)):
        assert await check_extract_coverage(
            summary="s", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []


@pytest.mark.asyncio
async def test_empty_summary_or_source_makes_no_paid_call():
    with patch.object(agent_mod, "run_phase", AsyncMock()) as rp:
        assert await check_extract_coverage(
            summary="   ", source_text="src", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []
        assert await check_extract_coverage(
            summary="s", source_text="", section_title="T", section_number="1",
            provider="gemini", model="gemini-3.5-flash-lite", transport="api",
            homework_job_id=None, phase_output_id=None,
        ) == []
    rp.assert_not_awaited()


# --- warning formatter (pure) ------------------------------------------------

from app.services import pipeline as pipeline_mod


def _miss(label, central=False):
    return ExtractCoverageMiss(label=label, central=central)


def test_no_misses_formats_to_no_warning():
    assert pipeline_mod._extract_coverage_warnings([]) == []


def test_one_aggregated_warning_lists_central_items_first():
    out = pipeline_mod._extract_coverage_warnings([
        _miss("secondary detail"),
        _miss("isotope mass-fraction problem", central=True),
        _miss("valence → unknown element problem", central=True),
    ])
    assert len(out) == 1
    msg = out[0]
    assert msg.startswith("extract_coverage:")
    assert "3 item(s)" in msg and "2 central" in msg
    # central first, so a truncated read still shows what matters most
    assert msg.index("isotope mass-fraction problem") < msg.index("secondary detail")


def test_warning_caps_the_item_list(monkeypatch):
    monkeypatch.setattr(settings, "extract_coverage_max_items", 2)
    out = pipeline_mod._extract_coverage_warnings([_miss(f"item {i}") for i in range(5)])
    assert len(out) == 1
    assert "(+3 more)" in out[0]
    assert "item 4" not in out[0]


def test_blank_labels_are_dropped_and_long_labels_truncated():
    assert pipeline_mod._extract_coverage_warnings([_miss("  "), _miss("")]) == []
    out = pipeline_mod._extract_coverage_warnings([_miss("L" * 400)])
    assert len(out[0]) < 300
