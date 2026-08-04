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


# ── Author-only answer key ───────────────────────────────────────────────────
# practice-rlc is in pipeline._SOLVER_PHASES, and the judge grades against a
# markdown contract that says "Mark which option is correct". So the rendered
# markdown MUST carry the key — but in a section the platform provably strips
# before a student sees it.

import importlib.util
import pathlib as _pl
import subprocess
import sys
import types

import pytest

_PLATFORM_REPO = _pl.Path("/Users/macmini5/Documents/Class-A-Education-Platform-Backend")


def _platform_redactor():
    """Load the platform's REAL redactor (django stubbed) so this test asserts
    against their behaviour, not a local copy of it."""
    dj = types.ModuleType("django")
    conf = types.ModuleType("django.conf")

    class _S:
        def __getattr__(self, n):
            return None

    conf.settings = _S()
    dj.conf = conf
    sys.modules.setdefault("django", dj)
    sys.modules.setdefault("django.conf", conf)
    src = subprocess.run(
        ["git", "-C", str(_PLATFORM_REPO), "show",
         "origin/Akademiya-AI:apps/library/redactor.py"],
        capture_output=True, text=True,
    ).stdout
    if not src.strip():
        pytest.skip("platform checkout unavailable")
    f = _pl.Path("/tmp/_plat_redactor_test.py")
    f.write_text(src)
    spec = importlib.util.spec_from_file_location("_plat_red_test", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rendered_markdown_carries_an_answer_key_for_the_solver():
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    assert "## Answer key" in md
    assert "Yes" in md.split("## Answer key", 1)[1]   # the correct option is listed


@pytest.mark.skipif(not _PLATFORM_REPO.exists(), reason="platform checkout absent")
def test_platform_redactor_strips_our_answer_key_section():
    red = _platform_redactor()
    md = phase_render.render_md("practice-rlc", _rlc_cfg())
    clean, dropped = red.strip_answer_sections(md)
    assert dropped == ["Answer key"]
    assert "## Answer key" not in clean
    # The fixture reuses "Yes"/"No" across the three option steps, so "Yes"
    # appears 3x as an option + 3x in the key. After stripping, only the three
    # option occurrences survive: RLC must still render every option, but the
    # key listing is gone.
    assert md.count("Yes") == 6
    assert clean.count("Yes") == 3
    # no numbered key rows survive
    assert "\n1. Yes" not in clean


@pytest.mark.skipif(not _PLATFORM_REPO.exists(), reason="platform checkout absent")
def test_platform_redactor_strips_sentence_answer_key():
    red = _platform_redactor()
    cfg = SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["cat"], "word_bank": ["cat", "dog"]}]})
    clean, dropped = red.strip_answer_sections(phase_render.render_md("practice-sentence", cfg))
    assert dropped == ["Answer key"]
    assert "## Answer key" not in clean
