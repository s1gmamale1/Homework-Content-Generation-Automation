"""Fail-fast: every phase in the general flow has a _general prompt."""
import pathlib
import pytest
from app.services import flows
from app.services.prompts import get_prompt

_PAIRS = [(s, p) for s in flows.SUPPORTED_SUBJECTS for p in flows.GENERAL_FLOW]


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
