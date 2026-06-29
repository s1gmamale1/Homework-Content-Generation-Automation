"""Lock the PR#37↔C4 graft in `batch._rollup_payload`: the PR rewrote the function
for `not_started`-aware completeness math but dropped C4's `paused_at`/`paused_reason`.
After the rebase graft, BOTH must be present. Pure-function test, no DB."""
import types
from datetime import datetime, timezone

from app.api.v1 import batch as batch_api


def _fake_batch(paused=False):
    return types.SimpleNamespace(
        id="b1", book_id="bk1", subject="math", grade=9,
        output_language="uz",
        provider="claude", model="claude-sonnet-4-6", transport="api",
        extract_transport="inherit", judge_transport="inherit",
        extract_provider=None, extract_model=None, judge_provider=None, judge_model=None,
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        paused_at=datetime(2026, 6, 19, tzinfo=timezone.utc) if paused else None,
        paused_reason="batch cap reached" if paused else None,
        session_limit_strategy="inherit",
    )


def test_rollup_keeps_c4_pause_fields_and_not_started_math():
    tally = {"done": 3, "running": 1, "not_started": 2}
    out = batch_api._rollup_payload(_fake_batch(paused=True), tally, "math_g9.pdf")
    # C4 pause fields preserved through the PR rewrite
    assert out["paused_reason"] == "batch cap reached"
    assert out["paused_at"] is not None
    # PR not_started math: covered excludes not_started; complete False while not_started>0
    assert out["lessons_covered"] == 4          # 3 done + 1 running, NOT the 2 not_started
    assert out["complete"] is False


def test_rollup_complete_true_only_when_no_not_started_and_no_inflight():
    out = batch_api._rollup_payload(_fake_batch(), {"done": 5}, "math_g9.pdf")
    assert out["complete"] is True
    assert out["paused_at"] is None
    assert out["paused_reason"] is None
