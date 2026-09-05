import inspect
from unittest.mock import AsyncMock

from app.services import pipeline
from app.services.pipeline import _custom_for
from tests.services.test_pipeline_solver import _agree, _make_kwargs, _mismatch, patch_io
from tests.services.test_pipeline_judge_status import _major, _ok, _unavail


def test_custom_for_returns_text():
    assert _custom_for("flashcards", {"flashcards": "RULES"}) == "RULES"


def test_custom_for_none_and_blank():
    assert _custom_for("flashcards", None) is None
    assert _custom_for("flashcards", {}) is None
    assert _custom_for("flashcards", {"flashcards": "   "}) is None
    assert _custom_for("flashcards", {"memory-check": "X"}) is None


async def test_custom_contract_survives_unavailable_retry_and_both_repairs(monkeypatch, patch_io):
    patch_io.failover_outputs = [
        ("# initial", 10, 5, "claude"), ("# judge repair", 20, 8, "claude"),
        ("# solver repair", 30, 9, "gemini"),
    ]
    patch_io.solve_outputs = [_mismatch(), _agree()]
    judge = AsyncMock(side_effect=[_unavail(), _major(), _ok(), _ok()])
    monkeypatch.setattr(pipeline, "_judge_with_timeout", judge)
    kw = _make_kwargs()
    kw["custom_prompts"] = {"memory-check": "Custom contract"}
    result = await pipeline._execute_phase(**kw)
    assert result[0] == "# solver repair"
    assert result[3].startswith("custom:sha256:")
    assert len(judge.call_args_list) == 4
    assert all(c.kwargs["contract_override"] == "Custom contract" for c in judge.call_args_list)
    assert all(c["contract_override"] == "Custom contract" for c in patch_io.solve_calls)


def test_run_builds_sequence_from_selected_phases():
    src = inspect.getsource(pipeline.run)
    assert "custom_prompts" in src
    assert "selected_phases" in src
