from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.batch import _rollup_payload


def _fake_batch(subject):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), subject=subject, grade="8",
        output_language="uz",
        provider="claude", model=None, transport="cli",
        extract_transport="inherit", judge_transport="inherit",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        paused_at=None, paused_reason=None,
        session_limit_strategy="inherit",
    )


def test_rollup_payload_history_variant_jahon():
    p = _rollup_payload(_fake_batch("history"), {"done": 3}, "8-sinf Jahon tarixi.pdf")
    assert p["subject_variant"] == "jahon"


def test_rollup_payload_variant_none_without_filename():
    p = _rollup_payload(_fake_batch("history"), {"done": 1})
    assert p["subject_variant"] is None


def test_rollup_payload_non_history_variant_none():
    p = _rollup_payload(_fake_batch("math-algebra"), {"done": 1}, "8-sinf Algebra.pdf")
    assert p["subject_variant"] is None
