"""Acceptance: the deterministic dims of the CQ-E rubric reproduce the real
human audit's verdicts on the 5 defective edu_copy packets (tests/golden/manifest.json).

DB-gated (reads `edu_copy` read-only via `phase_outputs`) — free, deterministic,
no LLM calls (`llm=False`). Run:

    RUN_GOLDEN_AUDIT=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_copy \\
        uv run python -m pytest tests/golden/test_reproduces_audit.py -q
"""
import os

import pytest

from app.services import golden_eval as ge

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_GOLDEN_AUDIT") != "1",
    reason="reads edu_copy; set RUN_GOLDEN_AUDIT=1 + DATABASE_URL=edu_copy",
)

_DET = ("language", "reflection")  # deterministic dims validated for free


@pytest.mark.asyncio
async def test_deterministic_dims_reproduce_audit_flags():
    for entry in ge.load_golden_set():
        phases = await ge._load_phases_from_db(entry.job_id)
        score = await ge.score_packet(
            entry, phases, "", None, provider="gemini",
            model="gemini-2.5-pro", transport="api", llm=False,
        )
        for dim in _DET:
            assert score.scores[dim].verdict == entry.audit_verdict[dim], (
                f"{entry.job_id[:8]} {dim}: got {score.scores[dim].verdict}, "
                f"audit says {entry.audit_verdict[dim]}"
            )
