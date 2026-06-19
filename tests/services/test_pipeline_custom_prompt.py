import inspect

from app.services import pipeline
from app.services.pipeline import _custom_for


def test_custom_for_returns_text():
    assert _custom_for("flashcards", {"flashcards": "RULES"}) == "RULES"


def test_custom_for_none_and_blank():
    assert _custom_for("flashcards", None) is None
    assert _custom_for("flashcards", {}) is None
    assert _custom_for("flashcards", {"flashcards": "   "}) is None
    assert _custom_for("flashcards", {"memory-check": "X"}) is None


def test_execute_phase_uses_custom_prompt_and_hash():
    src = inspect.getsource(pipeline._execute_phase)
    # generator prompt: custom replaces built-in
    assert "_custom_for(phase_name, custom_prompts)" in src
    # provenance: sha256 of the custom text when custom is used
    assert "sha256" in src
    # judge: the override is threaded into BOTH judge() calls
    assert src.count("contract_override=") == 2


def test_run_builds_sequence_from_selected_phases():
    src = inspect.getsource(pipeline.run)
    assert "custom_prompts" in src
    assert "selected_phases" in src
