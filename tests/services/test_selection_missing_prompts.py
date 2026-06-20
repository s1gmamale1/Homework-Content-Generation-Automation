"""Unit tests for flows.selection_missing_prompts — the 'every picked phase
needs an uploaded md' rule that gates the pick-phases custom-prompt flow."""

from app.services.flows import selection_missing_prompts


def test_full_packet_is_exempt():
    # selected_phases is None ⇒ built-in prompts, no upload required.
    assert selection_missing_prompts(None, None) == []
    assert selection_missing_prompts(None, {"flashcards": "x"}) == []


def test_all_selected_have_prompts():
    sel = ["flashcards", "boss-arena"]
    cp = {"flashcards": "md1", "boss-arena": "md2"}
    assert selection_missing_prompts(sel, cp) == []


def test_some_selected_missing_prompts_preserves_order():
    sel = ["case-based-preview", "flashcards", "boss-arena"]
    cp = {"flashcards": "md"}
    assert selection_missing_prompts(sel, cp) == ["case-based-preview", "boss-arena"]


def test_no_prompts_at_all_returns_full_selection():
    sel = ["flashcards", "boss-arena"]
    assert selection_missing_prompts(sel, None) == ["flashcards", "boss-arena"]
    assert selection_missing_prompts(sel, {}) == ["flashcards", "boss-arena"]


def test_empty_or_whitespace_prompt_counts_as_missing():
    sel = ["flashcards", "boss-arena"]
    cp = {"flashcards": "   ", "boss-arena": ""}
    assert selection_missing_prompts(sel, cp) == ["flashcards", "boss-arena"]


def test_extra_prompts_for_unselected_phases_are_ignored():
    sel = ["flashcards"]
    cp = {"flashcards": "md", "reflection": "unused"}
    assert selection_missing_prompts(sel, cp) == []
