"""Unit tests for ``agent.extract_source_map`` (PR-1).

Derives a structured ``SourceMap`` (concepts with stable IDs) from the
already-extracted ``lesson_context`` — a text-only, cheap-pinned structured
call. We mock ``_spawn``/``_record_usage`` (same harness as ``test_agent.py``)
so no subprocess or DB is needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.schemas.flow_v2 import SourceMap
from app.services import agent as agent_module
from app.services.agent import extract_source_map


_VALID_MAP = json.dumps(
    {
        "subject_family": "math_family",
        "chapter": "Oddiy kasrlar",
        "section": "To'g'ri kasrni natural songa bo'lish",
        "concepts": [
            {
                "id": "frac-proper",
                "label": "proper fraction",
                "statement": "A proper fraction has a numerator smaller than its denominator.",
                "kind": "concept",
            },
            {
                "id": "frac-div-whole",
                "label": "divide a fraction by a whole number",
                "statement": "To divide a/b by n, multiply the denominator by n: (a/b)/n = a/(b*n).",
                "kind": "process",
            },
        ],
    }
)


def _usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 50,
        "output_tokens": 80,
        "cached_tokens": 0,
        "total_tokens": 130,
        "raw": {},
    }


@pytest.mark.asyncio
async def test_extract_source_map_returns_parsed_sourcemap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_attachments: list[list[Any]] = []

    async def fake_spawn(*, provider: Any, model: Any, prompt: str, attachments: list[Any]):
        seen_attachments.append(attachments)
        return (0, _VALID_MAP, _usage(), "")

    async def fake_record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    sm = await extract_source_map(
        provider="gemini",
        model="gemini-2.5-flash",
        lesson_context="# Lesson\n- proper fractions\n- dividing a fraction by a whole number",
        subject_family="math_family",
        chapter="Oddiy kasrlar",
        section="To'g'ri kasrni natural songa bo'lish",
        homework_job_id=None,
        phase_output_id=None,
    )

    assert isinstance(sm, SourceMap)
    assert sm.concept_ids() == ["frac-proper", "frac-div-whole"]
    assert sm.concepts[0].statement.startswith("A proper fraction")
    # Must be a TEXT-ONLY call — no PDF re-read (the map derives from lesson_context).
    assert seen_attachments == [[]]


@pytest.mark.asyncio
async def test_extract_source_map_repairs_then_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First response is non-JSON; run_phase's built-in repair retry kicks in,
    second response validates. Two spawns, returns the parsed map."""
    outputs = [
        (0, "I cannot produce JSON.", _usage(), ""),
        (0, _VALID_MAP, _usage(), ""),
    ]

    async def fake_spawn(**_kwargs: Any):
        return outputs.pop(0)

    async def fake_record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    sm = await extract_source_map(
        provider="gemini",
        model="gemini-2.5-flash",
        lesson_context="lesson",
        subject_family="math_family",
        chapter="Ch",
        section="Sec",
        homework_job_id=None,
        phase_output_id=None,
    )
    assert isinstance(sm, SourceMap)
    assert len(outputs) == 0  # both canned responses consumed
