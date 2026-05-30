"""Plan §8 content convention: answer keys / rubrics / explanations must be
labeled distinctly from student-visible text in the assembled markdown, so a
downstream consumer can split teacher content from student content.

These tests pin that the `_synth_md_for_structured` renderer routes every
answer-revealing element through a machine-detectable TEACHER NOTE marker and
never leaks the answer into a student-visible line.
"""

from app.schemas.memory_check import MemoryCheckItem, MemoryCheckPack
from app.schemas.practice_games import ErrorBlock, ErrorDetection
from app.services.pipeline import _TEACHER_MARK, _synth_md_for_structured


def _split(md: str) -> tuple[str, str]:
    """(student_text, teacher_text) — partition lines by the teacher marker."""
    student = "\n".join(l for l in md.splitlines() if _TEACHER_MARK not in l)
    teacher = "\n".join(l for l in md.splitlines() if _TEACHER_MARK in l)
    return student, teacher


def test_memory_check_answer_only_in_teacher_notes():
    pack = MemoryCheckPack(
        items=[
            MemoryCheckItem(
                flashcard_id="card_1",
                prompt="Which organelle makes ATP?",
                kind="multiple_choice",
                options=[
                    {"text": "Nucleus", "is_correct": False, "reason": "stores DNA, not energy"},
                    {"text": "Mitochondrion", "is_correct": True},
                    {"text": "Ribosome", "is_correct": False, "reason": "builds proteins"},
                    {"text": "Vacuole", "is_correct": False, "reason": "storage, not energy"},
                ],
                explanation="Mitochondrion is the powerhouse because reasons.",
            )
        ]
    )
    md = _synth_md_for_structured("memory-check", pack)
    student, teacher = _split(md)

    assert _TEACHER_MARK in md, "labeling marker must be present"
    # The correct-answer flag must NOT appear in student-visible lines.
    assert "✓" not in student
    # The explanation is teacher-facing, not shown to the student inline.
    assert "because reasons" not in student.lower()
    assert "because reasons" in teacher.lower()
    # The correct option is named in a teacher note.
    assert "Mitochondrion" in teacher


def test_error_detection_answer_only_in_teacher_notes():
    ed = ErrorDetection(
        pattern="math_equation",
        concept_ids=["c1"],
        blocks=[
            ErrorBlock(id="b1", content="2+2=4"),
            ErrorBlock(id="b2", content="3+3=7", is_error=True),
            ErrorBlock(id="b3", content="4+4=8"),
        ],
        correct_answer_for_error_block="3+3=6",
        hint="re-check one of the sums",
        why_prompt="Why is that step wrong?",
        correct_feedback="ok",
        wrong_correction_feedback="no",
        reveal_feedback="rv",
    )
    md = _synth_md_for_structured("practice-error-detection", ed)
    student, teacher = _split(md)

    assert _TEACHER_MARK in md
    # The legacy inline "broken" flag must be gone entirely.
    assert "← broken" not in md
    # The correction must be teacher-only.
    assert "3+3=6" in teacher
    assert "3+3=6" not in student
