def test_extract_prompt_hash_is_v3():
    import inspect
    from app.services import pipeline
    src = inspect.getsource(pipeline)
    assert '"builtin:extract:v3"' in src
    assert '"builtin:extract:v2"' not in src


from app.services.pipeline import _coverage_warnings_for_job


def test_coverage_warnings_helper_flags_and_ignores_extract():
    rows = [
        {"phase_name": "extract", "output_md": "## Worked-example types\n- izotop massa hisoblash\n"},
        {"phase_name": "flashcards", "output_md": "Davriy qonun. Elementlar."},
        {"phase_name": "boss-arena", "output_md": "Savollar."},
    ]
    warns = _coverage_warnings_for_job(rows)
    assert warns and warns[0].startswith("lint:coverage_thin")

def test_coverage_warnings_empty_when_covered_or_no_extract():
    assert _coverage_warnings_for_job([{"phase_name": "flashcards", "output_md": "x"}]) == []
    rows = [
        {"phase_name": "extract", "output_md": "## Key facts\n- davriy qonun\n"},
        {"phase_name": "flashcards", "output_md": "Davriy qonun elementlarni tartiblaydi."},
    ]
    assert _coverage_warnings_for_job(rows) == []
