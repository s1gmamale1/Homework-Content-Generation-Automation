import pytest

from app.schemas.content_json import RlcConfig, SentenceFillConfig
from app.services import content_lint, phase_render


def _rlc_cfg():
    def opts():
        return [{"id": "o0", "label": "Yes", "is_correct": True},
                {"id": "o1", "label": "No", "is_correct": False}]
    return RlcConfig.model_validate({
        "id": "c1", "title": "Fire audit", "intro": "You inspect a hall.",
        "expert_role": "fire_inspector",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "Choose", "prompt": "Evacuate?", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "Ask", "prompt": "What data?", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "Decide", "prompt": "Final?", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "Concept", "prompt": "Which?",
             "concept_chips": [{"id": "k1", "label": "Load", "is_correct": True},
                               {"id": "k2", "label": "Colour", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "Explain", "prompt": "Why?", "min_chars": 80},
        ],
    })


def test_render_rlc_has_title_and_every_step_and_option():
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    assert md.startswith("# ")
    assert "Fire audit" in md
    for text in ("Evacuate?", "What data?", "Final?", "Which?", "Why?", "Yes", "No", "Load"):
        assert text in md


def test_render_rlc_passes_content_lint():
    # content_lint.lint_phase has no "empty_body" code (verified against the real
    # source: the only codes it ever emits are ru_uzbek_leak, mixed_script,
    # english_template, calque, english_heading_leak, errdet_no_broken_marker,
    # errdet_multiple_broken, errdet_reveal_mismatch — none of them "empty output").
    # The meaningful contract for a renderer is that its output trips ZERO
    # advisory findings, so assert the finding list is empty outright.
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    findings = content_lint.lint_phase(
        "practice-rlc", md, subject="history", output_language="ru"
    )
    assert findings == []


def test_render_sentence_lists_passage_and_bank():
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})
    md = phase_render.render_md("practice-sentence", cfg)
    assert "A ___ ran." in md and "cat" in md and "dog" in md


def test_render_unknown_phase_raises():
    with pytest.raises(phase_render.RenderError):
        phase_render.render_md("flashcards", _rlc_cfg())
