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


def render_teacher_deck_markdown(deck: TeacherDeck) -> str:
    """Full bilingual (uz/English) lesson-plan markdown for `deck`.

    The ONE markdown source both the readable Notion page and the PDF render
    from — see the module scene in the fidelity docstring above. Unlike
    `serialize_deck_for_fidelity` (fact-bearing content only, for the judge),
    this renders EVERY section including structural/teaching chrome (meta,
    passport, lesson map, stage timings/badges, rubric) — nothing here is
    dropped for a human reader.

    Pure function — no I/O, deterministic.
    """
    lines: list[str] = []

    lines.append(f"# {deck.meta.topic_number}. {deck.meta.topic_title}")
    lines.append(
        f"{deck.meta.subject_label} · {deck.meta.grade} · {deck.meta.lesson_type} · "
        f"{deck.meta.duration_min} daqiqa"
    )
    lines.append("")

    lines.append("## Pasport / Passport")
    lines.append(f"- **Fan/sinf:** {deck.passport.fan_sinf}")
    lines.append(f"- **Mavzu:** {deck.passport.mavzu}")
    lines.append(f"- **Dars turi:** {deck.passport.dars_turi}")
    lines.append(f"- **Metod:** {deck.passport.metod}")
    lines.append(f"- **Kerakli vosita:** {deck.passport.kerakli_vosita}")
    lines.append(f"- **Baholash:** {deck.passport.baholash}")
    lines.append(f"- **Usullar / Method:** {', '.join(deck.meta.method)}")
    lines.append(f"- **Materiallar / Materials:** {', '.join(deck.meta.materials)}")
    if deck.meta.video_ref:
        lines.append(f"- **Video:** {deck.meta.video_ref}")
    lines.append("")

    lines.append("## Maqsad / Objectives")
    lines.append(f"- **Bilib oladi:** {deck.objectives.bilib_oladi}")
    lines.append(f"- **Qila oladi:** {deck.objectives.qila_oladi}")
    lines.append(f"- **Tushunadi:** {deck.objectives.tushunadi}")
    lines.append("")

    lines.append("## Asosiy g'oya / Core idea")
    lines.append(deck.core_idea.statement)
    lines.append("")
    lines.append(deck.core_idea.elaboration)
    lines.append("")

    lines.append("## Dars xaritasi / Lesson map")
    for item in sorted(deck.lesson_map, key=lambda i: i.index):
        lines.append(
            f"- {item.index}. **{item.title}** — {item.minutes} daqiqa: {item.description}"
        )
    lines.append("")

    lines.append("## Bosqichlar / Stages")
    for stage in sorted(deck.stages, key=lambda s: s.index):
        lines.append(f"### {stage.index}-bosqich · {stage.title} ({stage.minutes} daqiqa)")
        lines.append(f"- **O'qituvchi:** {stage.teacher_action}")
        lines.append(f"- **O'quvchi:** {stage.student_action}")
        for point in stage.points:
            lines.append(f"- {point.title}: {point.detail}")
        if stage.screen_text:
            lines.append("")
            lines.append(f"**Ekran:** {stage.screen_text}")
    lines.append("")

    lines.append("## Test / Quiz")
    for q in deck.quiz:
        lines.append(f"**{q.number}. {q.question}**")
        for opt in q.options:
            lines.append(f"- {opt.label}) {opt.text}")
        lines.append(f"_To'g'ri javob: {q.correct_label} · Yordam: {q.hint}_")
    lines.append("")

    lines.append("## Javoblar kaliti / Answer key")
    for a in deck.answer_key:
        lines.append(f"- **{a.number}. ({a.correct_label})** {a.explanation}")
    lines.append("")

    lines.append("## Juftlikda ish / Pair work")
    lines.append(deck.pair_work.intro)
    for task in deck.pair_work.tasks:
        lines.append(f"- **{task.title}:** {task.prompt}")
    lines.append("")

    lines.append("## Yakun / Conclusion")
    for question in deck.conclusion.questions:
        lines.append(f"- {question}")
    lines.append("")

    lines.append("## Baholash mezoni / Rubric")
    for c in deck.rubric.components:
        lines.append(f"- **{c.title}** — {c.points} ball: {c.detail}")
    lines.append("")
    lines.append(f"**Jami / Total: {deck.rubric.total} ball**")
    for band in deck.rubric.bands:
        lines.append(f"- {band.range}: {band.grade}")

    return "\n".join(lines)
