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
  - each stage's points (title/detail) and screen_text
  - each quiz question + option texts + correct_label
  - each answer_key correct_label + explanation

Excluded (process/teaching chrome, not claims about the world):
  - meta / passport (grade, timings, method/materials labels)
  - lesson_map (minutes)
  - stage.index / .minutes / .badge / .teacher_action / .student_action
  - pair_work / conclusion (practice prompts, not source facts)
  - rubric (scoring chrome)

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

    return "\n".join(lines)
