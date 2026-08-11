"""Teacher-deck fidelity serialization (Task 7).

`serialize_deck_for_fidelity` renders ONLY the fact-bearing content of a
generated `TeacherDeck` to plain text, for the factual-fidelity judge pass
run by `pipeline._execute_teacher_deck_phase` after generation. The judge
(`phase_judge.judge(..., contract_override=get_teacher_deck_fidelity_contract())`)
grades this plain text against the lesson extract for contradictions — it is
explicitly instructed NOT to flag teaching/structural numbers (stage timings,
badge values, rubric points), so those are deliberately excluded here rather
than relying on the judge to ignore them: a field the judge never sees can
never trigger a false-positive regen.

Included (facts a generator could get wrong about the world):
  - objectives (all three fields)
  - core_idea.statement + .elaboration
  - each stage's teacher_action, points (title/detail), and screen_text
  - each quiz question + option texts + correct_label
  - each answer_key correct_label + explanation
  - conclusion.questions

`teacher_action` is unconstrained prose the teacher says aloud and CAN carry a
fabricated fact (e.g. a wrong date cited mid-lecture) — it belongs with the
other fact-bearing fields. `student_action` describes what students DO
(minimal fact surface) and stays excluded. Including teacher_action is safe
against false positives: the fidelity contract instructs the judge to flag
only CONTRADICTIONS of the lesson context and to ignore teaching/structure
numbers, so ordinary "ask a question" / "collect notebooks" prose never
triggers a regen.

Excluded (process/teaching chrome, not claims about the world):
  - meta / passport (grade, timings, method/materials labels)
  - lesson_map (minutes)
  - stage.index / .minutes / .badge / .student_action
  - pair_work (invented-but-fictional practice content, not source facts)
  - rubric (scoring chrome)

This list must stay in lockstep with
`prompts/_general/structured/teacher-deck.fidelity.md`'s "What you are
grading" section — that contract names exactly the sections emitted here, no
more, no less; the judge should never be told it's grading a section this
serializer doesn't actually show it.

Pure function — no I/O, unit-testable in isolation.
"""
from __future__ import annotations

from app.schemas.content_json import TeacherDeck


def serialize_deck_for_fidelity(deck: TeacherDeck) -> str:
    """Plain-text rendering of `deck`'s fact-bearing content for the judge."""
    lines: list[str] = []

    lines.append("## Objectives")
    lines.append(deck.objectives.bilib_oladi)
    lines.append(deck.objectives.qila_oladi)
    lines.append(deck.objectives.tushunadi)
    lines.append("")

    lines.append("## Core idea")
    lines.append(deck.core_idea.statement)
    lines.append(deck.core_idea.elaboration)
    lines.append("")

    lines.append("## Stages")
    for stage in deck.stages:
        lines.append(f"- {stage.teacher_action}")
        for point in stage.points:
            lines.append(f"- {point.title}: {point.detail}")
        if stage.screen_text:
            lines.append(stage.screen_text)
    lines.append("")

    lines.append("## Quiz")
    for q in deck.quiz:
        lines.append(f"Q{q.number}: {q.question}")
        for opt in q.options:
            lines.append(f"  {opt.label}) {opt.text}")
        lines.append(f"  correct: {q.correct_label}")
    lines.append("")

    lines.append("## Answer key")
    for a in deck.answer_key:
        lines.append(f"A{a.number} ({a.correct_label}): {a.explanation}")
    lines.append("")

    lines.append("## Conclusion")
    for question in deck.conclusion.questions:
        lines.append(f"- {question}")

    return "\n".join(lines)
