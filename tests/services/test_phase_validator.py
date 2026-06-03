# tests/services/test_phase_validator.py
from app.services import phase_validator as pv


def test_empty_output_warns():
    assert pv.validate("flashcards", "   \n  ") == ["empty output"]


def test_missing_top_heading_warns():
    out = pv.validate("flashcards", "some body text\n\nmore text")
    assert "missing top-level heading (`# `)" in out


def test_well_formed_markdown_no_warnings():
    md = "# Flashcards\n\nsome body\n"
    assert pv.validate("flashcards", md) == []


def test_placeholder_image_is_allowed():
    md = "# Case\n\n![placeholder: lab bench — image gen required](placeholder)\n"
    assert pv.validate("case-based-preview", md) == []


def test_broken_image_target_warns():
    md = "# Case\n\n![scene](scene.png)\n"
    out = pv.validate("case-based-preview", md)
    assert any("non-resolving image target" in w for w in out)
