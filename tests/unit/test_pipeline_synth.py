"""Tests for pipeline._synth_md_for_structured — pure function, no DB needed."""
import pytest
from types import SimpleNamespace

from app.services.pipeline import _synth_md_for_structured


def ns(**kwargs):
    """Convenience: build a SimpleNamespace to simulate parsed Pydantic models."""
    return SimpleNamespace(**kwargs)


# ─── classify ────────────────────────────────────────────────────────────────

class TestSynthClassify:
    def test_easy_with_reason(self):
        parsed = ns(difficulty="easy", reason="vocabulary is simple")
        result = _synth_md_for_structured("classify", parsed)
        assert "EASY" in result
        assert "vocabulary is simple" in result

    def test_hard_without_reason(self):
        parsed = ns(difficulty="hard", reason=None)
        result = _synth_md_for_structured("classify", parsed)
        assert "HARD" in result
        assert "—" not in result

    def test_empty_reason_omitted(self):
        parsed = ns(difficulty="easy", reason="")
        result = _synth_md_for_structured("classify", parsed)
        assert "—" not in result

    def test_missing_difficulty_falls_back_to_question_mark(self):
        parsed = ns()  # no difficulty attribute
        result = _synth_md_for_structured("classify", parsed)
        assert "?" in result


# ─── flashcards ──────────────────────────────────────────────────────────────

class TestSynthFlashcards:
    def _card(self, front, back, hint=None):
        return ns(front=front, back=back, hint=hint)

    def test_card_count_in_header(self):
        parsed = ns(cards=[self._card("Q1", "A1"), self._card("Q2", "A2")])
        result = _synth_md_for_structured("flashcards", parsed)
        assert "2 flashcards" in result

    def test_card_fronts_and_backs_present(self):
        parsed = ns(cards=[self._card("Capital of France", "Paris")])
        result = _synth_md_for_structured("flashcards", parsed)
        assert "Capital of France" in result
        assert "Paris" in result

    def test_hint_present_when_set(self):
        parsed = ns(cards=[self._card("Q", "A", hint="starts with P")])
        result = _synth_md_for_structured("flashcards", parsed)
        assert "starts with P" in result

    def test_hint_omitted_when_none(self):
        parsed = ns(cards=[self._card("Q", "A", hint=None)])
        result = _synth_md_for_structured("flashcards", parsed)
        assert "hint:" not in result

    def test_empty_card_list(self):
        parsed = ns(cards=[])
        result = _synth_md_for_structured("flashcards", parsed)
        assert "0 flashcards" in result

    def test_none_cards_attribute(self):
        parsed = ns(cards=None)
        result = _synth_md_for_structured("flashcards", parsed)
        assert "0 flashcards" in result


# ─── memory-sprint ───────────────────────────────────────────────────────────

class TestSynthMemorySprint:
    def _item(self, kind, prompt, options=None, correct_index=0, explanation=None):
        return ns(kind=kind, prompt=prompt, options=options or [],
                  correct_index=correct_index, explanation=explanation)

    def test_item_count_in_header(self):
        parsed = ns(items=[self._item("mc", "Q1"), self._item("tf", "Q2")])
        result = _synth_md_for_structured("memory-sprint", parsed)
        assert "2 rapid-fire items" in result

    def test_correct_answer_marked_with_checkmark(self):
        parsed = ns(items=[self._item("mc", "Who wrote Hamlet?",
                                     options=["Marlowe", "Shakespeare"],
                                     correct_index=1)])
        result = _synth_md_for_structured("memory-sprint", parsed)
        assert "✓" in result

    def test_kind_uppercased_in_bracket(self):
        parsed = ns(items=[self._item("mc", "Question")])
        result = _synth_md_for_structured("memory-sprint", parsed)
        assert "[MC]" in result

    def test_explanation_shown_when_set(self):
        parsed = ns(items=[self._item("mc", "Q", explanation="because X")])
        result = _synth_md_for_structured("memory-sprint", parsed)
        assert "because X" in result

    def test_empty_items(self):
        parsed = ns(items=[])
        result = _synth_md_for_structured("memory-sprint", parsed)
        assert "0 rapid-fire" in result


# ─── game-breaks ─────────────────────────────────────────────────────────────

