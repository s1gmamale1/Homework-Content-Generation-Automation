"""RED/GREEN tests for the teacher-deck authoring prompt + fidelity contract
(SDD task 5, 2026-08-11-teacher-material-deck).

Pure unit tests — no DB, no RUN_DB_INTEGRATION. Verifies:
- `get_structured_prompt("history", "teacher-deck")` is the JSON-authoring
  prompt: `{{SUBJECT}}`/`{{LANGUAGE_RULES}}` are substituted, and it carries
  the facts-only directive + the 45-min/7-stage/5-question structure.
- the fidelity-contract loader returns a *separate* prompt the judge uses via
  `contract_override` — grades a serialized PLAIN-TEXT deck (not JSON),
  exempts teaching/structure numbers, and majors only on a contradiction.
- `get_prompt(subject, "teacher-deck")` (the markdown-contract lookup) does
  NOT resolve either prompt — proving the fidelity contract is reachable only
  by explicit loader / `contract_override`, never by phase-name lookup.
"""
import pytest

from app.services import prompts


def test_teacher_deck_structured_prompt_exists_and_substitutes():
    body = prompts.get_structured_prompt("history", "teacher-deck", output_language="uz")
    assert body is not None
    assert "{{SUBJECT}}" not in body
    assert "{{LANGUAGE_RULES}}" not in body


def test_teacher_deck_structured_prompt_has_facts_only_directive():
    body = prompts.get_structured_prompt("history", "teacher-deck")
    low = body.lower()
    assert "lesson context" in low
    assert "invent" in low or "fabricat" in low
    # teaching/structure numbers (timings, option counts) are the author's own
    assert "timing" in low or "option count" in low or "structure" in low


def test_teacher_deck_structured_prompt_guides_all_required_front_matter_fields():
    """Fix A (review round 1): the model only sees the bare `model_json_schema()`
    (no field `description=`s) and Task 6 fails the job loudly on schema-validation
    exhaustion — so every REQUIRED top-level block needs prose guidance here, not
    just the stage-by-stage plan."""
    body = prompts.get_structured_prompt("history", "teacher-deck")
    for field in (
        "meta", "passport", "objectives", "core_idea", "lesson_map",
        "subject_label", "topic_number", "duration_min", "video_ref",
        "fan_sinf", "mavzu", "dars_turi", "metod", "kerakli_vosita", "baholash",
        "bilib_oladi", "qila_oladi", "tushunadi",
        "statement", "elaboration",
    ):
        assert field in body, f"missing guidance for required field {field!r}"


def test_teacher_deck_structured_prompt_lesson_map_distinct_from_stages():
    body = prompts.get_structured_prompt("history", "teacher-deck")
    low = body.lower()
    assert "lesson_map" in low
    assert "separate from" in low or "distinct from" in low
    # lesson_map carries its own independent 45-minute total, same as stages
    assert low.count("sum to **45**") >= 1 or "must sum to" in low


def test_teacher_deck_structured_prompt_screen_text_badge_rule():
    body = prompts.get_structured_prompt("history", "teacher-deck")
    low = body.lower()
    assert "teacher_only" in low and "screen_text" in low
    assert "no `screen_text`" in low or "never carry `screen_text`" in low


def test_teacher_deck_structured_prompt_has_45_min_7_stage_structure():
    body = prompts.get_structured_prompt("history", "teacher-deck")
    assert "45" in body
    assert "7" in body
    for minutes in ("3", "9", "8", "4"):
        assert minutes in body
    assert "5" in body  # 5-question quiz
    assert "quiz" in body.lower()
    assert "rubric" in body.lower()
    assert "10" in body  # 10-point rubric


def test_teacher_deck_structured_prompt_clean_json_instruction():
    body = prompts.get_structured_prompt("history", "teacher-deck")
    assert "JSON" in body


def test_teacher_deck_fidelity_contract_loader_exists():
    contract = prompts.get_teacher_deck_fidelity_contract()
    assert contract and isinstance(contract, str)


def test_teacher_deck_fidelity_contract_grades_serialized_plain_text():
    contract = prompts.get_teacher_deck_fidelity_contract()
    low = contract.lower()
    assert "plain text" in low or "plain-text" in low
    assert "serializ" in low
    assert "json" not in low or "not json" in low or "never" in low


def test_teacher_deck_fidelity_contract_never_demands_json():
    contract = prompts.get_teacher_deck_fidelity_contract()
    low = contract.lower()
    # Belt-and-suspenders: the contract must not tell the judge to require a
    # JSON-shaped output (that would major "output isn't JSON" against the
    # deliberately-serialized plain-text view).
    assert "return json" not in low
    assert "json only" not in low
    assert "respond in json" not in low


def test_teacher_deck_fidelity_contract_teaching_numbers_exempt():
    contract = prompts.get_teacher_deck_fidelity_contract()
    low = contract.lower()
    assert "timing" in low or "timings" in low
    assert "option count" in low or "option counts" in low
    assert "not a defect" in low or "not defects" in low or "not flag" in low


def test_teacher_deck_fidelity_contract_major_only_on_contradiction():
    contract = prompts.get_teacher_deck_fidelity_contract()
    low = contract.lower()
    assert "contradict" in low
    assert "major" in low
    assert "minor" in low
    assert "absent" in low or "not mentioned" in low or "not present" in low


def test_teacher_deck_not_reachable_via_get_prompt():
    """The judge markdown-contract lookup (`get_prompt`) must MISS on
    "teacher-deck" — neither the authoring prompt nor the fidelity contract
    lives where `get_prompt` looks, so the judge is forced to receive the
    fidelity contract explicitly via `contract_override`."""
    with pytest.raises(KeyError):
        prompts.get_prompt("history", "teacher-deck")
