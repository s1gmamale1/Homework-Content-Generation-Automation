import pytest
from pydantic import ValidationError

from app.schemas.content_json import SCHEMAS, RlcConfig, SentenceFillConfig, norm


def test_norm_matches_mobile():
    assert norm("  Hello   World ") == "hello world"


def _rlc(**over):
    def opts(n=2):
        return [{"id": f"o{i}", "label": f"L{i}", "is_correct": i == 0} for i in range(n)]
    cfg = {
        "id": "c1", "title": "T", "intro": "I", "expert_role": "historian",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "a", "prompt": "p", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "b", "prompt": "p", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "c", "prompt": "p", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "d", "prompt": "p",
             "concept_chips": [{"id": "c1", "label": "A", "is_correct": True},
                               {"id": "c2", "label": "B", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "e", "prompt": "p", "min_chars": 80},
        ],
    }
    cfg.update(over)
    return cfg


def test_rlc_happy_path():
    m = RlcConfig.model_validate(_rlc())
    assert m.SCHEMA_VERSION == "rlc_config@1"
    assert len(m.steps) == 5


def test_rlc_rejects_bad_expert_role():
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(_rlc(expert_role="wizard"))


def test_rlc_rejects_extra_keys():
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(_rlc(answer_key="Paris"))


@pytest.mark.parametrize("bad", [-1, 0, 19, 1001, True])
def test_rlc_min_chars_bounded(bad):
    cfg = _rlc()
    cfg["steps"][4]["min_chars"] = bad
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def test_rlc_requires_exactly_one_correct_option():
    cfg = _rlc()
    for o in cfg["steps"][0]["options"]:
        o["is_correct"] = True
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def test_rlc_option_labels_normalized_unique_and_non_empty():
    cfg = _rlc()
    cfg["steps"][0]["options"][1]["label"] = "  l0  "   # normalizes to "l0"
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def _sf(**over):
    cfg = {"items": [{
        "id": "i1", "mode": "word_bank",
        "passage": "A ___ and a ___.",
        "answers": ["cat", "dog"],
        "word_bank": ["cat", "dog", "fox"],
    }]}
    cfg.update(over)
    return cfg


def test_sentence_fill_happy_path():
    m = SentenceFillConfig.model_validate(_sf())
    assert m.SCHEMA_VERSION == "sentence_fill_config@1"


