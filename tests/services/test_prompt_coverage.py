"""Fail-fast: every phase in the general flow has a _general prompt."""
import pathlib
import pytest
from app.services import flows
from app.services.prompts import get_prompt
from app.services.prompts import get_prompt as _gp

_PAIRS = [(s, p) for s in flows.SUPPORTED_SUBJECTS for p in flows.flow_for(s)]

_DEAD_VOCAB = [
    "options: null", "eval_mode", "min_chars", "source_concept_ids",
    "interaction_payload", "interaction_mode", "chips[]",
    "expected_reasoning_keywords", "base_damage", "accepted_variants",
    "source map", "allowed_assembly_types",
]


def _assert_clean(rendered: str):
    low = rendered.lower()
    hits = [tok for tok in _DEAD_VOCAB if tok.lower() in low]
    assert not hits, f"dead JSON vocab still present: {hits}"


def test_cbp_has_family_token():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "case-based-preview.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body


def test_cbp_family_visual_defaults_distinct_and_clean():
    sci = _gp("biology", "case-based-preview")
    mat = _gp("math-algebra", "case-based-preview")
    lan = _gp("english", "case-based-preview")
    hum = _gp("history", "case-based-preview")
    for r in (sci, mat, lan, hum):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    assert "](placeholder)" in sci and "](placeholder)" in lan and "](placeholder)" in hum
    assert sci != mat and lan != hum and sci != hum


def test_flashcards_has_family_token_and_canonical_enum():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "flashcards.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body
    for t in ("definition", "term_to_meaning", "process_step",
              "question_answer", "misconception", "image_label"):
        assert t in body, f"canonical type {t} missing"
    assert "FlashcardType" not in body


def test_flashcards_families_distinct_and_clean():
    sci = _gp("physics", "flashcards")
    lan = _gp("english", "flashcards")
    hum = _gp("history", "flashcards")
    mat = _gp("geometriya-g7-11", "flashcards")
    for r in (sci, lan, hum, mat):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    assert sci != lan and lan != hum and sci != mat


def test_memory_check_clean_and_consistent():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "memory-check.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "memory-check"))
    assert "all 3 kinds" not in body


def test_rlc_and_error_detection_clean_with_strip_test():
    for subj, phase, path in [
        ("biology", "practice-rlc", "practice-rlc.md"),
        ("biology", "practice-error-detection", "practice-error-detection.md"),
    ]:
        body = (pathlib.Path(__file__).resolve().parents[2]
                / "prompts" / "_general" / path).read_text(encoding="utf-8")
        _assert_clean(_gp(subj, phase))
        assert "strip" in body.lower(), f"{path} missing the Strip Test rule"


def test_boss_arena_clean_with_adaptation():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "boss-arena.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "boss-arena"))
    low = body.lower()
    assert "weak" in low, "missing weak-skill adaptation rule"
    for part in ("why", "how", "what"):
        assert part in low


@pytest.mark.parametrize("subject,phase", _PAIRS)
def test_every_flow_phase_has_a_general_prompt(subject, phase):
    body = get_prompt(subject, phase)
    assert body.strip(), f"empty prompt for {phase}"
    assert "{{SUBJECT}}" not in body  # substituted


def test_every_general_prompt_has_language_token():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    missing = [p.name for p in gdir.glob("*.md")
               if "{{LANGUAGE_RULES}}" not in p.read_text(encoding="utf-8")]
    assert not missing, f"prompts missing language token: {missing}"
