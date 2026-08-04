"""`phase_outputs.prompt_hash` must name the prompt that ACTUALLY authored the row.

A structured phase is authored by the JSON contract
(`prompts/_general/structured/<phase>.md`), not by the markdown contract the judge
and lint read. Recording the markdown prompt's hash attributed the output to a
document the model never saw — provenance that is not merely imprecise but wrong.

Everything here is patched at the DB/agent seams: no DB, no model call.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.content_json import SCHEMAS
from app.services import phase_render, pipeline, prompts

SUBJECT = "tarix"
STRUCTURED_PHASE = "practice-rlc"
MARKDOWN_PHASE = "flashcards"


def _rlc_json() -> dict:
    def opts():
        return [{"id": "o0", "label": "Yes", "is_correct": True},
                {"id": "o1", "label": "No", "is_correct": False}]
    return {
        "id": "c1", "title": "Fire audit", "intro": "You inspect a hall.",
        "expert_role": "fire_inspector",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "Choose",
             "prompt": "Evacuate?", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "Ask",
             "prompt": "What data?", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "Decide",
             "prompt": "Final?", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "Concept", "prompt": "Which?",
             "concept_chips": [{"id": "k1", "label": "Load", "is_correct": True},
                               {"id": "k2", "label": "Colour", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "Explain",
             "prompt": "Why?", "min_chars": 80},
        ],
    }


@pytest.fixture()
def recorded_hash(monkeypatch):
    """Run `_execute_phase` with every seam faked; capture the recorded prompt_hash."""
    seen: dict = {}

    fake_po = MagicMock()
    fake_po.id = uuid.uuid4()

    async def fake_create_or_reset(session, **kw):
        seen["prompt_hash"] = kw["prompt_hash"]
        return fake_po

    async def fake_set_status(session, *a, **kw):
        return True

    monkeypatch.setattr(pipeline.phase_repo, "create_or_reset", fake_create_or_reset)
    monkeypatch.setattr(pipeline.phase_repo, "set_status", fake_set_status)
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", fake_set_status)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(pipeline, "SessionLocal", MagicMock(return_value=fake_session))

    async def fake_failover(*, requested_provider, model, run_fn, transport, **kw):
        # Structured phases go through the JSON path; markdown ones return text.
        return ("# generated output", 10, 5, "claude")

    monkeypatch.setattr(pipeline, "_run_with_failover", fake_failover)

    async def fake_structured(*, phase_name, **kw):
        cfg = SCHEMAS[phase_name].model_validate(_rlc_json())
        return (
            pipeline.PhaseArtifact(
                output_md=phase_render.render_md(phase_name, cfg),
                content_json=cfg.model_dump(mode="json"),
                authoring_mode="structured",
                content_schema_version=cfg.SCHEMA_VERSION,
                renderer_version=phase_render.RENDERER_VERSION,
            ),
            10, 5, "claude",
        )

    monkeypatch.setattr(pipeline, "_run_structured_attempt", fake_structured)

    from app.services.phase_judge import JudgeOutcome

    async def fake_judge(**kw):
        return JudgeOutcome(available=True, passed=True, warnings=[],
                            feedback="", has_major=False)

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)
    monkeypatch.setattr(pipeline.model_tiers, "resolve_judge", lambda *a, **kw: ("claude", None))

    # practice-rlc is in _SOLVER_PHASES; without this seam the answer-key solver
    # runs for real (retries + backoff) and the test takes ~27s instead of ~0.1s.
    monkeypatch.setattr(pipeline.solver, "solve",
                        AsyncMock(return_value=pipeline.solver.SolveOutcome(
                            available=True, agrees=True)))

    async def _run(phase_name: str, **over):
        kw = dict(
            job_id=uuid.uuid4(), phase_name=phase_name, phase_order=1,
            subject=SUBJECT, provider="claude", model=None,
            pdf_path=Path("/fake/book.pdf"), attach_file=False,
            section={"title": "T", "number": "1.1", "page_start": 1,
                     "page_end": 5, "id": uuid.uuid4()},
            lesson_context="ctx", prior_outputs={}, difficulty=None,
            source_map_digest="abc", transport="cli", extract_transport="cli",
            judge_transport="cli", extract_provider="gemini", extract_model=None,
            output_language="uz",
        )
        kw.update(over)
        await pipeline._execute_phase(**kw)
        return seen["prompt_hash"]

    return _run


@pytest.mark.asyncio
async def test_structured_phase_records_the_structured_prompt_hash(recorded_hash):
    recorded = await recorded_hash(STRUCTURED_PHASE)

    expected = prompts.get_structured_prompt_hash(SUBJECT, STRUCTURED_PHASE,
                                                  output_language="uz")
    assert expected is not None, "practice-rlc has no structured prompt — fixture is stale"
    assert recorded == expected
    assert recorded.startswith("structured:sha256:")
    # The bug: the MARKDOWN contract's hash was recorded instead. These are
    # different documents, so the two hashes must not coincide.
    assert recorded != prompts.get_prompt_hash(SUBJECT, STRUCTURED_PHASE,
                                               output_language="uz")


@pytest.mark.asyncio
async def test_markdown_phase_still_records_the_markdown_prompt_hash(recorded_hash):
    recorded = await recorded_hash(MARKDOWN_PHASE)

    assert recorded == prompts.get_prompt_hash(SUBJECT, MARKDOWN_PHASE,
                                               output_language="uz")
    assert "structured" not in recorded


@pytest.mark.asyncio
async def test_custom_prompt_overrides_the_structured_hash(recorded_hash):
    """A custom prompt is a MARKDOWN contract and disables the structured lane,
    so `custom:sha256:` must still win for a phase that HAS a schema."""
    recorded = await recorded_hash(
        STRUCTURED_PHASE, custom_prompts={STRUCTURED_PHASE: "my own prompt"},
    )
    assert recorded.startswith("custom:sha256:")


@pytest.mark.asyncio
async def test_extract_hash_is_unchanged(recorded_hash, monkeypatch):
    """`builtin:extract:v3` is the cross-job extract REUSE KEY — it must not move.

    Served from the cross-job cache so the extract branch short-circuits before
    any agent call.
    """
    cached = MagicMock()
    cached.output_md = "cached summary"
    cached.job_id = uuid.uuid4()
    cached.id = uuid.uuid4()
    monkeypatch.setattr(pipeline.phase_repo, "find_latest_extract",
                        AsyncMock(return_value=cached))
    monkeypatch.setattr(pipeline.agent, "record_cached_lesson_extract",
                        AsyncMock(return_value=None))

    recorded = await recorded_hash("extract")
    assert recorded == "builtin:extract:v3"


def test_structured_prompt_hash_is_language_sensitive():
    uz = prompts.get_structured_prompt_hash(SUBJECT, STRUCTURED_PHASE, output_language="uz")
    ru = prompts.get_structured_prompt_hash(SUBJECT, STRUCTURED_PHASE, output_language="ru")
    assert uz and ru and uz != ru


def test_structured_prompt_hash_is_none_for_a_markdown_phase():
    assert prompts.get_structured_prompt_hash(SUBJECT, MARKDOWN_PHASE) is None
