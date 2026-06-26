from app.services.pipeline import _inject_grade


def test_inject_grade_prepends_when_present():
    out = _inject_grade("LESSON BODY", "7")
    assert out.startswith("Student grade level: 7")
    assert "LESSON BODY" in out


def test_inject_grade_noop_when_grade_missing():
    assert _inject_grade("LESSON BODY", None) == "LESSON BODY"
    assert _inject_grade("LESSON BODY", "") == "LESSON BODY"


def test_inject_grade_noop_when_context_none():
    assert _inject_grade(None, "7") is None
