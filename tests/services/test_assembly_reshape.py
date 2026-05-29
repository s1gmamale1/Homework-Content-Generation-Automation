"""PR-5: Flow v2 assembly reshape (plan §8).

The assembled markdown handoff must carry the Flow v2 section structure —
a title, source book/chapter/section, extracted summary, a rendered Source Map,
and the three flow divisions (Learning Sections / Practice Arc / Boss Arena)
with each phase as a named subsection — then Reflection as the closing section.

These pin the pure renderer ``_render_homework_md`` (no DB needed).
"""

from __future__ import annotations

from app.services.pipeline import _render_homework_md, _strip_leading_md_heading

_SOURCE_MAP = {
    "subject_family": "math-algebra",
    "chapter": "Linear equations",
    "section": "Solving one-variable equations",
    "concepts": [
        {"id": "isolate-variable", "label": "Isolate the variable",
         "statement": "Move variable terms one side, constants the other.",
         "kind": "process"},
    ],
}

_PHASE_BODIES = {
    "case-based-preview": "## Case-Based Preview\n\nrole stuff",
    "flashcards": "_3 flashcards_\n\n1. **front** — back",
    "memory-check": "_items_",
    "practice-error-detection": "## Error Detection\n\nfind the bug",
    "practice-tictactoe": "## Game (CBP mode: tictactoe)\n\ngrid",
    "boss-arena": "_Boss Arena_\n\nq1",
    "reflection": "reflect here",
}


def _render(**over) -> str:
    base = dict(
        book_title="Algebra 7", chapter="Linear equations",
        section_number="3.1", section_title="Solving one-variable equations",
        page_start=40, page_end=45,
        extract_md="The section covers solving ax+b=c.",
        source_map_json=_SOURCE_MAP,
        phase_bodies=dict(_PHASE_BODIES),
    )
    base.update(over)
    return _render_homework_md(**base)


def test_has_top_title_and_metadata_section() -> None:
    md = _render()
    assert md.startswith("# Homework Content")
    assert "## Source Book / Chapter / Section" in md
    assert "Algebra 7" in md and "3.1" in md and "Solving one-variable equations" in md


def test_renders_extracted_summary_and_source_map() -> None:
    md = _render()
    assert "## Extracted Section Summary" in md
    assert "The section covers solving ax+b=c." in md
    assert "## Source Map" in md
    # concept surfaced (the map was previously persisted to JSON only).
    assert "isolate-variable" in md and "Isolate the variable" in md


def test_groups_into_three_divisions_in_order() -> None:
    md = _render()
    i_learn = md.index("## Learning Sections")
    i_prac = md.index("## Practice Arc")
    i_boss = md.index("## Boss Arena")
    i_refl = md.index("## Reflection")
    assert i_learn < i_prac < i_boss < i_refl
    # leaf phases sit as named subsections under their division
    assert "### Case-Based Preview" in md
    assert "### Error Detection" in md
    assert "### Real-Life Challenge" not in md  # this job didn't run RLC


def test_no_double_heading_for_structured_phase_bodies() -> None:
    # The CBP synth body starts with "## Case-Based Preview"; the renderer must
    # strip it so only its own "### Case-Based Preview" subsection remains (no
    # leftover "## Case-Based Preview" heading line from the body).
    md = _render()
    lines = md.splitlines()
    assert "## Case-Based Preview" not in lines  # body heading stripped
    assert lines.count("### Case-Based Preview") == 1


def test_unknown_phase_is_not_silently_dropped() -> None:
    bodies = dict(_PHASE_BODIES)
    bodies["mystery-phase"] = "some content here"
    md = _render(phase_bodies=bodies)
    assert "some content here" in md  # rendered somewhere, never dropped


def test_omits_empty_divisions() -> None:
    # A learning-only job has no Practice Arc / Boss sections.
    md = _render(phase_bodies={"flashcards": "_cards_", "reflection": "done"})
    assert "## Practice Arc" not in md
    assert "## Boss Arena" not in md
    assert "## Learning Sections" in md
    assert "## Reflection" in md


def test_strip_leading_md_heading() -> None:
    assert _strip_leading_md_heading("## Title\n\nbody") == "body"
    assert _strip_leading_md_heading("# Title\nbody") == "body"
    assert _strip_leading_md_heading("no heading\nbody") == "no heading\nbody"
    assert _strip_leading_md_heading("") == ""
