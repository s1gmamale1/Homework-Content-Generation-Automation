import pytest
from app.services import platform_payload as pp


def test_load_subject_map_rejects_non_positive_and_malformed():
    assert pp.load_subject_map('{"history": 7}') == {"history": 7}
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": 0}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": -3}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": "7"}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": true}')   # bool is not a valid id
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map("not json")


def _job():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "book_id": "22222222-2222-2222-2222-222222222222",
        "subject": "history",
        "grade": "8",
        "output_language": "ru",
    }


def _phase(**kw):
    base = {
        "phase_name": "practice-rlc",
        "output_md": "# x",
        "content_json": {"a": 1},
        "content_schema_version": "rlc_config@1",
        "authoring_mode": "structured",
        "judge_status": "ok",
        "status": "done",
    }
    base.update(kw)
    return base


def test_build_payload_shape_and_string_uuids():
    """Field-for-field against HomeworkImportIngestSerializer.

    `phases` MUST live under `payload` (the view reads `payload.get("phases")`);
    at the request top level DRF drops it and 400s on the missing `payload`.
    `grade` MUST be an int — IntegerField(min_value=1, max_value=11).
    """
    out = pp.build_ingest_payload(
        job=_job(), phases=[_phase()], subject_map={"history": 7}
    )
    assert out["source"] == "hcg"
    assert out["source_ref"] == "22222222-2222-2222-2222-222222222222"
    assert out["external_key"] == "11111111-1111-1111-1111-111111111111"
    assert isinstance(out["source_ref"], str) and isinstance(out["external_key"], str)
    assert out["language"] == "ru"
    assert out["subject_id"] == 7
    assert out["grade"] == 8 and isinstance(out["grade"], int)
    assert "phases" not in out                       # NOT at the top level
    assert isinstance(out["payload"], dict) and out["payload"]  # non-empty dict
    assert isinstance(out["payload"]["phases"], list)           # LIST, not dict
    assert out["payload"]["phases"][0]["phase_name"] == "practice-rlc"
    assert set(out) == {
        "source", "source_ref", "language", "subject_id",
        "grade", "external_key", "payload",
    }


@pytest.mark.parametrize("raw,expected", [(8, 8), ("8", 8), (" 11 ", 11), (1, 1)])
def test_grade_is_coerced_to_int(raw, expected):
    job = _job()
    job["grade"] = raw
    out = pp.build_ingest_payload(job=job, phases=[_phase()], subject_map={"history": 7})
    assert out["grade"] == expected and isinstance(out["grade"], int)


@pytest.mark.parametrize("bad", [0, 12, -1, "", "eight", None, True, 8.0, "8.0"])
def test_grade_out_of_range_or_non_numeric_is_a_hard_error(bad):
    job = _job()
    job["grade"] = bad
    with pytest.raises(pp.PayloadError):
        pp.build_ingest_payload(job=job, phases=[_phase()], subject_map={"history": 7})


def test_structured_pairs_dedupes_and_ignores_markdown():
    out = pp.build_ingest_payload(
        job=_job(),
        phases=[
            _phase(),
            _phase(phase_name="practice-rlc"),          # duplicate pair
            _phase(phase_name="practice-sentence",
                   content_schema_version="sentence_fill_config@1"),
            _phase(phase_name="flashcards", authoring_mode="markdown_builtin",
                   content_schema_version=None),
        ],
        subject_map={"history": 7},
    )
    assert pp.structured_pairs(out) == [
        ("practice-rlc", "rlc_config@1"),
        ("practice-sentence", "sentence_fill_config@1"),
    ]


def test_structured_pairs_empty_for_markdown_only_payload():
    out = pp.build_ingest_payload(
        job=_job(),
        phases=[_phase(authoring_mode="markdown_builtin", content_schema_version=None)],
        subject_map={"history": 7},
    )
    assert pp.structured_pairs(out) == []


def test_build_payload_missing_subject_mapping_is_hard_error():
    with pytest.raises(pp.SubjectMapError):
        pp.build_ingest_payload(job=_job(), phases=[_phase()], subject_map={"biology": 3})


def test_build_payload_excludes_extract_and_non_done_and_empty():
    phases = [
        _phase(phase_name="extract"),
        _phase(phase_name="practice-sentence", status="failed"),
        _phase(phase_name="flashcards", output_md=""),
        _phase(),
    ]
    out = pp.build_ingest_payload(job=_job(), phases=phases, subject_map={"history": 7})
    assert [p["phase_name"] for p in out["payload"]["phases"]] == ["practice-rlc"]