class TestSynthGameBreaks:
    def _game(self, title, game_type, questions=None, pairs=None, cards=None):
        return ns(title=title, type=game_type,
                  questions=questions, pairs=pairs, cards=cards)

    def test_game_count_in_header(self):
        games = [self._game("Quiz 1", "quiz", questions=["q"] * 5)]
        parsed = ns(games=games)
        result = _synth_md_for_structured("game-breaks", parsed)
        assert "1 game break" in result

    def test_game_title_shown(self):
        parsed = ns(games=[self._game("Vocab Match", "tile-match", pairs=["p"] * 3)])
        result = _synth_md_for_structured("game-breaks", parsed)
        assert "Vocab Match" in result

    def test_item_count_from_questions(self):
        parsed = ns(games=[self._game("Q", "quiz", questions=list(range(7)))])
        result = _synth_md_for_structured("game-breaks", parsed)
        assert "7" in result

    def test_item_count_from_pairs_fallback(self):
        parsed = ns(games=[self._game("M", "memory-match", pairs=list(range(4)))])
        result = _synth_md_for_structured("game-breaks", parsed)
        assert "4" in result

    def test_empty_games_list(self):
        parsed = ns(games=[])
        result = _synth_md_for_structured("game-breaks", parsed)
        assert "0 game break" in result


# ─── final-challenge ─────────────────────────────────────────────────────────

class TestSynthFinalChallenge:
    def _q(self, kind, prompt, damage=10, options=None, correct_index=None,
            correct_answer=None, explanation=None):
        return ns(kind=kind, prompt=prompt, damage=damage,
                  options=options, correct_index=correct_index,
                  correct_answer=correct_answer, explanation=explanation)

    def test_title_and_hp_in_header(self):
        parsed = ns(title="Boss Fight", starting_hp=100, questions=[])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "Boss Fight" in result
        assert "100" in result

    def test_question_count_shown(self):
        qs = [self._q("mc", "Q1"), self._q("short", "Q2")]
        parsed = ns(title="T", starting_hp=100, questions=qs)
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "2 questions" in result

    def test_damage_shown_per_question(self):
        parsed = ns(title="T", starting_hp=100,
                    questions=[self._q("mc", "Q", damage=15)])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "-15 HP" in result

    def test_mc_correct_option_marked(self):
        parsed = ns(title="T", starting_hp=100,
                    questions=[self._q("mc", "Q", options=["A", "B"], correct_index=0)])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "✓" in result

    def test_short_answer_shown(self):
        parsed = ns(title="T", starting_hp=100,
                    questions=[self._q("short", "Q", correct_answer="photosynthesis")])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "photosynthesis" in result

    def test_explanation_shown(self):
        parsed = ns(title="T", starting_hp=100,
                    questions=[self._q("mc", "Q", explanation="key concept")])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "key concept" in result

    def test_default_title_when_missing(self):
        parsed = ns(starting_hp=100, questions=[])
        result = _synth_md_for_structured("final-challenge", parsed)
        assert "Final Challenge" in result


# ─── reading ─────────────────────────────────────────────────────────────────

class TestSynthReading:
    def _checkpoint(self):
        return ns(question="What is X?")

    def test_checkpoint_count_shown(self):
        parsed = ns(passage_md="Some text.", cefr_level="B1",
                    checkpoints=[self._checkpoint(), self._checkpoint()])
        result = _synth_md_for_structured("reading", parsed)
        assert "2 comprehension checkpoints" in result

    def test_cefr_level_shown(self):
        parsed = ns(passage_md="text", cefr_level="A2", checkpoints=[])
        result = _synth_md_for_structured("reading", parsed)
        assert "A2" in result

    def test_passage_md_included(self):
        parsed = ns(passage_md="Once upon a time.", cefr_level=None, checkpoints=[])
        result = _synth_md_for_structured("reading", parsed)
        assert "Once upon a time." in result

    def test_cefr_omitted_when_none(self):
        parsed = ns(passage_md="text", cefr_level=None, checkpoints=[])
        result = _synth_md_for_structured("reading", parsed)
        assert "CEFR" not in result


# ─── unknown phase ───────────────────────────────────────────────────────────

class TestSynthUnknownPhase:
    def test_unknown_phase_returns_string(self):
        parsed = ns()
        result = _synth_md_for_structured("unknown-phase-xyz", parsed)
        assert isinstance(result, str)
