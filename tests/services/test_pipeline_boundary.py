from app.services.pipeline import _inject_lesson_boundary, _inject_grade


def test_boundary_note_names_next_lesson_and_forbids_its_concepts():
    out = _inject_lesson_boundary("EXTRACT BODY", "Pifagor teoremasiga teskari")
    assert "Pifagor teoremasiga teskari" in out
    assert "EXTRACT BODY" in out
    low = out.lower()
    assert "next lesson" in low
    # must forbid reaching into the next lesson's natural completions
    assert "converse" in low
    assert "criteria" in low
    assert "generaliz" in low  # generalization / generalisation


def test_boundary_note_is_noop_without_a_successor():
    assert _inject_lesson_boundary("EXTRACT BODY", None) == "EXTRACT BODY"
    assert _inject_lesson_boundary("EXTRACT BODY", "") == "EXTRACT BODY"


def test_boundary_note_is_noop_when_context_missing():
    assert _inject_lesson_boundary(None, "Next Lesson") is None


def test_boundary_composes_after_grade_injection():
    ctx = _inject_grade("EXTRACT BODY", "8")
    ctx = _inject_lesson_boundary(ctx, "Next Lesson")
    assert "Student grade level: 8" in ctx
    assert "Next Lesson" in ctx
    assert "EXTRACT BODY" in ctx
