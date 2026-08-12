"""Lock `batch._rollup_payload`'s post-BE-03 semantics (task 2): the tally is
launched-lessons-only (no `not_started` key ever, per task 1's
`rollup_for_batch`); `complete` now requires EVERY launched lesson to be
`done` (a failed/cancelled launched lesson blocks completeness — deliberate
semantic change, gatekeeper-approved); `toc_total` is a new display-only field
carrying the whole-book TOC row count (never used as the completeness
denominator). C4's `paused_at`/`paused_reason` fields must survive alongside
all of this. Pure-function test, no DB."""
import types
from datetime import datetime, timezone

from app.api.v1 import batch as batch_api


def _fake_batch(paused=False):
    return types.SimpleNamespace(
        id="b1", book_id="bk1", subject="math", grade=9,
        kind="homework",
        output_language="uz",
        provider="claude", model="claude-sonnet-4-6", transport="api",
        extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit",
        extract_provider=None, extract_model=None, judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        paused_at=datetime(2026, 6, 19, tzinfo=timezone.utc) if paused else None,
        paused_reason="batch cap reached" if paused else None,
        session_limit_strategy="inherit",
    )


def test_rollup_payload_keeps_c4_pause_fields():
    out = batch_api._rollup_payload(_fake_batch(paused=True), {"done": 1}, "math_g9.pdf",
                                    toc_total=1)
    assert out["paused_reason"] == "batch cap reached"
    assert out["paused_at"] is not None


def test_rollup_never_carries_not_started_and_exposes_toc_total():
    # rollup_for_batch (task 1) never emits "not_started" any more; toc_total
    # is the new whole-book display field, threaded in separately.
    out = batch_api._rollup_payload(_fake_batch(), {"done": 2, "failed": 1}, "math_g9.pdf",
                                    toc_total=5)
    assert "not_started" not in out["rollup"]
    assert out["toc_total"] == 5


def test_lessons_covered_is_sum_of_the_launched_tally():
    out = batch_api._rollup_payload(_fake_batch(), {"done": 3, "running": 1}, "math_g9.pdf",
                                    toc_total=10)
    assert out["lessons_covered"] == 4


def test_complete_true_when_all_launched_lessons_done_even_if_book_has_more_rows():
    # Headline BE-03 fix: 2 launched lessons, both done; the book has a 3rd,
    # un-launched row (toc_total=3) that must NOT block completeness.
    out = batch_api._rollup_payload(_fake_batch(), {"done": 2}, "math_g9.pdf", toc_total=3)
    assert out["complete"] is True
    assert out["toc_total"] == 3


def test_complete_false_when_a_launched_lesson_failed():
    # Deliberate semantic change: a failed launched lesson now blocks complete,
    # even though there is no "not_started" concept left to check.
    out = batch_api._rollup_payload(_fake_batch(), {"done": 1, "failed": 1}, "math_g9.pdf",
                                    toc_total=2)
    assert out["complete"] is False
