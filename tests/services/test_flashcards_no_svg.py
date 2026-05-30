"""Regression: the `flashcards` phase must NOT receive the heavy inline-SVG
rules block. The Flow v2 plan keeps flashcards a "simple reference tool"
(plan §3/§5: "keep, add stable IDs") and the FlashcardsPack schema has no SVG
field — cards carry bracket `[Diagram: ...]` descriptions, not raw <svg>.

When flashcards was (wrongly) in `_SVG_PHASES`, claude generated a full inline
SVG per card and blew past the claude CLI's 32k output-token ceiling, failing
the job after a 29-minute call. These tests pin the fix.
"""

from app.services.agent import _SVG_PHASES, _SVG_RULES, _build_master_prompt

# A stable, unambiguous marker that only appears inside the SVG rules block.
_SVG_MARKER = "VISUAL / SVG RULES"


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


def test_flashcards_not_in_svg_phases():
    assert "flashcards" not in _SVG_PHASES


def test_flashcards_prompt_has_no_svg_rules():
    prompt = _prompt_for("flashcards")
    assert _SVG_MARKER not in prompt


def test_svg_marker_actually_present_in_svg_phase():
    # Guard against the marker string drifting: a real SVG phase must still
    # carry the rules, otherwise the negative assertions above are vacuous.
    assert _SVG_MARKER in _SVG_RULES
    prompt = _prompt_for("case-based-preview")
    assert _SVG_MARKER in prompt
