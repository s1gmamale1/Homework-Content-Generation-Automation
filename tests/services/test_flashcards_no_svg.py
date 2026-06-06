"""Regression: content phases use the placeholder visual policy, never the old
inline-SVG rules. `flashcards` (atomic reference cards) gets no visual-rules block
at all; a real visual phase (case-based-preview) gets the placeholder rules.

History: flashcards once sat in the SVG-rules set and claude emitted a full inline
SVG per card, blowing the 32k output ceiling. The engine now never emits <svg> —
phases describe visuals as `![visual: … ](placeholder)` instead.
"""

from app.services.agent import (
    _PLACEHOLDER_RULES,
    _VISUAL_PHASES,
    _build_master_prompt,
)

# Marker that only appears inside the placeholder rules block.
_RULES_MARKER = "VISUAL RULES (placeholders only"


def _prompt_for(phase_name: str) -> str:
    return _build_master_prompt(
        phase_prompt="Build the deck.",
        phase_name=phase_name,
        lesson_context="Some lesson text.",
        prior_outputs=None,
        difficulty="hard",
        schema=None,
        provider_suffix="",
    )


def test_flashcards_not_in_visual_phases():
    assert "flashcards" not in _VISUAL_PHASES


def test_flashcards_prompt_has_no_visual_rules():
    prompt = _prompt_for("flashcards")
    assert _RULES_MARKER not in prompt


def test_placeholder_rules_forbid_svg_and_reach_visual_phase():
    # The rules must forbid <svg> and carry the sentinel format, and a real
    # visual phase must actually receive them.
    assert _RULES_MARKER in _PLACEHOLDER_RULES
    assert "never emit `<svg>`" in _PLACEHOLDER_RULES
    assert "](placeholder)" in _PLACEHOLDER_RULES
    prompt = _prompt_for("case-based-preview")
    assert _RULES_MARKER in prompt
