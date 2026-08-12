"""Task 2 — render_teacher_deck_markdown: pure markdown renderer for the
readable Notion page + PDF (both consumers render from this one source).

Pure function, no I/O — unit-testable in isolation against the real fixture.
"""
import json

from app.schemas.content_json import TeacherDeck
from app.services.teacher_deck import render_teacher_deck_markdown

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _deck() -> TeacherDeck:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return TeacherDeck.model_validate(json.load(fh))


def test_h1_contains_topic_number_and_title():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    assert str(deck.meta.topic_number) in out
    assert deck.meta.topic_title in out


def test_passport_fields_and_content_loss_guard_for_method_and_materials():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    # Every passport field value appears.
    assert deck.passport.fan_sinf in out
    assert deck.passport.mavzu in out
    assert deck.passport.dars_turi in out
    assert deck.passport.metod in out
    assert deck.passport.kerakli_vosita in out
    assert deck.passport.baholash in out
    # Content-loss guard: method/materials are dropped by the fidelity
    # serializer — the readable page must keep them.
    assert deck.meta.method[0] in out
    assert deck.meta.materials[0] in out


def test_stage_headings_appear_in_ascending_index_order():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    positions = []
    for stage in sorted(deck.stages, key=lambda s: s.index):
        heading = f"### {stage.index}-bosqich"
        pos = out.find(heading)
        assert pos != -1, f"missing heading for stage {stage.index}"
        positions.append(pos)
    assert positions == sorted(positions)


def test_first_quiz_item_options_and_correct_label_render():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    first = deck.quiz[0]
    for opt in first.options:
        assert opt.text in out
    assert first.correct_label in out


def test_content_loss_guard_pair_work_and_conclusion():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    assert deck.pair_work.tasks[0].prompt in out
    assert deck.conclusion.questions[0] in out


def test_rubric_total_line_renders():
    deck = _deck()
    out = render_teacher_deck_markdown(deck)
    assert f"{deck.rubric.total} ball" in out
