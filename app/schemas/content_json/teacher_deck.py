"""TeacherDeck — structured teacher lesson-plan deck (see task-4-brief.md).

Field KEYS are English; VALUES are generated in the book's language. Nested
Pydantic models (not raw dicts) so `model_json_schema()` is well-typed for the
prompt embed, and `model_validate_json()` catches malformed model output before
it reaches a teacher.

Deliberately does NOT use the shared `StrictModel` (`common.py`): that class
sets `strict=True`, which rejects well-formed-but-loosely-typed generated
output (`"grade": 11` for a str field, `"minutes": 3.0` for an int field).
`run_phase` only retries once on a schema-validation failure, so `strict=True`
here would turn harmless type looseness into avoidable generation failures.
`_TeacherDeckModel` below is this schema's own local base — `extra="forbid"`
(unknown keys still reject) but `strict=False` (standard lossless coercion).
Do not change the shared `StrictModel`/`common.py` — other schemas
(`RlcConfig`, `SentenceFillConfig`) depend on its `strict=True` behavior.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BADGE_VALUES = ("ekranga", "teacher_only", "none")
OPTION_LABELS = ("A", "B", "C", "D")


class _TeacherDeckModel(BaseModel):
    """Local base for every TeacherDeck model (see module docstring).

    `coerce_numbers_to_str=True` on top of `strict=False`: lax mode alone does
    NOT coerce int/float into a declared str field (e.g. `"grade": 11`) —
    that needs this flag explicitly.
    """

    model_config = ConfigDict(extra="forbid", strict=False, coerce_numbers_to_str=True)


class Meta(_TeacherDeckModel):
    subject_label: str = Field(min_length=1)
    grade: str = Field(min_length=1)
    topic_number: int = Field(gt=0)
    topic_title: str = Field(min_length=1)
    duration_min: int = Field(gt=0)
    lesson_type: str = Field(min_length=1)
    method: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    video_ref: str | None = None


class Passport(_TeacherDeckModel):
    fan_sinf: str = Field(min_length=1)
    mavzu: str = Field(min_length=1)
    dars_turi: str = Field(min_length=1)
    metod: str = Field(min_length=1)
    kerakli_vosita: str = Field(min_length=1)
    baholash: str = Field(min_length=1)


class Objectives(_TeacherDeckModel):
    bilib_oladi: str = Field(min_length=1)
    qila_oladi: str = Field(min_length=1)
    tushunadi: str = Field(min_length=1)


class CoreIdea(_TeacherDeckModel):
    statement: str = Field(min_length=1)
    elaboration: str = Field(min_length=1)


class LessonMapItem(_TeacherDeckModel):
    index: int = Field(gt=0)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    minutes: int = Field(gt=0)


class Point(_TeacherDeckModel):
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class Stage(_TeacherDeckModel):
    index: int = Field(gt=0)
    title: str = Field(min_length=1)
    minutes: int = Field(gt=0)
    badge: Literal[BADGE_VALUES]  # type: ignore[valid-type]
    points: list[Point] = Field(default_factory=list)
    teacher_action: str = Field(min_length=1)
    student_action: str = Field(min_length=1)
    screen_text: str | None = None


class QuizOption(_TeacherDeckModel):
    label: Literal[OPTION_LABELS]  # type: ignore[valid-type]
    text: str = Field(min_length=1)


class QuizItem(_TeacherDeckModel):
    number: int = Field(gt=0)
    question: str = Field(min_length=1)
    options: list[QuizOption]
    correct_label: Literal[OPTION_LABELS]  # type: ignore[valid-type]
    hint: str = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self):
        if len(self.options) != 4:
            raise ValueError("quiz item must have exactly 4 options")
        labels = [o.label for o in self.options]
        if len(set(labels)) != 4:
            raise ValueError("quiz item option labels must be unique (A-D)")
        # No separate "correct_label must be among options" check: with exactly
        # 4 uniquely-labeled options and both `label` and `correct_label` typed
        # as Literal["A","B","C","D"], the option label set is structurally
        # always {A,B,C,D} — correct_label is always a member by construction.
        return self


class AnswerKeyItem(_TeacherDeckModel):
    number: int = Field(gt=0)
    correct_label: Literal[OPTION_LABELS]  # type: ignore[valid-type]
    explanation: str = Field(min_length=1)


class PairWorkTask(_TeacherDeckModel):
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)


class PairWork(_TeacherDeckModel):
    intro: str = Field(min_length=1)
    tasks: list[PairWorkTask] = Field(min_length=1)


class Conclusion(_TeacherDeckModel):
    questions: list[str] = Field(min_length=1)


class RubricComponent(_TeacherDeckModel):
    points: int = Field(gt=0)
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class RubricBand(_TeacherDeckModel):
    range: str = Field(min_length=1)
    grade: str = Field(min_length=1)


class Rubric(_TeacherDeckModel):
    components: list[RubricComponent] = Field(min_length=1)
    total: int = Field(gt=0)
    bands: list[RubricBand] = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self):
        component_sum = sum(c.points for c in self.components)
        if component_sum != self.total:
            raise ValueError(
                f"rubric component points must sum to total ({self.total}), got {component_sum}"
            )
        return self


class TeacherDeck(_TeacherDeckModel):
    # ClassVar, NOT a field — see rlc.py / sentence_fill.py: the version travels
    # in the envelope as content_schema_version, not inside content_json.
    SCHEMA_VERSION: ClassVar[str] = "teacher_deck@1"

    meta: Meta
    passport: Passport
    objectives: Objectives
    core_idea: CoreIdea
    lesson_map: list[LessonMapItem] = Field(min_length=1)
    stages: list[Stage] = Field(min_length=1)
    quiz: list[QuizItem] = Field(min_length=1)
    answer_key: list[AnswerKeyItem] = Field(min_length=1)
    pair_work: PairWork
    conclusion: Conclusion
    rubric: Rubric

    @model_validator(mode="after")
    def _lesson_map_minutes_match_duration(self):
        total = sum(item.minutes for item in self.lesson_map)
        if total != self.meta.duration_min:
            raise ValueError(
                "lesson_map minutes must sum to meta.duration_min "
                f"({self.meta.duration_min}), got {total}"
            )
        return self

    @model_validator(mode="after")
    def _answer_key_matches_quiz(self):
        if len(self.answer_key) != len(self.quiz):
            raise ValueError(
                "answer_key must have exactly as many entries as quiz "
                f"({len(self.quiz)}), got {len(self.answer_key)}"
            )
        quiz_numbers = {q.number for q in self.quiz}
        answer_numbers = {a.number for a in self.answer_key}
        if quiz_numbers != answer_numbers:
            raise ValueError(
                "answer_key numbers must match quiz numbers exactly "
                f"(quiz={sorted(quiz_numbers)}, answer_key={sorted(answer_numbers)})"
            )
        quiz_by_number = {q.number: q for q in self.quiz}
        for a in self.answer_key:
            expected = quiz_by_number[a.number].correct_label
            if a.correct_label != expected:
                raise ValueError(
                    f"answer_key[{a.number}].correct_label ({a.correct_label}) must match "
                    f"quiz[{a.number}].correct_label ({expected})"
                )
        return self
