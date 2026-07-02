from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from app.api.v1.batch import _rollup_payload


def _fake_batch():
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), subject="math", grade="8",
        output_language="uz", provider="gemini", model="gemini-2.5-pro",
        transport="api", extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit",
        extract_provider=None, extract_model=None, judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        created_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        paused_at=None, paused_reason=None, session_limit_strategy="inherit",
    )


def test_rollup_payload_defaults_archive_counts_to_zero():
    p = _rollup_payload(_fake_batch(), {"done": 3})
    assert p["archived"] == 0
    assert p["unarchived"] == 0


def test_rollup_payload_carries_archive_counts():
    p = _rollup_payload(_fake_batch(), {"done": 3}, archived=2, unarchived=1)
    assert p["archived"] == 2
    assert p["unarchived"] == 1