def test_sentence_fill_rejects_free_recall():
    cfg = _sf()
    cfg["items"][0]["mode"] = "free_recall"
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_answers_must_match_blank_count():
    cfg = _sf()
    cfg["items"][0]["answers"] = ["cat"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_duplicate_answers_rejected():
    cfg = _sf()
    cfg["items"][0]["answers"] = ["cat", " CAT "]
    cfg["items"][0]["word_bank"] = ["cat", "fox"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_bank_must_contain_every_answer():
    cfg = _sf()
    cfg["items"][0]["word_bank"] = ["cat", "fox"]
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_bank_membership_is_EXACT_not_normalized():
    """The platform validator does `all(a in bank for a in answers)` — plain
    string membership, no normalization. Accepting a case-differing answer here
    produced a config we called valid and the platform REJECTS at publish, which
    is precisely the cross-repo divergence this schema exists to prevent.
    """
    cfg = _sf()
    cfg["items"][0]["answers"] = ["Cat", "dog"]
    cfg["items"][0]["word_bank"] = ["cat", "dog", "fox"]   # 'Cat' not present verbatim
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_word_bank_needs_at_least_one_distractor():
    """A bank with no distractor is not an exercise.

    Redaction pops `answers` and ships `word_bank` as the chips, so answers ==
    bank means the student is handed exactly the right words, one per blank.
    A single-blank item degenerates to one tappable chip: unfailable, and it
    teaches nothing. The prompt already asks for "1-3 plausible distractors" —
    this makes the schema enforce what the contract already says.
    """
    cfg = _sf()
    cfg["items"][0]["word_bank"] = ["cat", "dog"]          # == answers, no distractor
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_rlc_duplicate_option_ids_rejected():
    """RED-proof case. The platform's `grade_rlc` resolves a submitted answer with
    ``next((o for o in opts if o.get("id") == ua), None)`` — FIRST id match wins.
    Two options sharing an id therefore grade against the option the student never
    saw: here `dup` awards 0 for the WRONG label while the visibly-correct option
    is unreachable. Before this rule the config validated clean."""
    cfg = _rlc()
    cfg["steps"][0]["options"] = [
        {"id": "dup", "label": "WRONG answer", "is_correct": False},
        {"id": "dup", "label": "RIGHT answer", "is_correct": True},
    ]
    with pytest.raises(ValidationError, match="duplicate option id"):
        RlcConfig.model_validate(cfg)


def test_rlc_duplicate_option_ids_rejected_case_insensitively():
    cfg = _rlc()
    cfg["steps"][2]["options"] = [
        {"id": "O1", "label": "a", "is_correct": True},
        {"id": " o1 ", "label": "b", "is_correct": False},
    ]
    with pytest.raises(ValidationError, match="duplicate option id"):
        RlcConfig.model_validate(cfg)


def test_rlc_duplicate_chip_ids_rejected():
    cfg = _rlc()
    cfg["steps"][3]["concept_chips"] = [
        {"id": "k", "label": "A", "is_correct": True},
        {"id": "k", "label": "B", "is_correct": False},
    ]
    with pytest.raises(ValidationError, match="duplicate chip id"):
        RlcConfig.model_validate(cfg)


def test_rlc_duplicate_step_ids_rejected():
    cfg = _rlc()
    cfg["steps"][1]["id"] = "s1"
    with pytest.raises(ValidationError, match="duplicate step id"):
        RlcConfig.model_validate(cfg)


@pytest.mark.parametrize("field", ["id", "title", "intro"])
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n "])
def test_rlc_top_level_strings_reject_whitespace_only(field, blank):
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(_rlc(**{field: blank}))


@pytest.mark.parametrize("field", ["id", "title", "prompt"])
def test_rlc_step_strings_reject_whitespace_only(field):
    cfg = _rlc()
    cfg["steps"][0][field] = "   "
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


@pytest.mark.parametrize("field", ["id", "label"])
def test_rlc_choice_strings_reject_whitespace_only(field):
    cfg = _rlc()
    cfg["steps"][0]["options"][0][field] = " \t "
    with pytest.raises(ValidationError):
        RlcConfig.model_validate(cfg)


def test_rlc_strings_are_stored_stripped():
    cfg = _rlc(title="  Fire drill  ")
    cfg["steps"][0]["options"][0]["label"] = "  Evacuate  "
    m = RlcConfig.model_validate(cfg)
    assert m.title == "Fire drill"
    assert m.steps[0].options[0].label == "Evacuate"


def test_sentence_fill_item_id_rejects_whitespace_only():
    cfg = _sf()
    cfg["items"][0]["id"] = "   "
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_sentence_fill_duplicate_item_ids_rejected():
    cfg = _sf()
    cfg["items"].append(dict(cfg["items"][0]))
    with pytest.raises(ValidationError, match="duplicate item id"):
        SentenceFillConfig.model_validate(cfg)


@pytest.mark.parametrize("field", ["answers", "word_bank"])
def test_sentence_fill_entries_reject_whitespace_only(field):
    cfg = _sf()
    cfg["items"][0]["answers"] = ["cat", "dog"]
    cfg["items"][0]["word_bank"] = ["cat", "dog", "fox"]
    cfg["items"][0][field][1] = "   "
    with pytest.raises(ValidationError):
        SentenceFillConfig.model_validate(cfg)


def test_schemas_registry():
    assert SCHEMAS["practice-rlc"] is RlcConfig
    assert SCHEMAS["practice-sentence"] is SentenceFillConfig


def test_schema_version_is_classvar_not_a_payload_field():
    """The version travels in the ENVELOPE as content_schema_version. If it were a
    pydantic field it would appear inside content_json, adding an unknown key to the
    object we ship — exactly what extra="forbid" + model_dump() exists to prevent."""
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})
    assert "SCHEMA_VERSION" not in cfg.model_dump(mode="json")
    assert cfg.SCHEMA_VERSION == "sentence_fill_config@1"
    assert "SCHEMA_VERSION" not in SentenceFillConfig.model_fields
    assert "SCHEMA_VERSION" not in RlcConfig.model_fields
