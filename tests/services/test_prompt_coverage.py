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
