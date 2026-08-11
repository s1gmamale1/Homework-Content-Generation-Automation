"""TeacherDeck schema tests.

Pure unit tests — no DB, no RUN_DB_INTEGRATION. The fixture
tests/fixtures/teacher_deck/hindiston_topic19.json is a faithful transcription
of the real 18-slide QOLLANMA_Jahon-tarixi_11-sinf_19-mavzu_Hindiston.pdf
template and is the committed source of truth.
"""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.content_json import SCHEMAS, TeacherDeck

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "teacher_deck" / "hindiston_topic19.json"
)


@pytest.fixture()
def fixture_data():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_schema_registered():
    assert SCHEMAS["teacher-deck"] is TeacherDeck


def test_fixture_validates(fixture_data):
    deck = TeacherDeck.model_validate(fixture_data)
    assert deck.SCHEMA_VERSION == "teacher_deck@1"
    assert deck.meta.topic_number == 19
    assert len(deck.lesson_map) == 7
    assert len(deck.stages) == 7
    assert len(deck.quiz) == 5
    assert len(deck.answer_key) == 5


def test_schema_version_is_classvar_not_a_payload_field(fixture_data):
    deck = TeacherDeck.model_validate(fixture_data)
    assert "SCHEMA_VERSION" not in deck.model_dump(mode="json")
    assert "SCHEMA_VERSION" not in TeacherDeck.model_fields


def test_json_schema_generates(fixture_data):
    # Sanity: model_json_schema() must not blow up — it's embedded verbatim
    # into the prompt by run_phase(schema=...).
    schema = TeacherDeck.model_json_schema()
    assert schema["title"] == "TeacherDeck"
    assert "properties" in schema


def test_lesson_map_minutes_must_sum_to_duration(fixture_data):
    bad = copy.deepcopy(fixture_data)
    bad["lesson_map"][0]["minutes"] = 99
    with pytest.raises(ValidationError, match="lesson_map minutes"):
        TeacherDeck.model_validate(bad)


def test_quiz_item_must_have_exactly_four_options(fixture_data):
    bad = copy.deepcopy(fixture_data)
    bad["quiz"][0]["options"].pop()
    with pytest.raises(ValidationError, match="exactly 4 options"):
        TeacherDeck.model_validate(bad)


def test_quiz_correct_label_must_be_among_options(fixture_data):
    bad = copy.deepcopy(fixture_data)
    # All 4 options use labels A-D already; force an inconsistent correct_label
    # by relabeling one option so A-D no longer includes the stated correct answer.
    bad["quiz"][0]["correct_label"] = "B"
    bad["quiz"][0]["options"] = [
        {"label": "A", "text": "x"},
        {"label": "C", "text": "y"},
        {"label": "D", "text": "z"},
        {"label": "A", "text": "w"},
    ]
    with pytest.raises(ValidationError):
        TeacherDeck.model_validate(bad)


def test_answer_key_number_set_must_match_quiz(fixture_data):
    bad = copy.deepcopy(fixture_data)
    bad["answer_key"][0]["number"] = 99
    with pytest.raises(ValidationError, match="answer_key numbers must match quiz numbers"):
        TeacherDeck.model_validate(bad)


def test_answer_key_correct_label_must_match_quiz_item(fixture_data):
    bad = copy.deepcopy(fixture_data)
    # quiz[0].correct_label is "B" in the fixture; flip the answer_key's label.
    assert fixture_data["quiz"][0]["correct_label"] == "B"
    bad["answer_key"][0]["correct_label"] = "A"
    with pytest.raises(ValidationError, match="must match"):
        TeacherDeck.model_validate(bad)


def test_rubric_points_must_sum_to_total(fixture_data):
    bad = copy.deepcopy(fixture_data)
    bad["rubric"]["total"] = 999
    with pytest.raises(ValidationError, match="rubric component points"):
        TeacherDeck.model_validate(bad)


def test_hook_screen_text_round_trips(fixture_data):
    deck = TeacherDeck.model_validate(fixture_data)
    hook = deck.stages[1]
    assert hook.index == 2
    assert hook.badge == "ekranga"
    assert hook.screen_text is not None
    assert "25 yilda nima" in hook.screen_text
    dumped = deck.model_dump(mode="json")
    assert dumped["stages"][1]["screen_text"] == fixture_data["stages"][1]["screen_text"]


def test_video_choreography_points_round_trip(fixture_data):
    deck = TeacherDeck.model_validate(fixture_data)
    video_stage = deck.stages[2]
    assert video_stage.index == 3
    expected_titles = [p["title"] for p in fixture_data["stages"][2]["points"]]
    assert len(expected_titles) == 3
    assert [p.title for p in video_stage.points] == expected_titles
    dumped = deck.model_dump(mode="json")
    assert dumped["stages"][2]["points"] == fixture_data["stages"][2]["points"]
